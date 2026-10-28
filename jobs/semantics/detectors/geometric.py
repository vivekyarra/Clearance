"""CPU fallback detector: occupancy blobs (+ point-cloud heights) labeled by size/height heuristics.

Thin structures (walls, scan noise) are removed with a morphological opening, the remaining
occupied cells are split into connected components, and each component is labeled from its
footprint shape and height. Labels are guesses, so confidences are low (<= 0.35) and the editor
highlights them for the user to confirm.

Touching objects (chairs pushed against a table) form one component. They are separated by
  * height layering: per-cell maximum heights are clustered into layers (table top 0.74 m vs chair
    seat 0.46 m); a layer piece becomes its own object when it is big and thick enough and lies on
    the outline of the component (an item standing on a counter stays part of the counter);
  * shape: without height data, the smallest erosion that cuts the blob into big parts marks a
    narrow neck; the parts grow back by nearest-core assignment.
Split pieces carry extra["split"] ("height" | "shape") and 0.85x the confidence. A component that
cannot be split but is clearly not one convex footprint (solidity < 0.9) keeps one object with
extra["possible_merge"] and 0.7x the confidence.
"""
from __future__ import annotations

import math
from typing import Optional

import cv2
import numpy as np
from scipy import ndimage

from jobs.semantics.detectors.base import Detector, Object3D, SceneContext
from jobs.semantics.primitives import fit_footprint_box, is_round, shape_stats
from clearance_core.grid import FREE, OCC, UNKNOWN, Grid

DEFAULT_HEIGHTS = {"chair": 0.85, "table": 0.75, "shelf": 1.8, "stool": 0.65, "trash bin": 0.7,
                   "pillar": 2.5, "counter": 1.0, "box": 0.5}


def classify_blob(length: float, width: float, area: float, round_: bool, height: Optional[float]):
    """(label, shape, confidence) from footprint size, roundness and height (m)."""
    conf = 0.35 if height is not None else 0.2
    if round_:
        d = (length + width) / 2
        if height is None:
            return ("stool" if d <= 0.45 else "trash bin"), "cylinder", conf
        if height >= 1.8:
            return "pillar", "cylinder", conf
        if height < 0.85 and d <= 0.5:
            return "stool", "cylinder", conf
        return "trash bin", "cylinder", conf
    if height is not None:
        if height >= 1.4:
            return "shelf", "box", conf
        if height >= 0.85 and length >= 1.0:
            return "counter", "box", conf
        if 0.55 <= height < 0.85 and area >= 0.25:
            return "table", "box", conf
        if area < 0.36 and length < 0.75 and 0.4 <= height <= 1.2:
            return "chair", "box", conf
        return "box", "box", conf * 0.8
    if length >= 1.5 and length / max(width, 1e-6) >= 2.5:
        return "shelf", "box", conf
    if area >= 0.36:
        return "table", "box", conf
    return "chair", "box", conf


def rasterize_cloud(cloud: np.ndarray, res: float = 0.025, band=(0.03, 2.0)) -> Grid:
    pts = cloud[(cloud[:, 2] >= band[0]) & (cloud[:, 2] <= band[1])]
    lo = cloud[:, :2].min(axis=0) - 0.2
    hi = cloud[:, :2].max(axis=0) + 0.2
    W, H = (np.ceil((hi - lo) / res).astype(int) + 1)
    g = Grid(np.full((H, W), FREE, dtype=np.uint8), res, (float(lo[0]), float(lo[1])))
    r, c = g.world_to_cell(pts[:, 0], pts[:, 1])
    ok = (r >= 0) & (r < H) & (c >= 0) & (c < W)
    g.data[r[ok], c[ok]] = OCC
    return g


class GeometricDetector(Detector):
    name = "geometric"

    def __init__(self, min_thickness_m: float = 0.12, min_area_m2: float = 0.03, max_area_m2: float = 6.0,
                 height_pct: float = 90.0, min_unmeasured_area_m2: float = 0.08, wall_margin_m: float = 0.2,
                 split: bool = True, layer_gap_m: float = 0.12, min_piece_area_m2: float = 0.08,
                 min_piece_width_m: float = 0.2, max_neck_m: float = 0.4, merge_solidity: float = 0.9):
        self.min_thickness_m = min_thickness_m
        self.min_area_m2 = min_area_m2
        self.max_area_m2 = max_area_m2
        self.height_pct = height_pct
        self.min_unmeasured_area_m2 = min_unmeasured_area_m2
        self.wall_margin_m = wall_margin_m
        self.split = split
        self.layer_gap_m = layer_gap_m
        self.min_piece_area_m2 = min_piece_area_m2
        self.min_piece_width_m = min_piece_width_m
        self.max_neck_m = max_neck_m
        self.merge_solidity = merge_solidity

    def can_run(self, ctx: SceneContext) -> tuple[bool, str]:
        if ctx.grid is None and ctx.cloud is None:
            return False, "no occupancy grid and no point cloud"
        return True, ""

    def detect(self, ctx: SceneContext) -> list[Object3D]:
        grid = ctx.grid if ctx.grid is not None else rasterize_cloud(ctx.cloud, band=ctx.height_band)
        return self.detect_grid(grid, ctx.cloud, ctx.height_band)

    def detect_grid(self, grid: Grid, cloud: Optional[np.ndarray] = None, band=(0.03, 2.0)) -> list[Object3D]:
        occ = (grid.data == OCC).astype(np.uint8)
        H, W = occ.shape
        rad = max(1, int(math.ceil(self.min_thickness_m / 2 / grid.res)))
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * rad + 1, 2 * rad + 1))
        opened = cv2.morphologyEx(occ, cv2.MORPH_OPEN, k)
        n, lab, stats, _ = cv2.connectedComponentsWithStats(opened, connectivity=8)

        hmap, near_wall = self._cell_heights(grid, cloud, band)
        known = grid.data != UNKNOWN
        span_h = max(1, np.ptp(np.nonzero(known.any(axis=1))[0]) if known.any() else H)
        span_w = max(1, np.ptp(np.nonzero(known.any(axis=0))[0]) if known.any() else W)
        cell_area = grid.res * grid.res
        out: list[Object3D] = []
        for i in range(1, n):
            x, y, w, h, area_px = stats[i]
            area = area_px * cell_area
            if area < self.min_area_m2:
                continue
            if w >= 0.8 * span_w or h >= 0.8 * span_h:  # spans the room: structure, not furniture
                continue
            sl = (slice(y, y + h), slice(x, x + w))
            comp = lab[sl] == i
            parts, how = self._split(comp, hmap[sl] if hmap is not None else None, grid.res)
            for part in parts:
                obj = self._make_object(grid, part, sl, hmap, near_wall, cloud is not None)
                if obj is None:
                    continue
                if how:
                    obj.score = round(obj.score * 0.85, 4)
                    obj.extra.update(split=how, split_from_area_m2=float(area))
                elif self.split and _solidity(part) < self.merge_solidity:
                    obj.score = round(obj.score * 0.7, 4)
                    obj.extra["possible_merge"] = True
                out.append(obj)
        return out

    def _make_object(self, grid: Grid, part: np.ndarray, sl, hmap, near_wall, have_cloud: bool):
        cell_area = grid.res * grid.res
        area = float(part.sum()) * cell_area
        if area < self.min_area_m2 or area > self.max_area_m2:
            return None
        rr, cc = np.nonzero(part)
        rr, cc = rr + sl[0].start, cc + sl[1].start
        wx, wy = grid.cell_to_world(rr, cc)
        xy = np.stack([wx, wy], axis=1)
        st = shape_stats(xy, area)
        box = fit_footprint_box(xy, trim=0)
        # Cell centres: an object edge falls on average mid-cell, so centre-to-centre extents are
        # the unbiased size estimate for "any point in the cell" occupancy.
        box_l, box_w = box.length, box.width
        if box_l / max(box_w, 1e-6) > 8 and box_w < 0.2:  # leftover wall segment
            return None
        hgt = self._height(hmap, near_wall, rr, cc)
        if hgt is None and area < self.min_unmeasured_area_m2 and have_cloud:
            return None  # tiny blob with no height evidence (wall corners, door jambs)
        round_ = is_round(st)
        label, shape, conf = classify_blob(box_l, box_w, area, round_, hgt)
        height = hgt if hgt is not None else DEFAULT_HEIGHTS.get(label, 0.75)
        pts = np.c_[xy, np.full(len(xy), height / 2)]
        return Object3D(label=label, points=pts, score=conf, n_views=0, shape=shape, height=height,
                        backend=self.name, extra={"area_m2": area, "height_measured": hgt is not None, **st})

    # ---------- heights ----------
    def _cell_heights(self, grid: Grid, cloud: Optional[np.ndarray], band):
        """Per-cell maximum height (NaN without >= 2 returns) and the cells within wall_margin_m of
        unknown space. Working per cell keeps a wall face rising behind a counter (a thin ring of
        cells) from setting the height, while a chair back (a full row of cells) still counts."""
        if cloud is None or len(cloud) == 0:
            return None, None
        H, W = grid.data.shape
        pts = cloud[(cloud[:, 2] >= band[0]) & (cloud[:, 2] <= 3.0)]
        r, c = grid.world_to_cell(pts[:, 0], pts[:, 1])
        ok = (r >= 0) & (r < H) & (c >= 0) & (c < W)
        r, c, z = r[ok], c[ok], pts[ok, 2]
        cell = r.astype(np.int64) * W + c
        cmax = np.full(H * W, -np.inf)
        cnt = np.zeros(H * W, dtype=np.int64)
        if len(z):
            np.maximum.at(cmax, cell, z)
            np.add.at(cnt, cell, 1)
        hmap = np.where(cnt >= 2, cmax, np.nan).reshape(H, W)  # a lone return is not enough evidence
        unknown = (grid.data == UNKNOWN).astype(np.uint8)
        if unknown.any():
            dist = cv2.distanceTransform((1 - unknown).astype(np.uint8), cv2.DIST_L2, 3) * grid.res
            near_wall = dist < self.wall_margin_m
        else:
            near_wall = np.zeros((H, W), bool)
        return hmap, near_wall

    def _height(self, hmap, near_wall, rr, cc) -> Optional[float]:
        """High percentile of the per-cell maxima; cells next to unobserved space (wall returns behind
        furniture against a wall) are ignored unless they are all the object has."""
        if hmap is None:
            return None
        h = hmap[rr, cc]
        have = np.isfinite(h)
        keep = have & ~near_wall[rr, cc]
        if not keep.any():
            keep = have
        hh = h[keep]
        return float(np.percentile(hh, self.height_pct)) if len(hh) >= 3 else None

    # ---------- splitting touching objects ----------
    def _split(self, comp: np.ndarray, hloc: Optional[np.ndarray], res: float) -> tuple[list[np.ndarray], str]:
        if not self.split:
            return [comp], ""
        if hloc is not None:
            parts = self._split_height(comp, hloc, res)
            if len(parts) > 1:
                return parts, "height"
        parts = self._split_shape(comp, res)
        if len(parts) > 1:
            return parts, "shape"
        return [comp], ""

    def _big_enough(self, piece: np.ndarray, res: float) -> bool:
        if piece.sum() * res * res < self.min_piece_area_m2:
            return False
        rr, cc = np.nonzero(piece)
        xy = np.stack([cc * res, -rr * res], axis=1).astype(np.float64)
        return fit_footprint_box(xy, trim=0).width + res >= self.min_piece_width_m

    def _split_height(self, comp: np.ndarray, hloc: np.ndarray, res: float) -> list[np.ndarray]:
        h = np.where(comp, hloc, np.nan)
        have = np.isfinite(h)
        if have.sum() < 0.5 * comp.sum():
            return [comp]
        vals = h[have]
        # layers: runs of populated 2 cm height bins; sparse bins (stray returns) do not bridge gaps
        bins = np.floor(vals / 0.02).astype(int)
        b0 = bins.min()
        cnt = np.bincount(bins - b0)
        populated = cnt >= max(2, 0.01 * len(vals))
        gap_bins = int(round(self.layer_gap_m / 0.02))
        layers, run_start, last = [], None, None
        for b in np.flatnonzero(populated):
            if run_start is None:
                run_start = last = b
            elif b - last > gap_bins:
                layers.append((run_start, last))
                run_start = last = b
            else:
                last = b
        if run_start is not None:
            layers.append((run_start, last))
        if len(layers) < 2:
            return [comp]
        centres = np.array([(lo + hi + 1) / 2 * 0.02 + b0 * 0.02 for lo, hi in layers])
        layer_of = np.full(comp.shape, -1)
        layer_of[have] = np.argmin(np.abs(h[have][:, None] - centres[None, :]), axis=1)
        pieces = []
        for li in range(len(layers)):
            lab, n = ndimage.label(layer_of == li)
            pieces += [lab == j for j in range(1, n + 1)]
        # pieces that are too small, too thin, or do not reach the component outline (an item
        # standing on a counter) are absorbed by the neighbour they share the longest border with
        # outline band 3 cells deep: edge cells often lack returns, so pieces end a cell or two inside
        outline = comp & ~ndimage.binary_erosion(comp, iterations=3, border_value=0)
        pieces = self._absorb(pieces, lambda p: self._big_enough(p, res) and _touches_outline(p, outline))
        if len(pieces) < 2:
            return [comp]
        return _assign_rest(comp, pieces)

    def _split_shape(self, comp: np.ndarray, res: float) -> list[np.ndarray]:
        if _solidity(comp) >= 0.9:
            return [comp]  # convex footprint: one object
        max_r = max(1, int(round(self.max_neck_m / 2 / res)))
        padded = np.pad(comp, max_r + 1)
        for r in range(1, max_r + 1):
            st = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
            core = cv2.erode(padded.astype(np.uint8), st).astype(bool)[max_r + 1:-max_r - 1, max_r + 1:-max_r - 1]
            lab, n = ndimage.label(core)
            if n < 2:
                if n == 0:
                    break
                continue
            cores = [lab == j for j in range(1, n + 1)]
            parts = _assign_rest(comp, cores)
            big = [p for p in parts if self._big_enough(p, res)]
            if len(big) >= 2:
                parts = _assign_rest(comp, [p for p in parts if self._big_enough(p, res)])
                return parts
        return [comp]

    @staticmethod
    def _absorb(pieces: list[np.ndarray], keep) -> list[np.ndarray]:
        pieces = [p.copy() for p in pieces if p.any()]
        changed = True
        while changed and len(pieces) > 1:
            changed = False
            largest = max(range(len(pieces)), key=lambda j: pieces[j].sum())
            weak = [i for i, p in enumerate(pieces) if i != largest and not keep(p)]
            if not weak:
                break
            i = min(weak, key=lambda j: pieces[j].sum())  # smallest weak piece first
            ring = ndimage.binary_dilation(pieces[i]) & ~pieces[i]
            shared = [(int((ring & pieces[j]).sum()), j) for j in range(len(pieces)) if j != i]
            best = max(shared, default=(0, -1))
            if best[0] == 0:
                # isolated (cells between them have no height): join the nearest piece
                d = ndimage.distance_transform_edt(~pieces[i])
                best = (0, min((j for j in range(len(pieces)) if j != i), key=lambda j: d[pieces[j]].min()))
            pieces[best[1]] |= pieces[i]
            del pieces[i]
            changed = True
        return pieces


def _solidity(mask: np.ndarray) -> float:
    rr, cc = np.nonzero(mask)
    if len(rr) < 3:
        return 1.0
    pts = np.stack([cc, rr], axis=1).astype(np.float32)
    # cell squares, not centres: add the four corners of every boundary cell
    offs = np.array([[-0.5, -0.5], [0.5, -0.5], [0.5, 0.5], [-0.5, 0.5]], np.float32)
    hull = cv2.convexHull((pts[:, None, :] + offs[None]).reshape(-1, 2))
    ha = cv2.contourArea(hull)
    return float(len(rr) / ha) if ha > 0 else 1.0


def _touches_outline(piece: np.ndarray, outline: np.ndarray, frac: float = 0.25) -> bool:
    filled = ndimage.binary_fill_holes(piece)  # cells without returns inside a piece are not its edge
    edge = filled & ~ndimage.binary_erosion(filled, border_value=0)
    n = int(edge.sum())
    return n > 0 and (edge & outline).sum() >= frac * n


def _assign_rest(comp: np.ndarray, seeds: list[np.ndarray]) -> list[np.ndarray]:
    """Every component cell goes to the nearest seed (Euclidean nearest seed cell)."""
    lab = np.zeros(comp.shape, np.int32)
    for j, sd in enumerate(seeds, 1):
        lab[sd & comp] = j
    if not (lab > 0).any():
        return [comp]
    _, (ir, ic) = ndimage.distance_transform_edt(lab == 0, return_indices=True)
    full = lab[ir, ic]
    return [comp & (full == j) for j in range(1, len(seeds) + 1) if (comp & (full == j)).any()]
