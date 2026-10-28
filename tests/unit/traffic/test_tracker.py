"""ByteTrack-style tracker on reference tracks: moving boxes with crossings, occlusions and clutter.
ID switches are counted per ground-truth person (CLEAR-MOT style)."""
from dataclasses import dataclass, field

import numpy as np
import pytest

from jobs.traffic.tracker import ByteTracker, KalmanXYAH, iou_matrix, linear_assignment, tlbr_to_xyah, xyah_to_tlbr


@dataclass
class Walker:
    start: int
    end: int
    x0: float
    y0: float
    vx: float
    vy: float
    w: float = 40.0
    h: float = 100.0
    hidden: set = field(default_factory=set)      # frames with no detection
    low: set = field(default_factory=set)         # frames with a low-score detection (partial occlusion)

    def box(self, f):
        x, y = self.x0 + self.vx * (f - self.start), self.y0 + self.vy * (f - self.start)
        return np.array([x - self.w / 2, y - self.h, x + self.w / 2, y])


def run(walkers, n_frames, seed=0, noise=1.5, clutter_per_frame=0.0, tracker=None):
    rng = np.random.default_rng(seed)
    tr = tracker or ByteTracker(track_buffer=30)
    assign = {i: [] for i in range(len(walkers))}  # gt index -> list of (frame, track id)
    for f in range(n_frames):
        dets, gt = [], []
        for i, wk in enumerate(walkers):
            if not (wk.start <= f <= wk.end):
                continue
            b = wk.box(f)
            if f in wk.hidden:
                continue
            gt.append((i, b))
            score = rng.uniform(0.2, 0.45) if f in wk.low else rng.uniform(0.7, 0.95)
            dets.append([*(b + rng.normal(0, noise, 4)), score])
        for _ in range(rng.poisson(clutter_per_frame)):
            x, y = rng.uniform(0, 600), rng.uniform(100, 400)
            dets.append([x, y - 60, x + 25, y, rng.uniform(0.5, 0.75)])
        out = tr.update(np.array(dets).reshape(-1, 5), f, f / 10)
        if gt and out:  # one-to-one GT <-> track matching at IoU >= 0.5
            cost = 1.0 - iou_matrix([b for _, b in gt], [t.tlbr for t in out])
            for gi, j in linear_assignment(cost, 0.5)[0]:
                assign[gt[gi][0]].append((f, out[j].track_id))
    return tr, assign


def id_switches(assign):
    sw = 0
    for seq in assign.values():
        ids = [t for _, t in seq]
        sw += sum(1 for a, b in zip(ids, ids[1:]) if a != b)
    return sw


def coverage(assign, walkers):
    tot = sum(w.end - w.start + 1 - len(w.hidden) for w in walkers)
    return sum(len(v) for v in assign.values()) / tot


def test_two_people_crossing():
    walkers = [Walker(0, 80, 50, 300, 6.0, 0.0), Walker(0, 80, 530, 310, -6.0, 0.0)]
    tr, assign = run(walkers, 81)
    assert id_switches(assign) == 0
    assert coverage(assign, walkers) > 0.9
    assert len(tr.all_tracks(min_len=10)) == 2


def test_crossing_with_occlusion_of_the_far_person():
    a = Walker(0, 90, 40, 300, 6.0, 0.0)
    b = Walker(0, 90, 580, 290, -6.0, 0.0, w=36, h=92)
    # b is hidden behind a around the crossing (frames 40..49), partially visible just before/after
    b.hidden = set(range(42, 49))
    b.low = {39, 40, 41, 49, 50}
    tr, assign = run([a, b], 91, seed=1)
    assert id_switches(assign) == 0
    assert len(tr.all_tracks(min_len=10)) == 2


def test_long_occlusion_recovered_within_buffer_and_lost_beyond():
    w = Walker(0, 100, 100, 300, 3.0, 0.5)
    w.hidden = set(range(30, 50))  # 20 frames, buffer is 30
    _, assign = run([w], 101)
    assert id_switches({0: assign[0]}) == 0
    w2 = Walker(0, 100, 100, 300, 3.0, 0.5)
    w2.hidden = set(range(30, 75))  # 45 frames > buffer -> a new id afterwards is expected
    tr, assign2 = run([w2], 101, tracker=ByteTracker(track_buffer=30))
    ids = {t for _, t in assign2[0]}
    assert len(ids) == 2


def test_many_people_with_crossings_and_clutter():
    rng = np.random.default_rng(7)
    walkers = []
    for k in range(8):
        start = int(rng.integers(0, 60))
        left = k % 2 == 0
        y = 200 + 25 * k
        walkers.append(Walker(start, start + 100, 20 if left else 620, y, (5.5 if left else -5.5) + rng.normal(0, 0.5),
                              rng.normal(0, 0.3)))
    walkers[3].hidden = set(range(walkers[3].start + 40, walkers[3].start + 46))
    tr, assign = run(walkers, 170, seed=3, clutter_per_frame=0.5)
    sw = id_switches(assign)
    assert sw <= 1, f"{sw} id switches"
    assert coverage(assign, walkers) > 0.85
    # clutter (single-frame random boxes) does not become confirmed long tracks
    long_tracks = tr.all_tracks(min_len=15)
    assert len(long_tracks) <= len(walkers) + 1


def test_tracker_edge_cases():
    tr = ByteTracker()
    assert tr.update(np.zeros((0, 5))) == []
    # degenerate boxes are ignored
    assert tr.update(np.array([[10, 10, 10, 50, 0.9], [5, 5, 2, 2, 0.9]])) == []
    # a single low-score detection never starts a track
    tr = ByteTracker()
    for f in range(10):
        tr.update(np.array([[100, 100, 140, 200, 0.3]]), f)
    assert tr.all_tracks() == []
    # first-frame detections are confirmed immediately (ByteTrack), later ones need a second hit
    tr = ByteTracker()
    assert len(tr.update(np.array([[0, 0, 40, 100, 0.9]]))) == 1
    assert len(tr.update(np.array([[0, 0, 40, 100, 0.9], [300, 0, 340, 100, 0.9]]))) == 1
    assert len(tr.update(np.array([[0, 0, 40, 100, 0.9], [300, 0, 340, 100, 0.9]]))) == 2


def test_kalman_and_helpers():
    kf = KalmanXYAH()
    box = np.array([10.0, 20.0, 50.0, 120.0])
    z = tlbr_to_xyah(box)
    assert np.allclose(xyah_to_tlbr(z), box)
    m, c = kf.initiate(z)
    for k in range(1, 15):  # constant velocity +4 px/frame in x
        m, c = kf.predict(m, c)
        m, c = kf.update(m, c, tlbr_to_xyah(box + [4 * k, 0, 4 * k, 0]))
    assert m[4] == pytest.approx(4.0, abs=0.5)
    assert np.allclose(iou_matrix([[0, 0, 10, 10]], [[0, 0, 10, 10], [5, 0, 15, 10], [20, 20, 30, 30]]),
                       [[1.0, 1 / 3, 0.0]])
    matches, ua, ub = linear_assignment(np.array([[0.1, 0.9], [0.95, 0.99]]), 0.8)
    assert matches == [(0, 0)] and ua == [1] and ub == [1]
    assert linear_assignment(np.zeros((0, 3)), 0.5) == ([], [], [0, 1, 2])
