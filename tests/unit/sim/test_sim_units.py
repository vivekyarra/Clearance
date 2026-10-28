"""Unit tests: rasterization, c-space, overrides, shards, CLI, live session."""
import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from clearance_core import storage
from clearance_core.grid import FREE, OCC, UNKNOWN
from clearance_core.schemas import SimBatch, Task
from jobs.sim.api import LiveSession, merge_shards, rasterize_twin, run_batch, run_shard, wilson
from jobs.sim.batch import make_shard_specs, run_shards_local
from jobs.sim.cspace import build_cspace, make_kernels
from jobs.sim.geometry import footprint_kernel
from jobs.sim.twin_builder import RoomCanvas, box, make_twin, rect_robot

REPO = Path(__file__).resolve().parents[3]


def simple_twin(**kw):
    c = RoomCanvas(6.0, 4.0, res=0.025)
    return make_twin(c, "sites/unit/room", [("A", 1.0, 2.0, 0.0, "dock"), ("B", 5.0, 2.0, 0.0, "dest")], **kw)


def cell(grid, x, y):
    r, c = grid.world_to_cell(x, y)
    return grid.data[int(r), int(c)]


def test_primitive_burned_with_yaw_and_keepout():
    t = simple_twin(primitives=[box("T1", 3.0, 2.0, 1.0, 0.2, yaw=math.pi / 2)],
                    keepouts=[("K1", [(0.5, 3.0), (1.5, 3.0), (1.5, 3.5), (0.5, 3.5)])])
    g = rasterize_twin(t)
    assert cell(g, 3.0, 2.4) == OCC   # long axis rotated onto y
    assert cell(g, 3.4, 2.0) == FREE
    assert cell(g, 1.0, 3.25) == OCC  # keep-out zone blocks the robot


def test_overrides_move_remove_add():
    t = simple_twin(primitives=[box("T1", 3.0, 2.0, 0.4, 0.4), box("T2", 2.0, 1.0, 0.4, 0.4)])
    g = rasterize_twin(t, overrides={"move": {"T1": {"dx": 1.0, "dy": 0.0, "dyaw": 0.0}}, "remove": ["T2"],
                                     "add_boxes": [{"id": "N1", "pose": {"xyz": (1.0, 3.0, 0.0)}, "size": (0.3, 0.3, 0.5)}]})
    assert cell(g, 3.0, 2.0) == FREE      # old footprint cleared
    assert cell(g, 4.0, 2.0) == OCC
    assert cell(g, 2.0, 1.0) == FREE
    assert cell(g, 1.0, 3.0) == OCC
    with pytest.raises(ValueError, match="unknown object"):
        rasterize_twin(t, overrides={"remove": ["nope"]})


def test_bounds_dilate_and_erode_keep_thin_walls():
    c = RoomCanvas(4.0, 4.0, res=0.025)
    c.rect(1.9, 0.0, 2.1, 3.0)            # 8-cell partition
    c.rect(2.9, 0.0, 2.95, 3.0)           # 2-cell partition, thinner than 2 x p90
    t = make_twin(c, "sites/unit/bounds", [("A", 1.0, 1.0, 0.0, "dock"), ("B", 3.5, 1.0, 0.0, "dest")], p90=0.05)
    nom = rasterize_twin(t)
    con = rasterize_twin(t, "conservative")
    opt = rasterize_twin(t, "optimistic")
    n_nom, n_con, n_opt = [(g.data != FREE).sum() for g in (nom, con, opt)]
    assert n_con > n_nom > n_opt
    assert cell(con, 1.86, 1.0) == OCC and cell(nom, 1.86, 1.0) == FREE
    assert cell(opt, 1.93, 1.0) == FREE and cell(opt, 2.0, 1.0) == OCC
    rows = opt.data[(opt.data.shape[0] // 2), :]
    cols = [int(opt.world_to_cell(x, 2.0)[1]) for x in (2.88, 2.97)]
    assert (rows[cols[0]:cols[1] + 1] != FREE).any()  # thin wall did not vanish


def test_unknown_stays_unknown_and_blocks():
    c = RoomCanvas(4.0, 4.0, res=0.025)
    c.rect(1.0, 1.0, 2.0, 2.0, value=UNKNOWN)
    t = make_twin(c, "sites/unit/unk", [("A", 0.5, 0.5, 0.0, "dock"), ("B", 3.5, 3.5, 0.0, "dest")])
    g = rasterize_twin(t)
    assert cell(g, 1.5, 1.5) == UNKNOWN and g.blocked()[g.world_to_cell(1.5, 1.5)]


def test_kernel_orientation_matches_cspace_convention():
    res = 0.05
    fp = np.array([[0.5, 0.1], [-0.1, 0.1], [-0.1, -0.1], [0.5, -0.1]])  # long nose forward (+x)
    blocked = np.zeros((41, 41), np.uint8)
    blocked[20, 28] = 1                                                    # obstacle 0.4 m east of center
    cs = build_cspace(blocked, make_kernels(fp, res))
    assert cs[0, 20, 20] == 1      # facing east: nose hits it
    assert cs[18, 20, 20] == 0     # facing west: clear
    assert cs[9, 20, 20] == 0      # facing north: clear
    k, h = footprint_kernel(fp, 0.0, res)
    assert k[h, h + 10] == 1 and k[h, h - 10] == 0


def test_wilson_interval():
    lo, hi = wilson(93, 100)
    assert lo == pytest.approx(0.8625, abs=0.002) and hi == pytest.approx(0.9657, abs=0.002)
    assert wilson(0, 10)[0] == 0.0 and wilson(10, 10)[1] == 1.0


def peds_twin():
    zones = [("Z1", [(0.3, 0.3), (1.0, 0.3), (1.0, 3.7), (0.3, 3.7)]),
             ("Z2", [(5.0, 0.3), (5.7, 0.3), (5.7, 3.7), (5.0, 3.7)])]
    return simple_twin(zones=zones, od_pairs=[("Z1", "Z2", 1.0), ("Z2", "Z1", 1.0)],
                       persons_per_min={"quiet": 4, "normal": 20, "peak": 40})


def test_shards_merge_equals_single_run():
    twin, robot, task = peds_twin(), rect_robot(0.5, 0.6), Task(sequence=["A", "B"])
    whole = run_batch(twin, robot, task, "normal", "nominal", 5, 3, out_prefix="analyses/u1/batches/")
    specs = make_shard_specs(twin, robot, task, "normal", "nominal", 5, 3, shard_size=2,
                             out_prefix="analyses/u2/batches/", batch_id=whole.batch_id)
    assert len(specs) == 3
    specs = [json.loads(json.dumps(s)) for s in specs]  # must survive a JSON round trip
    parts = [run_shard(s) for s in reversed(specs)]
    merged = merge_shards([json.loads(json.dumps(p)) for p in parts])
    assert merged.results == whole.results
    a = (storage.DATA / whole.episodes_uri).read_bytes()
    b = (storage.DATA / merged.episodes_uri).read_bytes()
    assert a == b
    assert np.array_equal(np.load(storage.DATA / whole.bottleneck_uri), np.load(storage.DATA / merged.bottleneck_uri))
    eps = [json.loads(l)["episode"] for l in a.decode().splitlines()]
    assert eps == sorted(eps) and set(eps) == set(range(5))
    with pytest.raises(ValueError):
        merge_shards(parts[:2])


def test_density_and_zone_scale_overrides():
    twin, robot, task = peds_twin(), rect_robot(0.5, 0.6), Task(sequence=["A", "B"])
    b = run_batch(twin, robot, task, "none", "nominal", 2, 0, overrides={"density": "peak"}, record_episodes=False)
    assert b.density == "peak"
    off = run_batch(twin, robot, task, "peak", "nominal", 2, 0, overrides={"ped_zone_scale": {"Z1": 0.0}},
                    record_episodes=False, out_prefix="analyses/u3/batches/")
    eps = storage.read_json(f"analyses/u3/batches/{off.batch_id}.summary.json")["episodes"]
    assert all(e["peds_peak"] == 0 for e in eps)
    assert off.results.success_rate == 1.0
    bott = np.load(storage.DATA / off.bottleneck_uri)
    assert bott.shape == (160, 240) and bott.dtype == np.float32


def test_cli_shard_roundtrip(tmp_path):
    twin, robot, task = simple_twin(), rect_robot(0.5, 0.6), Task(sequence=["A", "B"])
    spec = make_shard_specs(twin, robot, task, "none", "nominal", 2, 0, record_episodes=False,
                            inline_occupancy=True)[0]
    sp, out = tmp_path / "spec.json", tmp_path / "out.json"
    sp.write_text(json.dumps(spec))
    empty = tmp_path / "empty_store"
    empty.mkdir()
    # the spec is self-contained: the worker's storage root does not hold the twin
    env = {**__import__("os").environ, "CLEARANCE_DATA": str(empty), "PYTHONPATH": str(REPO)}
    r = subprocess.run([sys.executable, "-m", "jobs.sim.cli", "shard", str(sp), str(out)], cwd=REPO, env=env,
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr
    res = json.loads(out.read_text())
    batch = merge_shards([res])
    assert isinstance(batch, SimBatch) and batch.results.n_episodes == 2 and batch.results.success_rate == 1.0


def test_local_process_pool_matches_sequential():
    twin, robot, task = simple_twin(), rect_robot(0.5, 0.6), Task(sequence=["A", "B"])
    specs = make_shard_specs(twin, robot, task, "none", "nominal", 4, 9, shard_size=2, record_episodes=False)
    seq = merge_shards(run_shards_local(specs, 1))
    par = merge_shards(run_shards_local(specs, 2))
    assert seq.results == par.results


def test_live_session_frames_and_joystick():
    twin = peds_twin()
    s = LiveSession(twin, rect_robot(0.5, 0.6), Task(sequence=["A", "B"]), "quiet", 1)
    s.set_joystick(10.0, 0.0)
    assert math.hypot(*s.joy) == pytest.approx(1.4)
    x0 = s.sim.crowd.px[0]
    for _ in range(20):
        fr = s.step()
    assert fr["t"] == pytest.approx(1.0)
    assert set(fr) >= {"t", "robot", "peds", "events"}
    assert set(fr["robot"]) == {"x", "y", "yaw", "v", "state"}
    assert s.sim.crowd.px[0] != x0 or s.sim.crowd.px[0] > 5.5
    s.set_joystick(float("nan"), 0.0)
    assert s.joy == (0.0, 0.0)
    for _ in range(400):
        s.step()  # loops the mission without ending
    assert s.sim.legs_done >= 1
