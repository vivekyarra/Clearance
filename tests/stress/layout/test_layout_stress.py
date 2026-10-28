"""Optimizer stress: every recommended change is re-checked by an independent validator
(layout_check.py) on randomized rooms with rotated and wall-hugging objects; budget caps are exact;
empty and huge candidate sets; a 100% baseline yields nothing; executor runs are identical to
serial runs; one small run against the real simulator on the reference cafe."""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest
from scipy import ndimage
from shapely.geometry import Point

from clearance_core.schemas import BatchResults, Pose2, Primitive, SimBatch
from jobs.optimizer.candidates import bottleneck_peaks, generate_moves
from jobs.optimizer.constraints import ConstraintChecker
from jobs.optimizer.search import search_layout

from layout_check import Validator
from layout_rooms import Room, bottleneck_near, robot, task

REPO = Path(__file__).resolve().parents[3]


def _wilson(k, n, z=1.96):
    ph = k / n
    d = 1 + z * z / n
    c = (ph + z * z / (2 * n)) / d
    h = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


class RoomRunner:
    """run_batch-compatible: success probability grows with the free distance at the pinch point."""

    def __init__(self, room: Room, pinch_xy, p_floor=0.05, p_cap=0.97):
        self.room, self.pinch, self.p_floor, self.p_cap = room, pinch_xy, p_floor, p_cap

    def __call__(self, twin, robot_, task_, density, bound, n_episodes, seed_base, *, overrides=None,
                 record_episodes=True, out_prefix=None):
        g = self.room.rasterize(twin, bound, overrides=overrides)
        edt = ndimage.distance_transform_edt(g.data == 255) * g.res
        r, c = g.world_to_cell(*self.pinch)
        p = float(np.clip((edt[int(r), int(c)] - 0.15) / 0.4, self.p_floor, self.p_cap))
        k = int((np.random.default_rng(seed_base).random(n_episodes) < p).sum())
        bid = "sb_" + hashlib.sha1(json.dumps([overrides, n_episodes, seed_base, bound], sort_keys=True)
                                   .encode()).hexdigest()[:10]
        res = BatchResults(success_rate=k / n_episodes, ci95=_wilson(k, n_episodes), n_success=k,
                           n_episodes=n_episodes)
        return SimBatch(batch_id=bid, twin_id=twin.twin_id, twin_version=twin.version, robot_id=robot_.robot_id,
                        task=task_, density=density, geometry_bound=bound, n_episodes=n_episodes,
                        seed_base=seed_base, results=res)


class CountingRunner(RoomRunner):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.calls, self.episodes = 0, 0

    def __call__(self, *a, **kw):
        self.calls += 1
        self.episodes += a[5]
        return super().__call__(*a, **kw)


def _setup(seed, with_exit=True, n_objects=8):
    room = Room(seed, n_objects=n_objects)
    tw = room.twin(with_exit=with_exit)
    rng = np.random.default_rng(seed)
    movable = [p.id for p in room.prims if p.movable]
    oid = movable[int(rng.integers(len(movable)))]
    ch = ConstraintChecker(tw, robot(), room.rasterize)
    bn = bottleneck_near(room, ch.grid, oid, rng)
    peak = bottleneck_peaks(bn, ch.grid, k=1)[0]
    return room, tw, bn, (peak.x, peak.y), ch


# ---------- every admissible move passes the independent re-check ----------
@pytest.mark.parametrize("seed", [0, 1, 5, 7])
def test_generated_moves_pass_independent_checks(seed):
    room = Room(seed)
    for with_exit in (True, False):
        tw = room.twin(with_exit=with_exit)
        ch = ConstraintChecker(tw, robot(), room.rasterize)
        v = Validator(tw, robot(), room.rasterize)
        rng = np.random.default_rng(seed)
        n = 0
        for p in room.prims:
            if not p.movable:
                assert not ch.check({p.id: (0.05, 0.0, 0.0)}).ok
                continue
            peaks = bottleneck_peaks(bottleneck_near(room, ch.grid, p.id, rng), ch.grid)
            moves, _ = generate_moves(ch, p.id, peaks, max_moves=12)
            for m in moves:
                n += 1
                viol = v.violations({"move": {p.id: m}})
                assert not viol, (seed, with_exit, p.id, p.type, p.pose.yaw, m, viol)
        assert n > 0


def test_rotations_near_walls_never_enter_the_wall():
    # rectangles hugging each wall (0..3 cm gap), rotated in place: corners swing into the wall
    room = Room(3, n_objects=0)
    prims = []
    for k, (x, y, yaw) in enumerate([(0.31, 1.5, 0.0), (room.Wm - 0.31, 1.5, 0.0), (2.0, 0.26, math.pi / 2),
                                     (2.5, room.Hm - 0.27, math.pi / 2)]):
        prims.append(Primitive(id=f"R{k}", type="box", label="table", pose=Pose2(xyz=(x, y, 0.375), yaw=yaw),
                               size=(0.6, 1.0, 0.75), movable=True, source="reference"))
    room.prims = prims
    tw = room.twin()
    tw.keepout_zones = []
    ch = ConstraintChecker(tw, robot(), room.rasterize)
    v = Validator(tw, robot(), room.rasterize)
    for p in prims:
        for dyaw in (math.radians(15), math.radians(-15), math.radians(5)):
            for dx, dy in ((0, 0), (0.05, 0), (-0.05, 0), (0, 0.05), (0, -0.05)):
                res = ch.check({p.id: (dx, dy, dyaw)})
                viol = v.violations({"move": {p.id: {"dx": dx, "dy": dy, "dyaw": dyaw}}})
                if res.ok:
                    assert not viol, (p.id, dx, dy, dyaw, viol)
                assert res.ok or res.reason in ("overlap:wall", "outside_floor") or res.reason.startswith(
                    ("egress", "waypoint")), res.reason
        # a 15 degree turn of a 1.0 m long table touching the wall cannot be admissible
        assert not ch.check({p.id: (0.0, 0.0, math.radians(15))}).ok


@pytest.mark.parametrize("seed", [2, 4, 9])
def test_search_results_pass_independent_checks(seed):
    room, tw, bn, pinch, _ = _setup(seed)
    runner = RoomRunner(room, pinch)
    budget = {"screen_episodes": 20, "confirm_episodes": 60, "max_moves_per_object": 10, "top_k_confirm": 4}
    out = search_layout(tw, robot(), task(), bn, budget=budget, runner=runner, rasterizer=room.rasterize, seed=seed)
    v = Validator(tw, robot(), room.rasterize)
    for r in out["results"]:
        assert r["uplift"] > 0 and r["after"].success_rate > r["before"].success_rate
        assert not v.violations(r["change"]), r["change"]
        assert all(room.prims[int(o[1:])].movable for o in r["objects"])


# ---------- budgets ----------
@pytest.mark.parametrize("cap", [{"max_evaluations": 1}, {"max_evaluations": 2}, {"max_evaluations": 5},
                                 {"max_evaluations": 9}, {"max_evaluations": 17}, {"max_episodes": 50},
                                 {"max_episodes": 200}, {"max_episodes": 701},
                                 {"max_episodes": 1500, "max_evaluations": 12}])
def test_budget_caps_are_exact_upper_bounds(cap):
    room, tw, bn, pinch, _ = _setup(4)
    runner = CountingRunner(room, pinch)
    budget = {"screen_episodes": 10, "confirm_episodes": 50, "max_moves_per_object": 8, **cap}
    out = search_layout(tw, robot(), task(), bn, budget=budget, runner=runner, rasterizer=room.rasterize)
    st = out["stats"]
    assert (st["runner_calls"], st["episodes"]) == (runner.calls, runner.episodes)
    assert runner.calls <= cap.get("max_evaluations", math.inf)
    assert runner.episodes <= cap.get("max_episodes", math.inf)


# ---------- degenerate candidate sets / baselines ----------
def test_empty_bottleneck_and_no_movables_run_nothing():
    room, tw, bn, pinch, _ = _setup(1)
    runner = CountingRunner(room, pinch)
    out = search_layout(tw, robot(), task(), np.zeros_like(bn), runner=runner, rasterizer=room.rasterize)
    assert out["results"] == [] and runner.calls == 0 and out["peaks"] == []
    for p in tw.primitives:
        p.movable = False
    out = search_layout(tw, robot(), task(), bn, runner=runner, rasterizer=room.rasterize,
                        hint_object_ids=[p.id for p in tw.primitives] + ["NOPE"])
    assert out["results"] == [] and runner.calls == 0 and "NOPE" in out["skipped_hints"]
    nan = bn.astype(float)
    nan[:] = np.nan
    assert search_layout(tw, robot(), task(), nan, runner=runner, rasterizer=room.rasterize)["results"] == []


def test_full_success_baseline_returns_no_change():
    room, tw, bn, pinch, _ = _setup(4)
    runner = CountingRunner(room, pinch, p_floor=1.0, p_cap=1.0)
    out = search_layout(tw, robot(), task(), bn, runner=runner, rasterizer=room.rasterize,
                        budget={"screen_episodes": 10, "confirm_episodes": 40, "max_moves_per_object": 5})
    assert out["results"] == []
    assert out["stats"]["confirmed"] == 0 and out["stats"]["verdict_runs"] == 0  # no wasted confirm runs


def test_huge_candidate_set_is_bounded():
    room = Room(11, n_objects=0)
    rng = np.random.default_rng(0)
    prims = []
    for i in range(int(room.Wm / 0.3) - 1):  # rows of stools: ~ 200 objects
        for j in range(int(room.Hm / 0.3) - 1):
            x, y = 0.3 * (i + 1), 0.3 * (j + 1)
            if room.column.distance(Point(x, y)) < 0.3:
                continue
            prims.append(Primitive(id=f"S{len(prims)}", type="cylinder", label="stool",
                                   pose=Pose2(xyz=(x, y, 0.25)), size=(0.16, 0.16, 0.5), movable=True,
                                   source="reference"))
    room.prims = prims
    tw = room.twin()
    tw.keepout_zones = []
    tw.waypoints = tw.waypoints[:2]  # no egress pair: measures the geometry checks alone
    assert len(prims) > 120
    ch = ConstraintChecker(tw, robot(), room.rasterize)
    b = np.zeros(ch.grid.data.shape, np.float32)
    b[rng.integers(5, b.shape[0] - 5, 3), rng.integers(5, b.shape[1] - 5, 3)] = [3.0, 2.0, 1.0]
    runner = CountingRunner(room, (1.0, 1.0))
    t0 = time.perf_counter()
    out = search_layout(tw, robot(), task(), b, runner=runner, rasterizer=room.rasterize,
                        budget={"screen_episodes": 4, "confirm_episodes": 8, "max_moves_per_object": 6,
                                "max_evaluations": 40})
    assert time.perf_counter() - t0 < 120
    assert runner.calls <= 40 and len(out["candidates"]) > 20


# ---------- determinism ----------
def _dump(results):
    return json.dumps([{k: (v.model_dump() if hasattr(v, "model_dump") else v) for k, v in r.items()}
                       for r in results], sort_keys=True)


_ROOM_SEED = 4


def process_runner(*a, **kw):  # module level: picklable for a process pool
    room, _, _, pinch, _ = _setup(_ROOM_SEED)
    return RoomRunner(room, pinch)(*a, **kw)


def test_executor_runs_match_serial_runs():
    room, tw, bn, pinch, _ = _setup(_ROOM_SEED)
    kw = dict(budget={"screen_episodes": 12, "confirm_episodes": 40, "max_moves_per_object": 6},
              rasterizer=room.rasterize, seed=3)
    serial = search_layout(tw, robot(), task(), bn, runner=process_runner, **kw)
    with ThreadPoolExecutor(4) as ex:
        threaded = search_layout(tw, robot(), task(), bn, runner=process_runner, executor=ex, **kw)
    with ProcessPoolExecutor(2) as ex:
        procs = search_layout(tw, robot(), task(), bn, runner=process_runner, executor=ex, **kw)
    assert serial["stats"]["runner_calls"] > 3
    assert _dump(serial["results"]) == _dump(threaded["results"]) == _dump(procs["results"])
    assert serial["stats"] == threaded["stats"] == procs["stats"]


# ---------- reference cafe with the real simulator ----------
def test_reference_cafe_with_real_simulator(tmp_path, monkeypatch):
    from clearance_core import storage
    from clearance_core.schemas import RobotSpec, Task, Twin
    from jobs.sim.api import rasterize_twin, run_batch
    from jobs.sim.batch import run_batch_full

    monkeypatch.setattr(storage, "DATA", tmp_path)
    shutil.copytree(REPO / "sample_sites/ref_cafe/twin_v1", tmp_path / "sites/ref_cafe/twin_v1")
    tw = Twin.model_validate_json((tmp_path / "sites/ref_cafe/twin_v1/twin.json").read_text(encoding="utf-8"))
    rb = RobotSpec.model_validate_json((REPO / "catalog/robots/keenon_dinerbot_t8.json").read_text(encoding="utf-8"))
    tk = Task(type="delivery", sequence=["DOCK", "PICKUP", "T1", "T3", "T5", "DOCK"])
    _, _, bott = run_batch_full(tw, rb, tk, "normal", "nominal", 20, 1000, record_episodes=False)
    assert np.isfinite(bott).all() and bott.max() > 0

    ch = ConstraintChecker(tw, rb, rasterize_twin)
    wp = {w.id: w for w in tw.waypoints}
    assert ch.targets == [("EXIT", wp["EXIT"].xy)]  # the street door is the cafe's exit
    assert ch.sources == [("ENTRANCE", wp["ENTRANCE"].xy)]
    assert ch.egress_width_required  # egress is enforced on the cafe
    v = Validator(tw, rb, rasterize_twin)
    peaks = bottleneck_peaks(bott, ch.grid)
    for c in ("TABLE4", "CH4W"):
        moves, _ = generate_moves(ch, c, peaks, max_moves=4)
        for m in moves:
            assert not v.violations({"move": {c: m}}), (c, m)

    t0 = time.perf_counter()
    budget = {"screen_episodes": 3, "confirm_episodes": 6, "top_k_confirm": 1, "top_k_combo": 1,
              "max_moves_per_object": 2, "max_objects": 1, "max_evaluations": 4, "verdict_confirm": False}
    out = search_layout(tw, rb, tk, bott, budget=budget, seed=1, runner=run_batch, rasterizer=rasterize_twin)
    assert out["stats"]["runner_calls"] <= 4 and time.perf_counter() - t0 < 300
    for r in out["results"]:
        assert r["uplift"] > 0 and not v.violations(r["change"])
