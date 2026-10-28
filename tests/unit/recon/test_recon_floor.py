import numpy as np
import pytest

from jobs.common.camera import rot_axis
from jobs.recon.fusion import fit_floor_plane, floor_alignment


def _scene(rng, tilt_deg=0.8, dz=0.04, n_floor=20000):
    floor = np.c_[rng.uniform(-3, 5, n_floor), rng.uniform(-2, 4, n_floor), rng.normal(0, 0.008, n_floor)]
    wall = np.c_[np.full(6000, 5.0) + rng.normal(0, 0.01, 6000), rng.uniform(-2, 4, 6000), rng.uniform(0, 2.5, 6000)]
    table = np.c_[rng.uniform(0, 1, 3000), rng.uniform(0, 1, 3000), np.full(3000, 0.75)]
    lip = np.c_[rng.uniform(1, 2, 500), rng.uniform(2, 2.1, 500), np.full(500, 0.02)]
    pts = np.vstack([floor, wall, table, lip])
    R = rot_axis([1, 0.4, 0], np.deg2rad(tilt_deg))
    return pts @ R.T + [0.3, -0.2, dz], R


def test_floor_fit_recovers_tilt_and_offset():
    rng = np.random.default_rng(0)
    pts, R = _scene(rng)
    n, d, frac = fit_floor_plane(pts, rng=np.random.default_rng(1))
    assert np.degrees(np.arccos(n @ R[:, 2])) < 0.1
    A = floor_alignment(n, d)
    aligned = pts @ A[:3, :3].T + A[:3, 3]
    fl = aligned[: 20000]
    assert abs(np.median(fl[:, 2])) < 0.002
    assert np.std(fl[:, 2]) < 0.01
    assert frac > 0.5
    # minimal rotation: x/y of floor points barely move
    assert np.abs(aligned[:20000, :2] - pts[:20000, :2]).max() < 0.15


def test_alignment_is_rigid_and_identity_for_level_floor():
    A = floor_alignment(np.array([0, 0, 1.0]), 0.0)
    assert np.allclose(A, np.eye(4))
    n = np.array([0.02, -0.01, 1.0])
    n /= np.linalg.norm(n)
    A = floor_alignment(n, -0.1)
    R = A[:3, :3]
    assert np.allclose(R @ R.T, np.eye(3), atol=1e-9) and np.linalg.det(R) == pytest.approx(1)
    p = 0.1 * n  # on the plane n.p - 0.1 = 0
    assert (A[:3, :3] @ p + A[:3, 3])[2] == pytest.approx(0, abs=1e-9)


def test_floor_fit_rejects_scenes_without_a_floor():
    rng = np.random.default_rng(2)
    wall = np.c_[np.zeros(5000), rng.uniform(0, 4, 5000), rng.uniform(0, 2.5, 5000)]
    with pytest.raises(ValueError):
        fit_floor_plane(wall, rng=rng)
    with pytest.raises(ValueError):
        fit_floor_plane(wall[:20], rng=rng)
