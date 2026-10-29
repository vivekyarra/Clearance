"""Image tracks -> floor tracks (foot point through the floor homography), and cross-camera merging
by floor proximity over overlapping time."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np
from scipy.ndimage import median_filter

from clearance_core.grid import Grid
from jobs.traffic.homography import ObserverCamera


@dataclass
class FloorTrack:
    track_id: str
    t: np.ndarray        # (N,) seconds on the shared clock
    xy: np.ndarray       # (N,2) site-frame floor positions
    cameras: set = field(default_factory=set)
    members: list = field(default_factory=list)

    @property
    def t0(self) -> float:
        return float(self.t[0])

    @property
    def t1(self) -> float:
        return float(self.t[-1])

    @property
    def duration(self) -> float:
        return self.t1 - self.t0

    def at(self, ts: np.ndarray) -> np.ndarray:
        return np.stack([np.interp(ts, self.t, self.xy[:, 0]), np.interp(ts, self.t, self.xy[:, 1])], axis=1)

    def resample(self, dt: float, max_gap: float = 1.5) -> tuple[np.ndarray, np.ndarray]:
        """Uniform samples (skipping gaps longer than max_gap seconds)."""
        if len(self.t) < 2:
            return self.t.copy(), self.xy.copy()
        ts = np.arange(self.t0, self.t1 + 1e-9, dt)
        gap_end = np.searchsorted(self.t, ts, side="left").clip(1, len(self.t) - 1)
        gaps = self.t[gap_end] - self.t[gap_end - 1]
        ts = ts[gaps <= max_gap]
        return ts, self.at(ts)


def foot_points(boxes: np.ndarray) -> np.ndarray:
    """Bottom-centre of [x0,y0,x1,y1] boxes."""
    b = np.asarray(boxes, dtype=np.float64).reshape(-1, 4)
    return np.stack([(b[:, 0] + b[:, 2]) / 2, b[:, 3]], axis=1)


def image_track_to_floor(history: Sequence[tuple], cam: ObserverCamera, track_id: str, *, t_offset: float = 0.0,
                         grid: Optional[Grid] = None, max_speed_mps: float = 4.0, edge_px: float = 2.0,
                         min_points: int = 3, min_travel_m: float = 0.5) -> Optional[FloorTrack]:
    """history rows: (frame_idx, t, x0, y0, x1, y1, score) from the tracker.

    Tracks that never get farther than `min_travel_m` from where they started are dropped: they are
    background-model ghosts or people standing for the whole recording, neither of which is
    walking traffic."""
    if len(history) < min_points:
        return None
    h = np.asarray(history, dtype=np.float64)
    t = h[:, 1] + t_offset
    boxes = h[:, 2:6]
    keep = boxes[:, 3] < cam.cam.h - edge_px  # feet cut off by the image edge: foot point unreliable
    xy, valid = cam.image_to_floor(foot_points(boxes))
    keep &= valid
    keep &= np.isfinite(t)
    if grid is not None and keep.any():
        idx = np.flatnonzero(keep)
        r, c = grid.world_to_cell(xy[idx, 0], xy[idx, 1])
        H, W = grid.shape
        keep[idx[~((r >= 0) & (r < H) & (c >= 0) & (c < W))]] = False
    t, xy = t[keep], xy[keep]
    if len(t) < min_points:
        return None
    order = np.argsort(t, kind="stable")  # frames may arrive out of time order
    t, xy = t[order], xy[order]
    if len(np.unique(t)) < len(t):  # repeated timestamps: one averaged sample per time
        ut, inv = np.unique(t, return_inverse=True)
        cnt = np.bincount(inv)
        xy = np.stack([np.bincount(inv, weights=xy[:, 0]) / cnt, np.bincount(inv, weights=xy[:, 1]) / cnt], axis=1)
        t = ut
        if len(t) < min_points:
            return None
    if len(t) >= 5:
        xy = np.stack([median_filter(xy[:, 0], size=5, mode="nearest"),
                       median_filter(xy[:, 1], size=5, mode="nearest")], axis=1)
    # drop teleports (mis-associated or badly projected boxes)
    ok = np.ones(len(t), dtype=bool)
    last = 0
    for i in range(1, len(t)):
        dt = max(t[i] - t[last], 1e-3)
        if np.linalg.norm(xy[i] - xy[last]) / dt > max_speed_mps:
            ok[i] = False
        else:
            last = i
    t, xy = t[ok], xy[ok]
    if len(t) < min_points:
        return None
    if np.linalg.norm(xy - xy[0], axis=1).max() < min_travel_m:
        return None
    cam_id = track_id.split(":")[0]
    return FloorTrack(track_id, t, xy, cameras={cam_id}, members=[track_id])


def track_distance(a: FloorTrack, b: FloorTrack, dt: float = 0.2, min_overlap_s: float = 1.0) -> Optional[float]:
    """Mean floor distance over the shared time span; None if they overlap less than min_overlap_s."""
    lo, hi = max(a.t0, b.t0), min(a.t1, b.t1)
    if hi - lo < min_overlap_s:
        return None
    ts = np.arange(lo, hi + 1e-9, dt)
    return float(np.linalg.norm(a.at(ts) - b.at(ts), axis=1).mean())


def _overlap(a: FloorTrack, b: FloorTrack) -> float:
    return min(a.t1, b.t1) - max(a.t0, b.t0)


def merge_tracks(tracks: Sequence[FloorTrack], dist_thresh_m: float = 0.6, min_overlap_s: float = 1.0,
                 bin_s: float = 0.1) -> list[FloorTrack]:
    """Greedy agglomerative merge of tracks from different cameras that stay within dist_thresh_m.
    A group never holds two time-overlapping tracks from the same camera (those are two people)."""
    groups = [[t] for t in tracks]
    pairs = []
    for i in range(len(tracks)):
        for j in range(i + 1, len(tracks)):
            a, b = tracks[i], tracks[j]
            if a.cameras & b.cameras:
                continue
            d = track_distance(a, b, min_overlap_s=min_overlap_s)
            if d is not None and d <= dist_thresh_m:
                pairs.append((d, i, j))
    owner = list(range(len(tracks)))

    def find(i):
        while owner[i] != i:
            owner[i] = owner[owner[i]]
            i = owner[i]
        return i

    for d, i, j in sorted(pairs):
        gi, gj = find(i), find(j)
        if gi == gj:
            continue
        conflict = any(x.cameras & y.cameras and _overlap(x, y) > 0.5 for x in groups[gi] for y in groups[gj])
        if conflict:
            continue
        owner[gj] = gi
        groups[gi] += groups[gj]
        groups[gj] = []
    out = []
    for g in groups:
        if not g:
            continue
        if len(g) == 1:
            out.append(g[0])
            continue
        t = np.concatenate([m.t for m in g])
        xy = np.concatenate([m.xy for m in g])
        bins = np.floor(t / bin_s).astype(np.int64)
        ub, inv = np.unique(bins, return_inverse=True)
        cnt = np.bincount(inv)
        tt = np.bincount(inv, weights=t) / cnt
        mx = np.bincount(inv, weights=xy[:, 0]) / cnt
        my = np.bincount(inv, weights=xy[:, 1]) / cnt
        members = sorted(sum((m.members for m in g), []))
        out.append(FloorTrack("+".join(members), tt, np.stack([mx, my], axis=1),
                              cameras=set().union(*(m.cameras for m in g)), members=members))
    return sorted(out, key=lambda f: f.t0)
