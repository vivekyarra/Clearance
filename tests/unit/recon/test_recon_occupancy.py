"""Occupancy on a tiny scene: floor + one box + a 2 cm lip seen from a ring of cameras."""
import numpy as np
import pytest

from clearance_core.grid import FREE, OCC, UNKNOWN
from jobs.common.camera import look_rotation, make_T, pixel_rays_cam
from jobs.recon.fusion import VoxelGrid, cleanup, detect_thresholds, extract_occupancy
from jobs.reference.geometry import Solid, raycast_solids

K = (80.0, 80.0, 47.5, 35.5)
W, H = 96, 72


def _render(solids, T):
    dirs, _, _ = pixel_rays_cam(K, W, H)
    t, _ = raycast_solids(T[:3, 3], dirs @ T[:3, :3].T, solids, floor=True, max_t=8.0)
    return np.where(np.isfinite(t), t, 0.0).reshape(H, W)


@pytest.fixture(scope="module")
def scene():
    box = Solid("B", "box", "box", 0.0, 0.0, 0.6, 0.4, 0.0, 0.8)
    lip = Solid("L", "box", "threshold", 0.0, -1.0, 1.0, 0.08, 0.0, 0.02)
    solids = [box, lip]
    vg = VoxelGrid.create((-2.2, -2.2), (2.2, 2.2), trunc=0.05)
    fid = 0
    for ang in np.linspace(0, 2 * np.pi, 24, endpoint=False):
        for r in (1.4, 2.0):
            p = np.array([r * np.cos(ang), r * np.sin(ang), 1.3])
            yaw = np.arctan2(-p[1], -p[0])
            T = make_T(look_rotation(yaw, -0.6), p)
            vg.integrate(_render(solids, T), K, T, fid)
            fid += 1
    g, Hm = extract_occupancy(vg)
    return g, Hm, vg


def test_box_occupied_floor_free_far_unknown(scene):
    g, _, _ = scene
    r, c = g.world_to_cell(0.0, 0.0)
    assert g.data[r, c] == OCC
    # box footprint edges within one cell
    for x, y in [(0.27, 0.0), (-0.27, 0.0), (0.0, 0.17), (0.0, -0.17)]:
        r, c = g.world_to_cell(x, y)
        assert g.data[r, c] == OCC
    for x, y in [(0.36, 0.0), (0.0, 0.26), (0.8, 0.8), (-0.8, 0.5)]:
        r, c = g.world_to_cell(x, y)
        assert g.data[r, c] == FREE, (x, y)
    r, c = g.world_to_cell(2.15, 2.15)
    assert g.data[r, c] == UNKNOWN


def test_low_lip_is_not_an_obstacle_and_is_detected(scene):
    g, Hm, _ = scene
    r, c = g.world_to_cell(0.0, -1.0)
    assert g.data[r, c] == FREE
    ths, noise = detect_thresholds(Hm, g)
    near = [t for t in ths if abs(np.mean([p[1] for p in t["segment"]]) + 1.0) < 0.1]
    assert len(near) == 1
    assert near[0]["height_m"] == pytest.approx(0.02, abs=0.007)
    assert near[0]["length_m"] == pytest.approx(1.0, abs=0.15)
    assert noise < 0.005


def test_requires_two_observations(scene):
    _, _, vg = scene
    g1, _ = extract_occupancy(vg, min_frames=10_000)
    assert (g1.data == OCC).sum() == 0 and (g1.data == FREE).sum() == 0


def test_cleanup_rules():
    d = np.full((20, 20), FREE, np.uint8)
    d[2, 2] = OCC                       # speck -> free
    d[8:14, 8:14] = OCC
    d[10:12, 10:12] = UNKNOWN           # pocket inside obstacle -> occupied
    d[3:5, 15:17] = UNKNOWN             # small hole in open floor -> free
    d[0:3, 0] = UNKNOWN                 # touches the border -> stays unknown
    out = cleanup(d)
    assert out[2, 2] == FREE
    assert (out[10:12, 10:12] == OCC).all()
    assert (out[3:5, 15:17] == FREE).all()
    assert (out[0:3, 0] == UNKNOWN).all()
    assert (out[8:14, 8:14] == OCC).all()
