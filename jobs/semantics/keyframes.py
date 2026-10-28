"""Coverage-maximizing keyframe selection (PLAN 7.3 step 1).

The room is sampled with probe points (a 0.5 m floor grid at a few heights). Each frame "covers"
(probe, view-direction bin) elements it sees. Greedy max-coverage picks frames that first see
every probe, then see probes from new directions (diversity for multi-view voting and the splat).
Greedy max-coverage is a (1 - 1/e) approximation of the optimum.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

from jobs.semantics.camera import PinholeCamera
from clearance_core.schemas import PoseRecord


@dataclass
class KeyframeSelection:
    frames: list[PoseRecord]
    indices: list[int]
    probe_coverage: float          # fraction of coverable probes seen by >= 1 selected frame
    element_coverage: float        # fraction of coverable (probe, direction) elements covered
    coverable_probes: int
    stats: dict = field(default_factory=dict)


def probe_points(bounds: tuple[float, float, float, float], spacing: float = 0.5,
                 heights: Sequence[float] = (0.05, 0.8)) -> np.ndarray:
    xmin, xmax, ymin, ymax = bounds
    xs = np.arange(xmin + spacing / 2, xmax, spacing)
    ys = np.arange(ymin + spacing / 2, ymax, spacing)
    if len(xs) == 0 or len(ys) == 0:
        return np.zeros((0, 3))
    gx, gy = np.meshgrid(xs, ys)
    pts = [np.stack([gx.ravel(), gy.ravel(), np.full(gx.size, z)], axis=1) for z in heights]
    return np.concatenate(pts)


def bounds_from_poses(poses: Sequence[PoseRecord], margin: float = 2.0):
    c = np.array([[p.T_world_cam[3], p.T_world_cam[7]] for p in poses])
    return (c[:, 0].min() - margin, c[:, 0].max() + margin, c[:, 1].min() - margin, c[:, 1].max() + margin)


def visibility(poses: Sequence[PoseRecord], probes: np.ndarray, n_dir_bins: int = 8,
               near: float = 0.3, far: float = 6.0) -> tuple[np.ndarray, np.ndarray]:
    """Returns (seen (F,P) bool, dir_bin (F,P) int) of probe visibility per frame."""
    F, P = len(poses), len(probes)
    seen = np.zeros((F, P), dtype=bool)
    dbin = np.zeros((F, P), dtype=np.int16)
    for i, pose in enumerate(poses):
        cam = PinholeCamera.from_pose_record(pose)
        uv, z = cam.project(probes)
        seen[i] = cam.in_image(uv, z, near=near, far=far)
        d = cam.center[None, :2] - probes[:, :2]
        ang = np.arctan2(d[:, 1], d[:, 0])
        dbin[i] = (np.floor((ang + np.pi) / (2 * np.pi) * n_dir_bins).astype(int)) % n_dir_bins
    return seen, dbin


def select_keyframes(poses: Sequence[PoseRecord], k: int = 40, *,
                     bounds: Optional[tuple[float, float, float, float]] = None,
                     probes: Optional[np.ndarray] = None, n_dir_bins: int = 8,
                     probe_weight: float = 3.0, far: float = 6.0,
                     require_normal_tracking: bool = True) -> KeyframeSelection:
    cand = [i for i, p in enumerate(poses) if p.tracking == "normal" or not require_normal_tracking]
    if not cand:  # better a degraded selection than none
        cand = list(range(len(poses)))
    if not cand:
        return KeyframeSelection([], [], 0.0, 0.0, 0)
    sub = [poses[i] for i in cand]
    if probes is None:
        probes = probe_points(bounds or bounds_from_poses(sub))
    seen, dbin = visibility(sub, probes, n_dir_bins=n_dir_bins, far=far)
    P = len(probes)
    elem = (np.arange(P)[None, :] * n_dir_bins + dbin)  # element id per (frame, probe)
    coverable_p = seen.any(axis=0)
    all_elems = np.unique(elem[seen]) if seen.any() else np.zeros(0, dtype=np.int64)

    covered_p = np.zeros(P, dtype=bool)
    covered_e = np.zeros(P * n_dir_bins, dtype=bool)
    chosen: list[int] = []
    available = np.ones(len(sub), dtype=bool)
    while len(chosen) < min(k, len(sub)):
        new_p = (seen & ~covered_p[None, :]).sum(axis=1)
        # one element per (frame, probe), so per-frame elements are already unique
        new_e = (seen & ~covered_e[elem]).sum(axis=1)
        gain = probe_weight * new_p + new_e
        gain[~available] = -1
        best = int(np.argmax(gain))
        if gain[best] <= 0:
            # Everything coverable is covered: fill the budget with frames spread out in time.
            rest = np.flatnonzero(available)
            need = min(k, len(sub)) - len(chosen)
            if need > 0 and len(rest):
                picks = rest[np.linspace(0, len(rest) - 1, need).round().astype(int)]
                chosen.extend(int(x) for x in dict.fromkeys(picks.tolist()))
            break
        chosen.append(best)
        available[best] = False
        covered_p |= seen[best]
        covered_e[elem[best][seen[best]]] = True

    chosen = sorted(set(chosen), key=lambda f: sub[f].t)
    sel_seen = seen[chosen] if chosen else np.zeros((0, P), dtype=bool)
    pc = sel_seen.any(axis=0)
    sel_elems = np.unique(elem[chosen][sel_seen]) if chosen and sel_seen.any() else np.zeros(0)
    return KeyframeSelection(
        frames=[sub[f] for f in chosen],
        indices=[cand[f] for f in chosen],
        probe_coverage=float(pc.sum() / max(1, coverable_p.sum())),
        element_coverage=float(len(sel_elems) / max(1, len(all_elems))),
        coverable_probes=int(coverable_p.sum()),
        stats={"n_candidates": len(sub), "n_probes": P},
    )


def coverage_of(poses: Sequence[PoseRecord], probes: np.ndarray, n_dir_bins: int = 8, far: float = 6.0):
    """(probe coverage count, element coverage count) of an arbitrary frame subset."""
    if not poses:
        return 0, 0
    seen, dbin = visibility(poses, probes, n_dir_bins=n_dir_bins, far=far)
    elem = np.arange(len(probes))[None, :] * n_dir_bins + dbin
    return int(seen.any(axis=0).sum()), int(len(np.unique(elem[seen])))
