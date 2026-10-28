"""Coordinate frames end to end, T2 alignment fail-safe behaviour, tilted floors, memory budget."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

from clearance_core.grid import FREE, OCC, UNKNOWN, Grid
from clearance_core.schemas import PoseRecord
from jobs.common.camera import backproject, look_rotation, make_T, pixel_rays_cam, project, rot_axis
from jobs.recon import pipeline
from jobs.recon.depth import MonoAlignedDepth
from jobs.recon.fusion import VoxelGrid, extract_occupancy
from jobs.recon.measure import measure_gap
from jobs.recon.pipeline import reconstruct, unknown_regions
from jobs.reference.geometry import Solid
from jobs.reference.render import render_depth

K_NS = (210.0, 160.0, 77.3, 61.8)  # non-square pixels, off-centre principal point
W, H = 160, 120


def _look_at(p, target):
    d = np.asarray(target, float) - p
    return look_rotation(np.arctan2(d[1], d[0]), np.arctan2(d[2], np.hypot(d[0], d[1])))


def test_single_pixel_lands_in_analytic_world_point_and_cell():
    p = np.array([1.3, -0.4, 1.2])
    R = look_rotation(0.7, -0.6, 0.1)
    T = make_T(R, p)
    u, v, d = 37, 81, 2.1
    depth = np.zeros((H, W))
    depth[v, u] = d
    pw = backproject(depth, K_NS, T)
    assert pw.shape == (1, 3)
    # analytic: camera ray (x right, y up, looking along -z) in world
    ray_c = np.array([(u - K_NS[2]) / K_NS[0], -(v - K_NS[3]) / K_NS[1], -1.0]) * d
    expect = R @ ray_c + p
    assert np.allclose(pw[0], expect, atol=1e-12)
    # and it projects back to the same pixel
    uu, vv, dd, ok = project(pw, K_NS, T, W, H)
    assert ok[0] and abs(uu[0] - u) < 1e-9 and abs(vv[0] - v) < 1e-9 and abs(dd[0] - d) < 1e-9
    # grid convention: row 0 = max y, origin = min-x/min-y corner
    g = Grid(np.zeros((40, 60), np.uint8), 0.05, (-1.0, -2.0))
    r, c = g.world_to_cell(expect[0], expect[1])
    assert c == int(np.floor((expect[0] + 1.0) / 0.05)) and r == 39 - int(np.floor((expect[1] + 2.0) / 0.05))
    x, y = g.cell_to_world(r, c)
    assert abs(x - expect[0]) <= 0.025 and abs(y - expect[1]) <= 0.025


def test_fused_box_occupies_its_cells_in_grid_convention():
    """Cameras with non-square pixels (landscape and portrait) circle an off-centre rotated box; the fused
    occupancy must put the box exactly where it is in the site frame (catches transposed / flipped grids)."""
    box = Solid("B", "box", "block", 1.7, 0.6, 0.5, 0.3, 0.0, 1.0, yaw=0.3)
    scene = SimpleNamespace(solids=[box])
    vg = VoxelGrid.create(np.array([0.2, -0.9]), np.array([3.2, 2.1]))
    k = 0
    for i, a in enumerate(np.linspace(0, 2 * np.pi, 24, endpoint=False)):
        p = np.array([1.7 + 1.3 * np.cos(a), 0.6 + 1.3 * np.sin(a), 1.25])
        T = make_T(_look_at(p, (1.7, 0.6, 0.2)), p)
        portrait = i % 2 == 1
        K, w, h = ((K_NS[1], K_NS[0], K_NS[3], K_NS[2]), H, W) if portrait else (K_NS, W, H)
        depth, _ = render_depth(scene, T, K, w, h)
        vg.integrate(depth.astype(np.float32), K, T, k)
        k += 1
    g, _ = extract_occupancy(vg)
    rr, cc = np.mgrid[0:g.shape[0], 0:g.shape[1]]
    x, y = g.cell_to_world(rr, cc)
    inside = box.contains_xy(x, y)
    # erode / dilate the true footprint by one cell to allow for the cell-boundary decision
    near = np.zeros_like(inside)
    for dx in (-0.03, 0, 0.03):
        for dy in (-0.03, 0, 0.03):
            near |= box.contains_xy(x + dx, y + dy)
    core = np.ones_like(inside)
    for dx in (-0.03, 0, 0.03):
        for dy in (-0.03, 0, 0.03):
            core &= box.contains_xy(x + dx, y + dy)
    assert (g.data[core] == OCC).mean() > 0.97
    assert not (g.data[~near] == OCC).any()
    r0, c0 = g.world_to_cell(1.7, 0.6)
    assert g.data[r0, c0] == OCC
    r1, c1 = g.world_to_cell(1.7, 1.6)  # floor beyond the box on +y
    assert g.data[r1, c1] == FREE


# ---------------------------------------------------------------------------------------------- T2
class _Backend:
    name = "scripted"

    def __init__(self, rel):
        self.rel = rel

    def predict(self, image_bgr, frame_name):
        return self.rel


def _mono_setup(fp_world, rel, T, K, w=256, h=192):
    pose = PoseRecord(t=0, frame="000000.jpg", T_world_cam=T.ravel().tolist(), K=list(K), w=w, h=h)
    cap = SimpleNamespace(poses=[pose], feature_points={"000000.jpg": fp_world}, T=lambda i: T,
                          dir_of=lambda key: None)
    return MonoAlignedDepth(cap, _Backend(rel), max_depth_m=6.0)


def _floor_scene(seed):
    rng = np.random.default_rng(seed)
    K = (190.0, 190.0, 127.5, 95.5)
    T = make_T(look_rotation(0.3, -0.55), [0.0, 0.0, 1.4])
    depth, _ = render_depth(SimpleNamespace(solids=[]), T, K, 256, 192)
    return rng, K, T, depth


def test_t2_feature_points_on_narrow_depth_band_do_not_extrapolate():
    rng, K, T, depth = _floor_scene(0)
    dirs, uu, vv = pixel_rays_cam(K, 256, 192)
    dflat = depth.ravel()
    band = np.flatnonzero((dflat > 1.95) & (dflat < 2.05))
    sel = rng.choice(band, 150, replace=False)
    pc = dirs[sel] * (dflat[sel] * (1 + rng.normal(0, 0.01, sel.size)))[:, None]
    fp = pc @ T[:3, :3].T + T[:3, 3]
    inv = np.where(depth > 0, 1 / np.maximum(depth, 1e-3), 0)
    rel = 2.0 * inv * (1 + 0.004 * rng.normal(size=inv.shape)) + 0.3
    rel = rel * (1 + 0.25 * (inv - 0.5) ** 2)  # shape error away from the anchored band
    fr = _mono_setup(fp, rel, T, K).get(0)
    assert fr is not None
    ok = fr.depth > 0
    assert ok.any()
    err = np.abs(fr.depth[ok] - depth[ok]) / depth[ok]
    assert np.percentile(err, 99) < 0.06, np.percentile(err, 99)
    assert not ok[depth < 1.2].any() and not ok[depth > 3.5].any()  # far outside the anchored band


@pytest.mark.parametrize("seed", range(60))
def test_t2_random_feature_points_drop_the_frame(seed):
    rng, K, T, depth = _floor_scene(seed)
    fp = rng.uniform([-3, -3, -0.5], [3, 3, 2.5], (rng.integers(10, 300), 3))
    inv = np.where(depth > 0, 1 / np.maximum(depth, 1e-3), 0)
    src = _mono_setup(fp, 2 * inv + 0.1, T, K)
    assert src.get(0) is None and src.stats["dropped_fit"] + src.stats["dropped_no_points"] == 1


# ---------------------------------------------------------------------------------------------- floors
def _tilt_poses(cap, deg, axis=(1.0, 0.3, 0.0), dz=-1.1):
    G = make_T(rot_axis(axis, np.deg2rad(deg)), [0.4, -0.2, dz])
    lines = (cap / "poses.jsonl").read_text(encoding="utf-8").splitlines()
    out = []
    for s in lines:
        r = json.loads(s)
        r["T_world_cam"] = (G @ np.asarray(r["T_world_cam"]).reshape(4, 4)).ravel().tolist()
        out.append(json.dumps(r))
    (cap / "poses.jsonl").write_text("\n".join(out) + "\n", encoding="utf-8")
    return G


@pytest.mark.parametrize("deg", [5.0, 12.0])
def test_tilted_world_frame_is_levelled(small_capture, deg):
    G = _tilt_poses(small_capture, deg)
    r = reconstruct(str(small_capture))
    A = r["floor_align"]
    # A @ G must be a pure yaw + translation (floor level again)
    M = A @ G
    assert abs(M[2, 2] - 1) < 2e-4 and abs(M[2, 3]) < 0.03  # the render itself adds 1.5 cm
    assert r["stats"]["camera_height_median_m"] == pytest.approx(1.40, abs=0.05)


def test_tilt_beyond_floor_search_fails_loudly(small_capture):
    _tilt_poses(small_capture, 25.0)
    with pytest.raises(ValueError):
        reconstruct(str(small_capture))


# ---------------------------------------------------------------------------------------------- scale
def test_voxel_budget_refuses_instead_of_exhausting_memory(small_capture, monkeypatch):
    assert VoxelGrid.estimate_bytes((0, 0), (60, 60)) > 8 * 2 ** 30
    monkeypatch.setattr(pipeline, "MAX_VOXEL_BYTES", 10 * 2 ** 20)
    with pytest.raises(ValueError, match="budget"):
        reconstruct(str(small_capture))


def test_estimate_bytes_matches_allocation():
    lo, hi = np.array([-0.3, 0.1]), np.array([2.2, 1.4])
    vg = VoxelGrid.create(lo, hi)
    arrays = sum(a.nbytes for a in (vg.tsdf, vg.weight, vg.nframes, vg.last, vg.seen_through, vg.last_free,
                                    vg.free_after))
    est = VoxelGrid.estimate_bytes(lo, hi)
    assert arrays <= est <= 2 * arrays


# ---------------------------------------------------------------------------------------------- unknown
def test_gap_ended_by_unknown_is_flagged_and_regions_listed():
    data = np.full((40, 80), FREE, np.uint8)
    data[:, :10] = OCC
    data[:, 70:] = OCC
    data[15:25, 40:50] = UNKNOWN  # floor hidden behind a chair
    g = Grid(data, 0.025, (0.0, 0.0))
    y = g.cell_to_world(20, 0)[1]
    m = measure_gap(g, (0.3, y), (0.9, y))
    assert m["limited_by"] == ("occupied", "unknown") and m["value_m"] == pytest.approx(0.75, abs=0.03)
    blocked = measure_gap(g, (1.0, y), (1.3, y))
    assert blocked["value_m"] == 0 and blocked["limited_by"] == ("unknown", "unknown")
    regs = unknown_regions(g)
    assert len(regs) == 1 and regs[0]["area_m2"] == pytest.approx(100 * 0.025 ** 2)
    cx, cy = regs[0]["centroid_xy"]
    assert 1.0 < cx < 1.25 and abs(cy - y) < 0.05 and regs[0]["next_to_free"]
