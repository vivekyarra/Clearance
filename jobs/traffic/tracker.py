"""ByteTrack-style multi-object tracker. Own implementation (Apache-2.0, this repo),
following the published algorithm (Zhang et al., "ByteTrack", ECCV 2022):

* constant-velocity Kalman filter on (cx, cy, aspect, height);
* stage 1: confirmed + lost tracks vs high-score detections (IoU, Hungarian);
* stage 2: remaining tracked tracks vs low-score detections (recovers occluded people);
* stage 3: tentative tracks vs leftover high-score detections; tentative tracks need a second hit;
* lost tracks are kept `track_buffer` frames and re-identified by IoU with their prediction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy.optimize import linear_sum_assignment


# ---------- Kalman filter ----------
class KalmanXYAH:
    """State (cx, cy, a, h, vcx, vcy, va, vh); measurement (cx, cy, a, h). Noise scales with box height."""
    std_pos = 1.0 / 20
    std_vel = 1.0 / 160

    def __init__(self):
        self.F = np.eye(8)
        self.F[:4, 4:] = np.eye(4)
        self.Hm = np.eye(4, 8)

    def initiate(self, z: np.ndarray):
        mean = np.r_[z, np.zeros(4)]
        h = z[3]
        std = [2 * self.std_pos * h, 2 * self.std_pos * h, 1e-2, 2 * self.std_pos * h,
               10 * self.std_vel * h, 10 * self.std_vel * h, 1e-5, 10 * self.std_vel * h]
        return mean, np.diag(np.square(std))

    def predict(self, mean, cov):
        h = mean[3]
        q = [self.std_pos * h, self.std_pos * h, 1e-2, self.std_pos * h,
             self.std_vel * h, self.std_vel * h, 1e-5, self.std_vel * h]
        mean = self.F @ mean
        cov = self.F @ cov @ self.F.T + np.diag(np.square(q))
        return mean, cov

    def update(self, mean, cov, z):
        h = mean[3]
        r = np.diag(np.square([self.std_pos * h, self.std_pos * h, 1e-1, self.std_pos * h]))
        S = self.Hm @ cov @ self.Hm.T + r
        Kg = cov @ self.Hm.T @ np.linalg.inv(S)
        mean = mean + Kg @ (z - self.Hm @ mean)
        cov = (np.eye(8) - Kg @ self.Hm) @ cov
        return mean, cov


def tlbr_to_xyah(b) -> np.ndarray:
    w, h = b[2] - b[0], b[3] - b[1]
    return np.array([b[0] + w / 2, b[1] + h / 2, w / max(h, 1e-6), h], dtype=np.float64)


def xyah_to_tlbr(m) -> np.ndarray:
    cx, cy, a, h = m[:4]
    w = a * h
    return np.array([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2])


def iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a = np.asarray(a, dtype=np.float64).reshape(-1, 4)
    b = np.asarray(b, dtype=np.float64).reshape(-1, 4)
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)))
    x0 = np.maximum(a[:, None, 0], b[None, :, 0])
    y0 = np.maximum(a[:, None, 1], b[None, :, 1])
    x1 = np.minimum(a[:, None, 2], b[None, :, 2])
    y1 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(x1 - x0, 0, None) * np.clip(y1 - y0, 0, None)
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    return inter / np.maximum(area_a[:, None] + area_b[None, :] - inter, 1e-9)


def linear_assignment(cost: np.ndarray, thresh: float):
    """Hungarian matching keeping pairs with cost <= thresh. Returns (matches, unmatched_a, unmatched_b)."""
    if cost.size == 0:
        return [], list(range(cost.shape[0])), list(range(cost.shape[1]))
    c = np.where(cost > thresh, thresh + 1e3, cost)
    r, k = linear_sum_assignment(c)
    matches = [(int(i), int(j)) for i, j in zip(r, k) if cost[i, j] <= thresh]
    ma = {i for i, _ in matches}
    mb = {j for _, j in matches}
    return matches, [i for i in range(cost.shape[0]) if i not in ma], [j for j in range(cost.shape[1]) if j not in mb]


# ---------- tracks ----------
TRACKED, LOST, REMOVED = "tracked", "lost", "removed"


@dataclass
class Track:
    track_id: int
    mean: np.ndarray
    cov: np.ndarray
    score: float
    start_frame: int
    frame_id: int
    state: str = TRACKED
    activated: bool = False
    hits: int = 1
    # history of matched detections: (frame_idx, t, x0, y0, x1, y1, score)
    history: list = field(default_factory=list)

    @property
    def tlbr(self) -> np.ndarray:
        return xyah_to_tlbr(self.mean)

    @property
    def end_frame(self) -> int:
        return self.frame_id


class ByteTracker:
    def __init__(self, track_thresh: float = 0.5, low_thresh: float = 0.1, new_track_thresh: float = 0.6,
                 match_thresh: float = 0.8, second_match_thresh: float = 0.5, unconfirmed_thresh: float = 0.7,
                 track_buffer: int = 30, min_hits: int = 2):
        self.track_thresh = track_thresh
        self.low_thresh = low_thresh
        self.new_track_thresh = new_track_thresh
        self.match_thresh = match_thresh            # IoU-distance (1 - IoU) gates
        self.second_match_thresh = second_match_thresh
        self.unconfirmed_thresh = unconfirmed_thresh
        self.track_buffer = track_buffer
        self.min_hits = min_hits
        self.kf = KalmanXYAH()
        self.tracked: list[Track] = []
        self.lost: list[Track] = []
        self.removed: list[Track] = []
        self.frame_id = 0
        self._next_id = 1

    def _new_id(self) -> int:
        i = self._next_id
        self._next_id += 1
        return i

    def _predict(self, tracks: list[Track]):
        for t in tracks:
            if t.state != TRACKED:
                t.mean[7] = 0.0  # do not let the height keep growing while unseen
            t.mean, t.cov = self.kf.predict(t.mean, t.cov)

    def _apply(self, t: Track, det: np.ndarray, frame_idx: int, ts: float):
        t.mean, t.cov = self.kf.update(t.mean, t.cov, tlbr_to_xyah(det[:4]))
        t.score = float(det[4])
        t.frame_id = self.frame_id
        t.hits += 1
        if t.state == LOST or t.activated or t.hits >= self.min_hits:
            t.activated = True
        t.state = TRACKED
        t.history.append((frame_idx, ts, *map(float, det[:5])))

    def update(self, dets: np.ndarray, frame_idx: Optional[int] = None, t: float = 0.0) -> list[Track]:
        """dets: (N,5) [x0,y0,x1,y1,score]. Returns the confirmed, currently tracked tracks."""
        self.frame_id += 1
        fidx = self.frame_id if frame_idx is None else frame_idx
        dets = np.asarray(dets, dtype=np.float64).reshape(-1, 5)
        dets = dets[(dets[:, 2] > dets[:, 0]) & (dets[:, 3] > dets[:, 1])]
        high = dets[dets[:, 4] >= self.track_thresh]
        low = dets[(dets[:, 4] >= self.low_thresh) & (dets[:, 4] < self.track_thresh)]

        confirmed = [tr for tr in self.tracked if tr.activated]
        unconfirmed = [tr for tr in self.tracked if not tr.activated]
        pool = confirmed + self.lost
        self._predict(pool + unconfirmed)

        # stage 1: high-score boxes, currently tracked tracks first, then lost ones (a cascade: when
        # two people cross, the visible person's box must not be handed to the hidden one's track)
        cost = 1.0 - iou_matrix([tr.tlbr for tr in confirmed], high[:, :4])
        m1, u_conf, u_high = linear_assignment(cost, self.match_thresh)
        for i, j in m1:
            self._apply(confirmed[i], high[j], fidx, t)
        lost = list(self.lost)
        rest = high[u_high]
        cost = 1.0 - iou_matrix([tr.tlbr for tr in lost], rest[:, :4])
        m1b, _, u_rest = linear_assignment(cost, self.match_thresh)
        for i, j in m1b:
            self._apply(lost[i], rest[j], fidx, t)
        u_high = [u_high[j] for j in u_rest]
        # stage 2: only tracks that were tracked last frame (ByteTrack), against low-score boxes
        rem = [confirmed[i] for i in u_conf]
        cost = 1.0 - iou_matrix([tr.tlbr for tr in rem], low[:, :4])
        m2, u_rem, _ = linear_assignment(cost, self.second_match_thresh)
        for i, j in m2:
            self._apply(rem[i], low[j], fidx, t)
        for i in u_rem:
            rem[i].state = LOST
        # stage 3: tentative tracks
        left = high[u_high]
        cost = 1.0 - iou_matrix([tr.tlbr for tr in unconfirmed], left[:, :4])
        m3, u_unc, u_left = linear_assignment(cost, self.unconfirmed_thresh)
        for i, j in m3:
            self._apply(unconfirmed[i], left[j], fidx, t)
        for i in u_unc:
            unconfirmed[i].state = REMOVED
        # new tracks
        for j in u_left:
            d = left[j]
            if d[4] < self.new_track_thresh:
                continue
            mean, cov = self.kf.initiate(tlbr_to_xyah(d[:4]))
            tr = Track(self._new_id(), mean, cov, float(d[4]), self.frame_id, self.frame_id,
                       activated=self.frame_id == 1 or self.min_hits <= 1)
            tr.history.append((fidx, t, *map(float, d[:5])))
            self.tracked.append(tr)

        everyone = self.tracked + self.lost
        for tr in everyone:
            if tr.state == LOST and self.frame_id - tr.end_frame > self.track_buffer:
                tr.state = REMOVED
        self.tracked = [tr for tr in everyone if tr.state == TRACKED]
        self.lost = [tr for tr in everyone if tr.state == LOST]
        self.removed += [tr for tr in everyone if tr.state == REMOVED]
        self._dedupe()
        return [tr for tr in self.tracked if tr.activated]

    def _dedupe(self):
        """A lost track overlapping a live track almost entirely is the same person: drop the younger.
        Two tracks that were both matched in the same frame are two people (a passer-by walking in
        front of a person standing still, an overtaking walker): such a pair is never deduplicated.
        A duplicate of one person never shares a frame with its twin, since each detection feeds
        one track."""
        if not self.tracked or not self.lost:
            return
        iou = iou_matrix([a.tlbr for a in self.tracked], [b.tlbr for b in self.lost])
        drop_t, drop_l = set(), set()
        for i, j in zip(*np.nonzero(iou > 0.85)):
            a, b = self.tracked[i], self.lost[j]
            if not {h[0] for h in a.history}.isdisjoint(h[0] for h in b.history):
                continue
            if (a.frame_id - a.start_frame) >= (b.frame_id - b.start_frame):
                drop_l.add(j)
            else:
                drop_t.add(i)
        for i in drop_t:
            self.tracked[i].state = REMOVED
        for j in drop_l:
            self.lost[j].state = REMOVED
        self.removed += [self.tracked[i] for i in drop_t] + [self.lost[j] for j in drop_l]
        self.tracked = [t for i, t in enumerate(self.tracked) if i not in drop_t]
        self.lost = [t for j, t in enumerate(self.lost) if j not in drop_l]

    def all_tracks(self, min_len: int = 1) -> list[Track]:
        """Every confirmed track seen so far (any state), with at least `min_len` detections."""
        seen, out = set(), []
        for tr in self.removed + self.lost + self.tracked:
            if tr.track_id in seen or not tr.activated:
                continue
            seen.add(tr.track_id)
            if len(tr.history) >= min_len:
                out.append(tr)
        return sorted(out, key=lambda tr: tr.track_id)
