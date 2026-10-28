"""J1 + J2: Capture directory -> Twin with a measured occupancy grid (docs/CONTRACTS.md)."""
from __future__ import annotations

import os
import time

import cv2
import numpy as np

from scipy import ndimage

from clearance_core import grid as cgrid
from clearance_core import storage
from clearance_core.schemas import Accuracy, Floor, Threshold, Twin
from jobs.common.camera import backproject

from .depth import RelativeDepthBackend, depth_source_for
from .fusion import (BAND, VoxelGrid, detect_thresholds, extract_occupancy, fit_floor_plane, floor_alignment,
                     transient_exclusion, transient_voxels)
from .ingest import load_capture

PARAMS = {
    "T1_LIDAR": {"trunc": 0.06, "step": 1, "min_frames": 2, "max_depth_m": 5.0, "local_degree": 0,
                 "min_occ_voxels": 3, "occ_tsdf_max": -0.1},
    "T2_VIO_MONO": {"trunc": 0.10, "step": 1, "min_frames": 2, "max_depth_m": 3.0, "local_degree": 3,
                    "min_occ_voxels": 5, "occ_tsdf_max": -0.3},
}


MAX_VOXEL_BYTES = int(float(os.environ.get("CLEARANCE_RECON_MAX_GB", "3")) * 2 ** 30)
MIN_CAMERA_HEIGHT_M = 0.6


def unknown_regions(grid, min_area_m2: float = 0.05, max_regions: int = 50) -> list[dict]:
    """Unknown areas inside the scanned floor (not touching the grid border), largest first: places the
    scan never saw (floor hidden behind chairs, glass, a corner skipped). The simulator treats them as
    blocked; the user can rescan them. Each: {centroid_xy, area_m2, bbox_xy: [xmin, ymin, xmax, ymax],
    next_to_free: bool}."""
    unk = grid.data == cgrid.UNKNOWN
    lab, n = ndimage.label(unk)
    if not n:
        return []
    border = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
    free_d = ndimage.binary_dilation(grid.data == cgrid.FREE, np.ones((3, 3)))
    cell_a = grid.res ** 2
    out = []
    objs = ndimage.find_objects(lab)
    for i, sl in enumerate(objs, 1):
        if i in border or sl is None:
            continue
        m = lab[sl] == i
        area = float(m.sum()) * cell_a
        if area < min_area_m2:
            continue
        rr, cc = np.nonzero(m)
        rr, cc = rr + sl[0].start, cc + sl[1].start
        x, y = grid.cell_to_world(rr, cc)
        out.append({"centroid_xy": [round(float(x.mean()), 3), round(float(y.mean()), 3)],
                    "area_m2": round(area, 4),
                    "bbox_xy": [round(float(x.min()), 3), round(float(y.min()), 3),
                                round(float(x.max()), 3), round(float(y.max()), 3)],
                    "next_to_free": bool(free_d[rr, cc].any())})
    out.sort(key=lambda r: -r["area_m2"])
    return out[:max_regions]


def reconstruct(capture_dir: str, *, tier: str | None = None, backend: RelativeDepthBackend | None = None,
                params: dict | None = None) -> dict:
    """Run J1/J2 and return in-memory products: grid, height map, thresholds, stats."""
    timings = {}
    t0 = time.perf_counter()
    cap = load_capture(capture_dir)
    tier = tier or cap.tier
    prm = {**PARAMS.get(tier, PARAMS["T1_LIDAR"]), **(params or {})}
    timings["ingest_s"] = time.perf_counter() - t0

    t1 = time.perf_counter()
    src = depth_source_for(cap, tier, backend, max_depth_m=prm["max_depth_m"], local_degree=prm.get("local_degree", 0))
    frames = [f for f in (src.get(i) for i in cap.usable()) if f is not None]
    if len(frames) < 10:
        raise ValueError(f"only {len(frames)} frames with usable depth "
                         f"({len(cap.usable())} with normal tracking; depth source: {getattr(src, 'stats', {})})")
    timings["depth_s"] = time.perf_counter() - t1

    t2 = time.perf_counter()
    pts = np.concatenate([backproject(f.depth, f.K, f.T_world_cam, step=4) for f in frames[::2]])
    n, d, floor_frac = fit_floor_plane(pts)
    A = floor_alignment(n, d)
    pts = pts @ A[:3, :3].T + A[:3, 3]
    lo = np.percentile(pts[:, :2], 0.5, axis=0) - 0.4
    hi = np.percentile(pts[:, :2], 99.5, axis=0) + 0.4
    cams = np.array([(A @ f.T_world_cam)[:2, 3] for f in frames])
    lo = np.minimum(lo, cams.min(axis=0) - 0.4)
    hi = np.maximum(hi, cams.max(axis=0) + 0.4)
    span = hi - lo
    if np.any(span > 60):
        raise ValueError(f"scene extent {span.round(1)} m is larger than supported (60 m)")
    need = VoxelGrid.estimate_bytes(lo, hi)
    if need > MAX_VOXEL_BYTES:
        raise ValueError(f"scene extent {span.round(1)} m needs {need / 2 ** 30:.1f} GiB of voxels, above the "
                         f"{MAX_VOXEL_BYTES / 2 ** 30:.1f} GiB budget (CLEARANCE_RECON_MAX_GB); split the scan")
    cam_h = np.array([(A @ f.T_world_cam)[2, 3] for f in frames])
    warnings = list(cap.warnings)
    if np.median(cam_h) < MIN_CAMERA_HEIGHT_M:
        warnings.append(f"median camera height above the fitted floor is {np.median(cam_h):.2f} m: the floor may "
                        f"not have been seen (the fit may have used a table or shelf top)")
    timings["floor_s"] = time.perf_counter() - t2

    t3 = time.perf_counter()
    def fuse(exclude=None):
        v = VoxelGrid.create(lo, hi, trunc=prm["trunc"])
        for f in frames:
            v.integrate(f.depth, f.K, A @ f.T_world_cam, f.index, step=prm["step"],
                        weight_scale=f.info.get("inlier_ratio", 1.0), exclude=exclude)
        return v

    vg = fuse()
    gone = transient_voxels(vg, min_frames=prm["min_frames"], occ_tsdf_max=prm["occ_tsdf_max"])
    excl = transient_exclusion(gone)
    n_transient = int(gone.sum())
    del gone
    if excl is not None:  # something moved through the scan: fuse again without its measurements
        del vg
        vg = fuse(excl)
    timings["fusion_s"] = time.perf_counter() - t3

    t4 = time.perf_counter()
    grid, H = extract_occupancy(vg, min_frames=prm["min_frames"], min_occ_voxels=prm["min_occ_voxels"],
                                occ_tsdf_max=prm["occ_tsdf_max"])
    ths, noise = detect_thresholds(H, grid)
    timings["occupancy_s"] = time.perf_counter() - t4
    timings["total_s"] = time.perf_counter() - t0
    stats = {"tier": tier, "n_poses": len(cap.poses), "n_usable": len(cap.usable()), "n_depth_frames": len(frames),
             "limited_frac": round(cap.limited_frac, 4), "floor_normal_before": n.round(5).tolist(),
             "floor_offset_before_m": round(d, 4), "floor_inlier_frac": round(floor_frac, 3),
             "floor_noise_m": round(noise, 4) if np.isfinite(noise) else None,
             "voxels": list(vg.shape), "grid_shape": list(grid.shape),
             "transient_voxels": n_transient, "transient_refusion": excl is not None,
             "camera_height_median_m": round(float(np.median(cam_h)), 3),
             "unknown_regions": unknown_regions(grid),
             "timings": {k: round(v, 2) for k, v in timings.items()}, "warnings": warnings}
    if hasattr(src, "stats"):
        stats["depth_stats"] = src.stats
    used = len(frames) / max(len(cap.usable()), 1)
    if used < 0.5:
        warnings.append(f"only {used:.0%} of tracked frames gave usable depth ({src.stats if hasattr(src, 'stats') else ''}); "
                        f"parts of the room may be missing")
    return {"capture": cap, "grid": grid, "height_map": H, "thresholds": ths, "floor_align": A, "stats": stats}


def build_twin_from_capture(capture_dir: str, site_id: str, out_prefix: str, *, tier: str | None = None,
                            backend: RelativeDepthBackend | None = None, params: dict | None = None) -> Twin:
    """Writes <out_prefix>occ.png, twin.json and recon_report.json to storage; returns the Twin.
    Waypoints, keep-out zones and primitives are added later in the twin editor / J4."""
    if not out_prefix.endswith("/"):
        out_prefix += "/"
    r = reconstruct(capture_dir, tier=tier, backend=backend, params=params)
    grid = r["grid"]
    occ = cgrid.save(grid, out_prefix + "occ.png")
    occ.height_band_m = BAND
    free = np.argwhere(grid.data == cgrid.FREE)
    bounds = []
    if len(free) >= 3:
        hull = cv2.convexHull(free[:, ::-1].astype(np.int32)).reshape(-1, 2)
        x, y = grid.cell_to_world(hull[:, 1], hull[:, 0])
        bounds = [(round(float(a), 3), round(float(b), 3)) for a, b in zip(x, y)]
    ths = [Threshold(id=f"TH{i + 1}", segment=t["segment"], height_m=t["height_m"], status=t["status"])
           for i, t in enumerate(r["thresholds"])]
    version = 1
    if "twin_v" in out_prefix:
        try:
            version = int(out_prefix.rstrip("/").rsplit("twin_v", 1)[1])
        except ValueError:
            version = 1
    twin = Twin(twin_id=f"twin_{site_id}_v{version}", site_id=site_id, version=version,
                accuracy=Accuracy(method="estimated"), floor=Floor(plane=[0.0, 0.0, 1.0, 0.0], bounds_polygon=bounds),
                occupancy=occ, thresholds=ths, geometry_source="scan",
                assets={"capture_id": r["capture"].capture.capture_id})
    storage.write_json(out_prefix + "twin.json", twin)
    storage.write_json(out_prefix + "recon_report.json", {**r["stats"], "thresholds": r["thresholds"],
                                                          "floor_align": r["floor_align"].round(6).tolist()})
    return twin
