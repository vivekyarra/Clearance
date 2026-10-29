"""J2 geometry: floor fit, TSDF fusion into a voxel grid, occupancy and threshold extraction
. Pure numpy + numba; Open3D is not required.

Voxels: 2.5 cm in x/y (= occupancy cells) and DZ in z, with layer edges at band_lo + k*DZ so the
height band [0.03, 2.0] starts exactly on a layer edge. TSDF update is ray-based: each depth pixel
updates the voxels within +-trunc of its measured surface (signed distance along the ray).
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numba
import numpy as np
from scipy import ndimage

from clearance_core.grid import FREE, OCC, UNKNOWN, Grid
from jobs.common.camera import backproject, make_T, rot_axis, pixel_rays_cam

RES = 0.025
DZ = 0.025
EXCLUDE_MIN_HITS = 10
BAND = (0.03, 2.0)


# ---------------------------------------------------------------------------------------------
def fit_floor_plane(points: np.ndarray, *, dist_thresh: float = 0.02, iters: int = 400, max_tilt_deg: float = 15.0,
                    rng: np.random.Generator | None = None):
    """RANSAC plane on the lowest points. Returns (n[3] unit, d) with n.z > 0 and n.p + d = 0, and the
    inlier fraction among candidate points."""
    rng = rng or np.random.default_rng(0)
    z = points[:, 2]
    zlo = np.percentile(z, 2)
    cand = points[z < zlo + 0.35]
    if len(cand) < 50:
        raise ValueError("not enough low points for a floor fit")
    if len(cand) > 60000:
        cand = cand[rng.choice(len(cand), 60000, replace=False)]
    cos_max = np.cos(np.deg2rad(max_tilt_deg))
    cos_any = np.cos(np.deg2rad(45.0))
    best, best_cnt = None, 0
    any_n, any_cnt = None, 0  # best plane up to 45 degrees: detects a floor tilted beyond max_tilt_deg
    idx = rng.integers(0, len(cand), size=(iters, 3))
    for i, j, k in idx:
        p0, p1, p2 = cand[i], cand[j], cand[k]
        n = np.cross(p1 - p0, p2 - p0)
        nn = np.linalg.norm(n)
        if nn < 1e-9:
            continue
        n = n / nn
        if n[2] < 0:
            n = -n
        if n[2] < cos_any:
            continue
        d = -n @ p0
        cnt = int((np.abs(cand @ n + d) < dist_thresh).sum())
        if cnt > any_cnt:
            any_n, any_cnt = n, cnt
        if n[2] >= cos_max and cnt > best_cnt:
            best, best_cnt = (n, d), cnt
    if any_n is not None and any_n[2] < cos_max and any_cnt > 1.5 * best_cnt:
        raise ValueError(f"the floor appears tilted by {np.degrees(np.arccos(any_n[2])):.0f} degrees, beyond the "
                         f"{max_tilt_deg:.0f} degrees the floor fit accepts (gravity not aligned with world Z?)")
    if best is None:
        raise ValueError("floor RANSAC found no near-horizontal plane")
    n, d = best
    for _ in range(3):
        m = np.abs(cand @ n + d) < dist_thresh
        c = cand[m].mean(axis=0)
        _, _, vt = np.linalg.svd(cand[m] - c, full_matrices=False)
        n = vt[2] if vt[2][2] > 0 else -vt[2]
        d = -n @ c
    if n[2] < cos_max:
        raise ValueError(f"floor fit converged to a plane tilted {np.degrees(np.arccos(n[2])):.0f} degrees "
                         f"(max {max_tilt_deg:.0f})")
    return n, float(d), best_cnt / len(cand)


def floor_alignment(n: np.ndarray, d: float) -> np.ndarray:
    """Rigid 4x4 mapping the plane n.p + d = 0 onto Z = 0 with the minimal rotation."""
    z = np.array([0, 0, 1.0])
    axis = np.cross(n, z)
    ang = np.arccos(np.clip(n @ z, -1, 1))
    R = rot_axis(axis, ang) if np.linalg.norm(axis) > 1e-12 else np.eye(3)
    p0 = -d * n  # point on the plane closest to the origin
    T = make_T(R, -R @ p0)
    return T


# ---------------------------------------------------------------------------------------------
@dataclass
class VoxelGrid:
    origin: np.ndarray  # world xyz of voxel (0,0,0) corner
    shape: tuple        # nx, ny, nz
    res: float
    dz: float
    tsdf: np.ndarray    # float32 sum of weighted tsdf
    weight: np.ndarray  # float32
    nframes: np.ndarray  # uint16 distinct frames that touched the voxel
    last: np.ndarray    # int32 last frame id
    trunc: float
    seen_through: np.ndarray = None  # uint16 frames whose rays passed through the voxel (free space)
    last_free: np.ndarray = None
    free_after: np.ndarray = None    # uint16 frames that looked through the voxel since it was last seen
                                     # behind a surface (a person who walked away leaves free_after > 0)

    @classmethod
    def create(cls, xy_min, xy_max, z_min=-0.20, z_max=2.10, res=RES, dz=DZ, trunc=0.06, band_lo=BAND[0]):
        k0 = int(np.floor((z_min - band_lo) / dz))
        z0 = band_lo + k0 * dz
        nz = int(np.ceil((z_max - z0) / dz))
        x0 = np.floor(xy_min[0] / res) * res
        y0 = np.floor(xy_min[1] / res) * res
        nx = int(np.ceil((xy_max[0] - x0) / res))
        ny = int(np.ceil((xy_max[1] - y0) / res))
        shp = (nx, ny, nz)
        return cls(np.array([x0, y0, z0]), shp, res, dz, np.zeros(shp, np.float32), np.zeros(shp, np.float32),
                   np.zeros(shp, np.uint16), np.full(shp, -1, np.int32), trunc,
                   np.zeros(shp, np.uint16), np.full(shp, -1, np.int32), np.zeros(shp, np.uint16))

    BYTES_PER_VOXEL = 4 + 4 + 2 + 4 + 2 + 4 + 2

    @staticmethod
    def estimate_bytes(xy_min, xy_max, z_min=-0.20, z_max=2.10, res=RES, dz=DZ, band_lo=BAND[0]) -> int:
        """Memory of VoxelGrid.create for these bounds, plus the temporaries of extract_occupancy."""
        k0 = int(np.floor((z_min - band_lo) / dz))
        nz = int(np.ceil((z_max - (band_lo + k0 * dz)) / dz))
        nx = int(np.ceil((xy_max[0] - np.floor(xy_min[0] / res) * res) / res))
        ny = int(np.ceil((xy_max[1] - np.floor(xy_min[1] / res) * res) / res))
        return int(nx * ny * nz * (VoxelGrid.BYTES_PER_VOXEL + 8))

    def z_centers(self):
        return self.origin[2] + (np.arange(self.shape[2]) + 0.5) * self.dz

    def integrate(self, depth: np.ndarray, K, T_world_cam: np.ndarray, frame_id: int, step: int = 1,
                  weight_scale: float = 1.0, carve_step: int = 3, exclude: tuple | None = None):
        """exclude: optional (core, region) bool voxel masks from transient_exclusion. When at least
        EXCLUDE_MIN_HITS of this frame's surface points fall in core (the frame saw the transient
        obstacle), its samples whose surface point falls in region are not fused."""
        h, w = depth.shape
        dirs, uu, vv = pixel_rays_cam(K, w, h, step)
        d = depth[vv, uu].astype(np.float64)
        ok = d > 0
        R, t = T_world_cam[:3, :3], T_world_cam[:3, 3]
        t = np.ascontiguousarray(t)
        dw = (dirs[ok] @ R.T)
        dk = d[ok]
        if exclude is not None and dk.size:
            pts = t + dw * dk[:, None]
            if self._lookup(exclude[0], pts).sum() >= EXCLUDE_MIN_HITS:
                keep = ~self._lookup(exclude[1], pts)
                dw, dk = dw[keep], dk[keep]
        _integrate_rays(self.tsdf, self.weight, self.nframes, self.last, self.free_after, self.origin, self.res,
                        self.dz, t, np.ascontiguousarray(dw), dk, self.trunc, frame_id, weight_scale)
        if carve_step:
            dirs, uu, vv = pixel_rays_cam(K, w, h, carve_step)
            d = depth[vv, uu].astype(np.float64)
            ok = d > 0
            _carve_rays(self.seen_through, self.last_free, self.free_after, self.origin, self.res, self.dz, t,
                        np.ascontiguousarray(dirs[ok] @ R.T), d[ok], self.trunc, frame_id)

    def _lookup(self, mask: np.ndarray, pts: np.ndarray) -> np.ndarray:
        idx = np.floor((pts - self.origin) / np.array([self.res, self.res, self.dz])).astype(np.int64)
        inside = np.all((idx >= 0) & (idx < np.array(self.shape)), axis=1)
        out = np.zeros(len(pts), bool)
        i = idx[inside]
        out[inside] = mask[i[:, 0], i[:, 1], i[:, 2]]
        return out

    def mean_tsdf(self):
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(self.weight > 0, self.tsdf / np.maximum(self.weight, 1e-9), np.nan)


@numba.njit(cache=True)
def _integrate_rays(tsdf, weight, nfr, last, free_after, origin, res, dz, cam, dirs, depth, trunc, fid, wscale):
    nx, ny, nz = tsdf.shape
    step = min(res, dz) * 0.5
    for i in range(dirs.shape[0]):
        dx, dy, dzr = dirs[i, 0], dirs[i, 1], dirs[i, 2]
        L = np.sqrt(dx * dx + dy * dy + dzr * dzr)  # metres per unit depth
        dmeas = depth[i]
        wgt = wscale / max(dmeas * dmeas, 0.25)
        dd0 = dmeas - trunc / L
        dd1 = dmeas + trunc / L
        n = int((dd1 - dd0) * L / step) + 1
        for k in range(n):
            dd = dd0 + k * step / L
            if dd <= 0:
                continue
            x = cam[0] + dx * dd
            y = cam[1] + dy * dd
            z = cam[2] + dzr * dd
            ix = int(np.floor((x - origin[0]) / res))
            iy = int(np.floor((y - origin[1]) / res))
            iz = int(np.floor((z - origin[2]) / dz))
            if ix < 0 or iy < 0 or iz < 0 or ix >= nx or iy >= ny or iz >= nz:
                continue
            sdf = (dmeas - dd) * L
            v = sdf / trunc
            if v > 1.0:
                v = 1.0
            elif v < -1.0:
                v = -1.0
            tsdf[ix, iy, iz] += wgt * v
            weight[ix, iy, iz] += wgt
            if v < 0:
                free_after[ix, iy, iz] = 0
            if last[ix, iy, iz] != fid:
                last[ix, iy, iz] = fid
                if nfr[ix, iy, iz] < 65535:
                    nfr[ix, iy, iz] += 1


@numba.njit(cache=True)
def _carve_rays(seen, lastf, free_after, origin, res, dz, cam, dirs, depth, trunc, fid):
    nx, ny, nz = seen.shape
    step = min(res, dz) * 0.7
    for i in range(dirs.shape[0]):
        dx, dy, dzr = dirs[i, 0], dirs[i, 1], dirs[i, 2]
        L = np.sqrt(dx * dx + dy * dy + dzr * dzr)
        dend = depth[i] - 2.0 * trunc / L
        n = int(dend * L / step)
        for k in range(1, n):
            dd = k * step / L
            ix = int(np.floor((cam[0] + dx * dd - origin[0]) / res))
            iy = int(np.floor((cam[1] + dy * dd - origin[1]) / res))
            iz = int(np.floor((cam[2] + dzr * dd - origin[2]) / dz))
            if ix < 0 or iy < 0 or iz < 0 or ix >= nx or iy >= ny or iz >= nz:
                continue
            if lastf[ix, iy, iz] != fid:
                lastf[ix, iy, iz] = fid
                if seen[ix, iy, iz] < 65535:
                    seen[ix, iy, iz] += 1
                if free_after[ix, iy, iz] < 65535:
                    free_after[ix, iy, iz] += 1


# ---------------------------------------------------------------------------------------------
def transient_voxels(vg: VoxelGrid, *, band=BAND, min_frames: int = 2, occ_tsdf_max: float = -0.1,
                     transient_frames: int = 3, transient_ratio: float = 0.5) -> np.ndarray:
    """Band voxels that were seen behind a surface and later looked through by at least
    max(transient_frames, transient_ratio * frames that saw them) frames: something stood there and left."""
    if vg.free_after is None or not transient_frames:
        return np.zeros(vg.shape, bool)
    t = vg.mean_tsdf()
    kb = (vg.z_centers() > band[0]) & (vg.z_centers() < band[1])
    gone = vg.free_after.astype(np.float32) >= np.maximum(transient_frames, transient_ratio * vg.nframes)
    return gone & (vg.nframes >= min_frames) & (t < occ_tsdf_max) & kb[None, None, :]


def transient_exclusion(gone: np.ndarray, min_voxels: int = 800) -> tuple | None:
    """Voxel mask for a second fusion pass that leaves out measurements of transient obstacles: transient
    voxels grown by one cell sideways and extended down their column to the floor (feet, wheels, the
    stale surface a person leaves just above the floor). Returns (core, region) or None when there is
    nothing to exclude. Only components of at least min_voxels count (a standing person leaves several
    thousand; pose and depth noise in static rooms leaves components of tens to a few hundred, which the
    occupancy rule already drops without a second pass)."""
    lab, n = ndimage.label(gone, structure=np.ones((3, 3, 3)))
    if not n:
        return None
    sizes = ndimage.sum(gone, lab, range(1, n + 1))
    keep = np.isin(lab, np.flatnonzero(sizes >= min_voxels) + 1)
    if not keep.any():
        return None
    region = ndimage.binary_dilation(keep, np.ones((3, 3, 1), bool))
    return keep, np.flip(np.logical_or.accumulate(np.flip(region, axis=2), axis=2), axis=2)


def floor_height_map(vg: VoxelGrid, min_frames: int = 2, z_lo: float = -0.16, z_hi: float = 0.10):
    """Per-column height of the lowest upward-facing zero crossing near the floor (NaN if none)."""
    t = vg.mean_tsdf()
    zc = vg.z_centers()
    ks = np.flatnonzero((zc >= z_lo) & (zc <= z_hi))
    good = (vg.nframes >= min_frames) & np.isfinite(t)
    H = np.full(vg.shape[:2], np.nan)
    done = np.zeros(vg.shape[:2], bool)
    for a, b in zip(ks[:-1], ks[1:]):
        ta, tb = t[:, :, a], t[:, :, b]
        m = good[:, :, a] & good[:, :, b] & (ta <= 0) & (tb > 0) & ~done
        frac = ta[m] / (ta[m] - tb[m])
        H[m] = zc[a] + frac * (zc[b] - zc[a])
        done |= m
    return H


def extract_occupancy(vg: VoxelGrid, *, band=BAND, min_frames: int = 2, min_occ_voxels: int = 3, free_ratio: float = 1.0,
                      occ_tsdf_max: float = -0.1, transient_frames: int = 3, transient_ratio: float = 0.5,
                      speckle_cells: int = 3, hole_cells: int = 6):
    """Occupied / free / unknown per cell:
    occupied = at least min_occ_voxels voxels of the column's height band, each observed by >= min_frames
    frames, lie behind a surface (mean tsdf < occ_tsdf_max, in units of the truncation distance) and
    were looked through by fewer frames than saw a surface there (free_ratio), and were not looked through
    by at least max(transient_frames, transient_ratio * frames that saw them) frames after they were last
    seen behind a surface (an obstacle that left, e.g. a person walking through the scan);
    free = floor surface observed and nothing in the band; unknown otherwise.
    Cleanup: drop occupied specks, close small unknown holes in free space, and treat unknown
    pockets enclosed by obstacles as occupied."""
    t = vg.mean_tsdf()
    zc = vg.z_centers()
    kb = (zc > band[0]) & (zc < band[1])
    seen = vg.nframes >= min_frames
    occ_v = seen & (t < occ_tsdf_max) & kb[None, None, :]
    if vg.seen_through is not None:
        occ_v &= vg.seen_through.astype(np.float32) < free_ratio * vg.nframes
    occ_v &= ~transient_voxels(vg, band=band, min_frames=min_frames, occ_tsdf_max=occ_tsdf_max,
                               transient_frames=transient_frames, transient_ratio=transient_ratio)
    occ = occ_v.sum(axis=2) >= min_occ_voxels
    H = floor_height_map(vg, min_frames)
    floor_seen = np.isfinite(H)
    free = floor_seen & ~occ

    # numpy arrays are (ix, iy); Grid rows go north->south
    lab = np.full(occ.shape, UNKNOWN, np.uint8)
    lab[free] = FREE
    lab[occ] = OCC
    data = np.flipud(lab.T).copy()
    data = cleanup(data, speckle_cells, hole_cells)
    g = Grid(data, vg.res, (float(vg.origin[0]), float(vg.origin[1])))
    return g, np.flipud(H.T).copy()


def cleanup(data: np.ndarray, speckle_cells: int = 3, hole_cells: int = 6) -> np.ndarray:
    out = data.copy()
    occ = out == OCC
    lab, n = ndimage.label(occ, structure=np.ones((3, 3)))
    if n:
        sizes = ndimage.sum(occ, lab, range(1, n + 1))
        small = np.isin(lab, np.flatnonzero(sizes < speckle_cells) + 1)
        nb_free = ndimage.binary_dilation(out == FREE, np.ones((3, 3)))
        out[small & nb_free] = FREE
    unk = out == UNKNOWN
    lab, n = ndimage.label(unk)
    if n:
        sizes = ndimage.sum(unk, lab, range(1, n + 1))
        free_d = ndimage.binary_dilation(out == FREE)
        occ_d = ndimage.binary_dilation(out == OCC)
        h, w = out.shape
        border = np.zeros_like(unk)
        border[0], border[-1], border[:, 0], border[:, -1] = True, True, True, True
        touches_free = ndimage.maximum(free_d, lab, range(1, n + 1)) > 0
        touches_occ = ndimage.maximum(occ_d, lab, range(1, n + 1)) > 0
        touches_border = ndimage.maximum(border, lab, range(1, n + 1)) > 0
        for i in range(n):
            comp = lab == i + 1
            if touches_border[i]:
                continue
            if not touches_free[i] and touches_occ[i]:
                out[comp] = OCC  # pocket inside an obstacle
            elif sizes[i] <= hole_cells and not touches_occ[i]:
                out[comp] = FREE  # small unobserved spot in open floor
    return out


# ---------------------------------------------------------------------------------------------
def detect_thresholds(H: np.ndarray, grid: Grid, *, min_len_m: float = 0.4, max_width_m: float = 0.3,
                      min_step_m: float = 0.005, max_step_m: float = 0.06):
    """Step candidates in the floor height map (rows = grid rows). Returns (list of dict, noise_m).
    noise_m = robust std (1.4826 * MAD) of per-cell floor heights over free floor; a candidate whose
    |height| < 2 * noise_m gets status flagged_manual."""
    fl = np.isfinite(H) & (grid.data == FREE)
    if fl.sum() < 100:
        return [], float("nan")
    base = ndimage.median_filter(np.where(np.isfinite(H), H, 0.0), size=25)
    resid = H - base
    vals = H[fl]
    med = float(np.median(vals))
    noise = float(1.4826 * np.median(np.abs(vals - med)))
    noise = max(noise, 1e-4)
    det = np.isfinite(H) & (grid.data != OCC) & (H - med > max(min_step_m, 1.5 * noise)) & (H - med < max_step_m)
    det = ndimage.binary_opening(det, np.ones((2, 2))) | (ndimage.binary_closing(det, np.ones((3, 3))) & det)
    lab, n = ndimage.label(det, structure=np.ones((3, 3)))
    out = []
    for i in range(1, n + 1):
        rr, cc = np.nonzero(lab == i)
        if len(rr) < 6:
            continue
        x, y = grid.cell_to_world(rr, cc)
        P = np.stack([x, y], axis=1)
        c = P.mean(axis=0)
        _, sv, vt = np.linalg.svd(P - c, full_matrices=False)
        ax = vt[0]
        proj = (P - c) @ ax
        perp = (P - c) @ vt[1]
        length = proj.max() - proj.min() + grid.res
        width = perp.max() - perp.min() + grid.res
        if length < min_len_m or width > max_width_m:
            continue
        ring = ndimage.binary_dilation(lab == i, np.ones((9, 9))) & ~ndimage.binary_dilation(lab == i, np.ones((3, 3)))
        ring &= fl
        ref = float(np.median(H[ring])) if ring.sum() > 5 else med
        h = float(np.median(H[rr, cc]) - ref)
        a = c + proj.min() * ax
        b = c + proj.max() * ax
        out.append({"segment": ((float(a[0]), float(a[1])), (float(b[0]), float(b[1]))), "height_m": round(h, 4),
                    "length_m": round(float(length), 3), "width_m": round(float(width), 3),
                    "status": "measured" if abs(h) >= 2 * noise else "flagged_manual"})
    return out, noise
