"""Occupancy grid I/O and coordinate helpers. See schemas.Occupancy for the pixel convention."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from clearance_core import storage
from clearance_core.schemas import Occupancy

OCC, UNKNOWN, FREE = 0, 127, 255


@dataclass
class Grid:
    data: np.ndarray  # uint8 HxW, values OCC/UNKNOWN/FREE
    res: float
    origin: tuple[float, float]

    @property
    def shape(self):
        return self.data.shape

    def world_to_cell(self, x, y):
        h = self.data.shape[0]
        c = np.floor((np.asarray(x) - self.origin[0]) / self.res).astype(int)
        r = h - 1 - np.floor((np.asarray(y) - self.origin[1]) / self.res).astype(int)
        return r, c

    def cell_to_world(self, r, c):
        h = self.data.shape[0]
        x = self.origin[0] + (np.asarray(c) + 0.5) * self.res
        y = self.origin[1] + (h - np.asarray(r) - 0.5) * self.res
        return x, y

    def blocked(self) -> np.ndarray:
        """Unknown is treated as blocked in sim (PLAN 7.2 step 5)."""
        return self.data != FREE

    def meta(self, uri: str) -> Occupancy:
        h, w = self.data.shape
        return Occupancy(uri=uri, resolution_m=self.res, origin_xy=self.origin, width=w, height=h)


def save(grid: Grid, key: str) -> Occupancy:
    # imencode + write_bytes works for any path (cv2.imwrite fails silently on non-ASCII paths on Windows)
    ok, buf = cv2.imencode(".png", grid.data)
    if not ok:
        raise IOError(f"could not encode occupancy grid for {key}")
    storage.path(key).write_bytes(buf.tobytes())
    return grid.meta(key)


def load(occ: Occupancy) -> Grid:
    p = storage.DATA / occ.uri
    if not p.is_file():
        raise FileNotFoundError(occ.uri)
    img = cv2.imdecode(np.frombuffer(p.read_bytes(), np.uint8), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError(f"unreadable occupancy image: {occ.uri}")
    return Grid(img, occ.resolution_m, tuple(occ.origin_xy))
