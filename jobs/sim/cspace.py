"""Configuration space per robot: footprint rasterized at K headings, obstacles dilated per heading.

cs[k, r, c] = 1 when the robot centered at cell (r, c) with heading k*2pi/K overlaps a blocked cell.
margin[k, r, c] = distance (cells) to the nearest colliding position at that heading; because the
c-obstacle is the Minkowski sum, this equals the robot-to-obstacle gap (within one cell)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import cv2
import numpy as np

from . import params as P
from .geometry import fill_polygons, footprint_kernel, polygon_bbox_rc


@dataclass(frozen=True)
class Kernels:
    ks: tuple[np.ndarray, ...]
    hs: tuple[int, ...]
    hmax: int
    K: int


@lru_cache(maxsize=32)
def _kernels_cached(fp_bytes: bytes, n: int, res: float, K: int) -> Kernels:
    fp = np.frombuffer(fp_bytes, dtype=np.float64).reshape(n, 2)
    ks, hs = [], []
    for k in range(K):
        kern, h = footprint_kernel(fp, k * 2 * math.pi / K, res)
        ks.append(kern)
        hs.append(h)
    return Kernels(tuple(ks), tuple(hs), max(hs), K)


def make_kernels(fp: np.ndarray, res: float, K: int = P.K_HEADINGS) -> Kernels:
    fp = np.ascontiguousarray(fp, dtype=np.float64)
    return _kernels_cached(fp.tobytes(), fp.shape[0], float(res), K)


def build_cspace(blocked: np.ndarray, kern: Kernels) -> np.ndarray:
    """blocked: bool/uint8 HxW. Outside the grid counts as blocked."""
    b = blocked.astype(np.uint8)
    h, w = b.shape
    cs = np.empty((kern.K, h, w), np.uint8)
    for k in range(kern.K):
        hk = kern.hs[k]
        cs[k] = cv2.dilate(b, kern.ks[k], anchor=(hk, hk), borderType=cv2.BORDER_CONSTANT, borderValue=1)
    return cs


def add_polygons(cs: np.ndarray, polys: list[np.ndarray], kern: Kernels, res: float, origin) -> None:
    """OR the c-space footprint of extra obstacle polygons into cs (in place), touching only a window
    around each polygon."""
    K, h, w = cs.shape
    for poly in polys:
        pad = kern.hmax + 1
        r0, r1, c0, c1 = polygon_bbox_rc(poly, res, origin, h, pad)
        rr0, rr1, cc0, cc1 = max(r0, 0), min(r1, h), max(c0, 0), min(c1, w)
        if rr0 >= rr1 or cc0 >= cc1:
            continue
        sub = np.zeros((r1 - r0, c1 - c0), np.uint8)
        fill_polygons(sub, [poly], res, origin, 1, offset_rc=(r0, c0), full_h=h)
        if not sub.any():
            continue
        for k in range(K):
            hk = kern.hs[k]
            d = cv2.dilate(sub, kern.ks[k], anchor=(hk, hk), borderType=cv2.BORDER_CONSTANT, borderValue=0)
            cs[k, rr0:rr1, cc0:cc1] |= d[rr0 - r0:rr1 - r0, cc0 - c0:cc1 - c0]


def add_discs(cs: np.ndarray, centers: np.ndarray, radius: float, kern: Kernels, res: float, origin) -> None:
    ang = np.linspace(0, 2 * np.pi, 16, endpoint=False)
    polys = [np.stack([x + radius * np.cos(ang), y + radius * np.sin(ang)], axis=1) for x, y in centers]
    add_polygons(cs, polys, kern, res, origin)


def margin_cells(cs: np.ndarray) -> np.ndarray:
    K, h, w = cs.shape
    out = np.empty((K, h, w), np.uint8)
    for k in range(K):
        d = cv2.distanceTransform((cs[k] == 0).astype(np.uint8), cv2.DIST_L2, 5)
        np.minimum(d, 255.0, out=d)
        out[k] = d.astype(np.uint8)
    return out


def any_free(cs: np.ndarray) -> np.ndarray:
    return (cs == 0).any(axis=0)
