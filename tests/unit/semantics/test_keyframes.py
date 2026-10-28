import math

import numpy as np

from clearance_core.schemas import PoseRecord
from jobs.semantics.camera import PinholeCamera, look_at_arkit
from jobs.semantics.keyframes import coverage_of, probe_points, select_keyframes

K = [400.0, 400.0, 320.0, 240.0]
BOUNDS = (0.0, 6.0, 0.0, 5.0)


def walk_poses(n=300, seed=0):
    """A scanner walking a loop inside a 6 x 5 m room, looking outward-ish with jitter, plus a long
    stretch staring at one corner (redundant frames the selector should not waste budget on)."""
    rng = np.random.default_rng(seed)
    poses = []
    for i in range(n):
        if i < n * 0.6:
            a = 2 * math.pi * i / (n * 0.6)
            eye = (3 + 1.5 * math.cos(a), 2.5 + 1.2 * math.sin(a), 1.4)
            look = a + rng.normal(0, 0.4) + (math.pi if i % 2 else 0.0)
            tgt = (eye[0] + 3 * math.cos(look), eye[1] + 3 * math.sin(look), 0.3)
        else:  # dwelling at a corner
            eye = (1.0 + rng.normal(0, 0.02), 1.0 + rng.normal(0, 0.02), 1.4)
            tgt = (0.2, 0.2, 0.3)
        T = look_at_arkit(eye, tgt)
        poses.append(PoseRecord(t=i * 0.1, frame=f"{i:06d}.jpg", T_world_cam=T.ravel().tolist(), K=K, w=640, h=480))
    return poses


def test_selection_covers_room_better_than_uniform():
    poses = walk_poses()
    probes = probe_points(BOUNDS)
    sel = select_keyframes(poses, k=40, bounds=BOUNDS)
    assert len(sel.frames) == 40
    assert [f.t for f in sel.frames] == sorted(f.t for f in sel.frames)
    assert sel.probe_coverage >= 0.99
    all_p, all_e = coverage_of(poses, probes)
    g_p, g_e = coverage_of(sel.frames, probes)
    uni = [poses[i] for i in np.linspace(0, len(poses) - 1, 40).round().astype(int)]
    u_p, u_e = coverage_of(uni, probes)
    assert g_p >= u_p and g_e > u_e
    assert g_e >= 0.85 * all_e  # most (probe, direction) pairs seen by the full sequence
    # the corner dwell (frames 180..299) is nearly redundant: only a few picks land there
    assert sum(1 for i in sel.indices if i >= 180) <= 6


def test_greedy_matches_bruteforce_on_small_problem():
    poses = walk_poses(40, seed=2)[:24]
    probes = probe_points(BOUNDS, spacing=1.0)
    sel = select_keyframes(poses, k=3, probes=probes, probe_weight=1.0, n_dir_bins=1)
    best = 0
    import itertools
    for comb in itertools.combinations(range(len(poses)), 3):
        best = max(best, coverage_of([poses[i] for i in comb], probes, n_dir_bins=1)[0])
    got = coverage_of(sel.frames, probes, n_dir_bins=1)[0]
    assert got >= (1 - 1 / math.e) * best


def test_edge_cases():
    poses = walk_poses(30)
    # fewer frames than the budget -> all usable frames
    sel = select_keyframes(poses, k=100, bounds=BOUNDS)
    assert len(sel.frames) == 30
    # tracking-limited frames are skipped while normal ones exist
    lim = [p.model_copy(update={"tracking": "limited"}) if i % 2 else p for i, p in enumerate(poses)]
    sel = select_keyframes(lim, k=10, bounds=BOUNDS)
    assert all(f.tracking == "normal" for f in sel.frames)
    # all limited -> degraded but non-empty selection
    all_lim = [p.model_copy(update={"tracking": "limited"}) for p in poses]
    assert len(select_keyframes(all_lim, k=5, bounds=BOUNDS).frames) == 5
    # identical poses: the budget is still filled with distinct frames
    same = [poses[0].model_copy(update={"t": i, "frame": f"{i}.jpg"}) for i in range(20)]
    sel = select_keyframes(same, k=5, bounds=BOUNDS)
    assert len({f.frame for f in sel.frames}) == 5
    assert select_keyframes([], k=5).frames == []


def test_camera_projection_roundtrip():
    T = look_at_arkit((1, 2, 1.5), (3, 2, 0))
    cam = PinholeCamera.from_arkit(T, K, 640, 480)
    target = np.array([[3.0, 2.0, 0.0]])
    uv, z = cam.project(target)
    assert np.allclose(uv, [[320, 240]], atol=1e-9) and z[0] > 0
    back = cam.backproject(uv, z)
    assert np.allclose(back, target, atol=1e-9)
    # ARKit camera looks along its -Z axis
    assert np.allclose(cam.forward, -T[:3, 2])
