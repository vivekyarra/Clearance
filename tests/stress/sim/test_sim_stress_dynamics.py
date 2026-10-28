"""Episode dynamics: timeout, replanning, safety stop, near misses, pedestrians, live sessions, scale."""
import math
import time
import tracemalloc

import numpy as np
import pytest

from clearance_core import storage
from clearance_core.schemas import Task
from jobs.sim import params as P
from jobs.sim.api import LiveSession, run_batch
from jobs.sim.batch import run_batch_full
from jobs.sim.episode import MissionSim, make_context, nominal_geom, plan_mission, run_episode
from jobs.sim.live import EVENTS_LOG_MAX
from jobs.sim.peds import Crowd
from jobs.sim.twin_builder import RoomCanvas, box, make_twin, rect_robot

CALM = {"jitter": False, "clutter_max": 0, "warmup_s": 0.0}


def corridor(key="sites/dyn/corridor", ppm=None, zones=(), od=()):
    c = RoomCanvas(12.0, 3.0, res=0.025)
    return make_twin(c, key, [("A", 1.0, 1.5, 0.0, "dock"), ("B", 11.0, 1.5, 0.0, "dest")], zones=zones,
                     od_pairs=od, persons_per_min=ppm or {"quiet": 1, "normal": 2, "peak": 4})


def sim_with_still_pedestrian(twin, robot, ped_xy):
    """MissionSim on A->B with one motionless pedestrian and no spawning."""
    ctx = make_context(twin, robot, Task(sequence=["A", "B"]), "quiet", "nominal", sim_config=CALM)
    geom = nominal_geom(ctx)
    rails, why = plan_mission(ctx, geom)
    assert rails is not None, why
    sim = MissionSim(ctx, geom, list(rails), np.random.default_rng(0), ctx.cfg["dt"])
    sim.crowd.rate_s = 0.0
    sim.crowd.add_controlled(*ped_xy)
    return sim


def test_timeout_scales_the_drive_time_of_the_route():
    # Regression: nominal time was path pseudo-length / top speed. A fast robot (1.6 m/s) stops at every turn, so it
    # needed 2.4x that "nominal" time with no pedestrians at all and the 3x timeout failed it after ~25 s of delay:
    # OTTO 100 lost 6-10% of storeroom episodes to blocked_timeout for this reason alone.
    c = RoomCanvas(8.0, 6.0, res=0.025)
    for i, x in enumerate((2.0, 4.0, 6.0)):
        if i % 2:
            c.rect(x - 0.1, 1.2, x + 0.1, 6.0)
        else:
            c.rect(x - 0.1, 0.0, x + 0.1, 4.8)
    twin = make_twin(c, "sites/dyn/zigzag", [("A", 1.0, 1.0, 0.0, "dock"), ("B", 7.0, 5.0, 0.0, "dest")])
    fast = rect_robot(0.5, 0.6, speed=1.6)
    ctx = make_context(twin, fast, Task(sequence=["A", "B", "A"]), "none", "nominal", sim_config=CALM)
    s, _ = run_episode(ctx, 0, 1, None, False)
    assert s["outcome"] == "success"
    rails, _ = plan_mission(ctx, nominal_geom(ctx))
    assert s["nominal_time_s"] >= sum(r.s_end for r in rails) / 1.6
    assert s["elapsed_s"] <= 1.15 * s["nominal_time_s"]
    sim = MissionSim(ctx, nominal_geom(ctx), list(rails), np.random.default_rng(0), 0.1)
    assert sim.timeout == pytest.approx(3.0 * sim.nominal_time)


def test_replan_after_exactly_ten_seconds_blocked():
    # Regression: blocked time was a float sum of dt (100 x 0.1 = 9.999...), so the replan came one step late.
    robot = rect_robot(0.5, 0.6)
    sim = sim_with_still_pedestrian(corridor(), robot, (2.0, 1.5))
    steps, first_stop, replan_at = 0, None, None
    while not sim.done and steps < 400:
        ev = sim.step()
        steps += 1
        types = [e["type"] for e in ev]
        if "stop" in types and first_stop is None:
            first_stop = steps
        if "replan" in types:
            replan_at = steps
            break
    assert first_stop is not None and replan_at is not None
    assert replan_at - first_stop + 1 == round(P.REPLAN_AFTER_S / sim.dt)
    assert sim.t == pytest.approx(steps * sim.dt, abs=0) or sim.t == steps * sim.dt


def test_safety_stop_only_for_pedestrians_in_the_motion_direction():
    robot = rect_robot(0.5, 0.6)
    ahead = sim_with_still_pedestrian(corridor("sites/dyn/c_ahead"), robot, (1.0 + 0.3 + 0.25 + 0.3, 1.5))
    behind = sim_with_still_pedestrian(corridor("sites/dyn/c_behind"), robot, (1.0 - 0.3 - 0.25 - 0.3, 1.5))
    ev_a = [e["type"] for _ in range(15) for e in ahead.step()]
    ev_b = [e["type"] for _ in range(15) for e in behind.step()]
    assert "stop" in ev_a and ahead.x == pytest.approx(1.0, abs=0.05)
    assert "stop" not in ev_b and behind.x > 1.3


@pytest.mark.parametrize("edge_gap, expected", [(0.40, 1), (0.60, 0)])
def test_near_miss_counts_a_close_pass_once(edge_gap, expected):
    # pedestrian beside the robot, just behind its center (so no safety stop): edge-to-edge distance edge_gap
    robot = rect_robot(0.5, 0.6)
    sim = sim_with_still_pedestrian(corridor(f"sites/dyn/nm_{edge_gap}"), robot,
                                    (1.0 - 0.05, 1.5 + 0.25 + P.PED_RADIUS_M + edge_gap))
    for _ in range(30):
        sim.step()
    assert sim.near_misses == expected


def test_live_session_ten_minutes_at_20_hz_without_drift_jumps_or_growth():
    zones = [("W", [(0.3, 0.3), (1.2, 0.3), (1.2, 2.7), (0.3, 2.7)]),
             ("E", [(10.8, 0.3), (11.7, 0.3), (11.7, 2.7), (10.8, 2.7)])]
    twin = corridor("sites/dyn/live", {"quiet": 4, "normal": 12, "peak": 30}, zones,
                    [("W", "E", 1.0), ("E", "W", 1.0)])
    robot = rect_robot(0.5, 0.6)
    s = LiveSession(twin, robot, Task(sequence=["A", "B"]), "normal", 5)
    bad_inputs = [None, "fast", [1, 2], True, 1e308, -math.inf, math.nan, 10 ** 400, {"vx": 1}]
    tracemalloc.start()
    mem = {}
    prev = None
    max_jump = 0.0
    try:
        for i in range(12000):
            if i % 50 == 0:
                bad = bad_inputs[(i // 50) % len(bad_inputs)]
                s.set_joystick(bad, 0.5) if i % 100 else s.set_joystick(math.sin(i), math.cos(i) * 3)
                assert all(math.isfinite(v) for v in s.joy) and math.hypot(*s.joy) <= P.JOYSTICK_MAX_SPEED + 1e-9
            fr = s.step()
            xy = (fr["robot"]["x"], fr["robot"]["y"])
            if prev is not None:
                max_jump = max(max_jump, math.hypot(xy[0] - prev[0], xy[1] - prev[1]))
            prev = xy
            if i in (2400, 11999):
                mem[i] = tracemalloc.get_traced_memory()[0]
    finally:
        tracemalloc.stop()
    assert fr["t"] == 600.0 and s.sim.t == 12000 * P.LIVE_DT
    assert s.sim.legs_done >= 4 and s.sim.laps >= 2
    # a loop lap starts where the last leg ended: no frame-to-frame jump beyond the robot's own speed
    assert max_jump <= robot.nominal_speed_mps * P.LIVE_DT + 2e-3
    assert len(s.events_log) <= EVENTS_LOG_MAX
    assert mem[11999] - mem[2400] < 3 * 2 ** 20


def ped_room(ppm_peak, od=(("W", "E", 1.0), ("E", "W", 1.0)), door=None, key="sites/dyn/peds"):
    c = RoomCanvas(10.0, 6.0, res=0.025)
    if door:
        c.rect(4.9, 0.0, 5.1, 3.0 - door / 2)
        c.rect(4.9, 3.0 + door / 2, 5.1, 6.0)
    zones = [("W", [(0.3, 0.3), (1.6, 0.3), (1.6, 5.7), (0.3, 5.7)]),
             ("E", [(8.4, 0.3), (9.7, 0.3), (9.7, 5.7), (8.4, 5.7)])]
    return make_twin(c, key, [("A", 2.5, 3.0, 0.0, "dock"), ("B", 7.5, 3.0, 0.0, "dest")],
                     primitives=[box("T1", 3.0, 1.2, 0.8, 0.8), box("T2", 7.0, 4.8, 0.8, 0.8)], zones=zones,
                     od_pairs=list(od), persons_per_min={"quiet": 2, "normal": 8, "peak": ppm_peak})


def test_pedestrians_never_spawn_or_walk_inside_obstacles_or_the_robot():
    twin = ped_room(200.0, door=1.2)
    ctx = make_context(twin, rect_robot(0.5, 0.6), Task(sequence=["A", "B", "A"]), "peak", "nominal")
    from jobs.sim.episode import episode_variation
    from jobs.sim.routing import make_geom
    polys, _, _ = episode_variation(ctx, 11)
    geom = make_geom(ctx.base, polys)
    rails, why = plan_mission(ctx, geom)
    assert rails is not None, why
    sim = MissionSim(ctx, geom, list(rails), np.random.default_rng(11), 0.1)
    cr = sim.crowd
    H, W = geom.ped_blocked.shape
    seen_alive = np.zeros(cr.n, bool)
    for _ in range(900):
        before = cr.alive.astype(bool).copy()
        sim.step()
        if sim.done:
            break
        al = cr.alive.astype(bool)
        born = al & ~before
        if born.any():
            d = np.hypot(cr.px[born] - sim.x, cr.py[born] - sim.y)
            assert d.min() >= sim.model.radius + P.PED_RADIUS_M  # never spawned on the robot
        seen_alive |= al
        r = (H - 1 - np.floor((cr.py[al] - geom.origin[1]) / geom.res)).astype(int)
        c = np.floor((cr.px[al] - geom.origin[0]) / geom.res).astype(int)
        assert ((r >= 0) & (r < H) & (c >= 0) & (c < W)).all()
        assert not geom.ped_blocked[r, c].any()
    assert seen_alive.sum() >= 20


def test_zone_scale_and_od_pair_edge_cases():
    twin = ped_room(40.0)
    robot, task = rect_robot(0.5, 0.6), Task(sequence=["A", "B"])
    off = run_batch(twin, robot, task, "peak", "nominal", 2, 0, overrides={"ped_zone_scale": {"W": 0.0, "E": 0.0}},
                    record_episodes=False, out_prefix="analyses/zs/batches/")
    eps = storage.read_json(f"analyses/zs/batches/{off.batch_id}.summary.json")["episodes"]
    assert all(e["peds_peak"] == 0 for e in eps)
    for bad in ({"W": -1.0}, {"W": math.nan}, {"W": "x"}, {"NOPE": 1.0}, ["W"]):
        with pytest.raises(ValueError):
            make_context(twin, robot, task, "peak", "nominal", {"ped_zone_scale": bad})
    # OD pairs naming zones that do not exist are dropped; the valid pair still drives traffic
    partial = ped_room(40.0, od=(("W", "E", 1.0), ("W", "NOPE", 5.0), ("GONE", "E", 1.0)), key="sites/dyn/partial")
    ctx = make_context(partial, robot, task, "peak", "nominal")
    cr = Crowd(nominal_geom(ctx).ped_blocked, 0.025, ctx.layers.origin, partial.traffic, 40.0, {},
               np.random.default_rng(0))
    assert cr.source == "traffic_model" and cr.pairs == [("W", "E", 1.0, 1.0)]
    assert set(cr.dropped_pairs) == {("W", "NOPE"), ("GONE", "E")}
    none = ped_room(40.0, od=(("X", "Y", 1.0),), key="sites/dyn/none")
    ctx = make_context(none, robot, task, "peak", "nominal")
    cr = Crowd(nominal_geom(ctx).ped_blocked, 0.025, ctx.layers.origin, none.traffic, 40.0, {}, np.random.default_rng(0))
    assert cr.source == "uniform_anchors" and len(cr.pairs) >= 2


def test_huge_density_is_capped_and_a_doorway_crush_terminates():
    twin = ped_room(100000.0, door=0.9)
    ctx = make_context(twin, rect_robot(0.5, 0.6), Task(sequence=["A", "B"]), "peak", "nominal")
    t0 = time.perf_counter()
    s, lines = run_episode(ctx, 0, 3, None, True)
    assert s["outcome"] in ("success", "blocked_timeout", "collision")
    assert s["peds_peak"] <= P.PED_MAX_COUNT
    assert s["elapsed_s"] <= max(3.0 * s["nominal_time_s"], P.MIN_TIMEOUT_S) + 0.11
    assert time.perf_counter() - t0 < 120


def test_crowd_of_150_pedestrians_steps_fast():
    twin = ped_room(10000.0, key="sites/dyn/crowd150")
    ctx = make_context(twin, rect_robot(0.5, 0.6), Task(sequence=["A", "B"]), "peak", "nominal")
    g = nominal_geom(ctx)
    cr = Crowd(g.ped_blocked, g.res, g.origin, twin.traffic, 10000.0, {}, np.random.default_rng(1), cap=150)
    poly = np.array([[2.8, 2.75], [2.2, 2.75], [2.2, 3.25], [2.8, 3.25]])
    # 150 walkers on a 0.5 m lattice of open floor, half heading east and half west
    rr, cc = np.nonzero(cr.edt > 0.3)
    xs, ys = cr._cells_world(rr, cc)
    keep = (rr % 20 == 10) & (cc % 20 == 10) & (np.hypot(xs - 2.5, ys - 3.0) > 1.0)
    idx = np.nonzero(keep)[0][:150]
    assert len(idx) == 150
    for i, j in enumerate(idx):
        cr.px[i], cr.py[i] = xs[j], ys[j]
        cr.gx[i], cr.gy[i] = (9.0, 3.0) if i % 2 else (1.0, 3.0)
        cr.dz[i] = cr.dest_index["E" if i % 2 else "W"]
        cr.alive[i] = 1
    assert cr.n_alive == 150
    t0 = time.perf_counter()
    for _ in range(100):
        cr.step(0.1, poly, (2.5, 3.0), 0.4)
    assert time.perf_counter() - t0 < 10.0
    assert np.isfinite(cr.px[cr.alive == 1]).all()


def test_large_site_30_by_30_m_at_2_5_cm():
    c = RoomCanvas(30.0, 30.0, res=0.025)
    for i in range(1, 6):
        c.rect(i * 5.0 - 0.1, 2.0, i * 5.0 + 0.1, 28.0)
    zones = [("S", [(0.5, 0.3), (29.5, 0.3), (29.5, 1.8), (0.5, 1.8)]),
             ("N", [(0.5, 28.2), (29.5, 28.2), (29.5, 29.7), (0.5, 29.7)])]
    twin = make_twin(c, "sites/dyn/big", [("A", 2.5, 1.0, 0.0, "dock"), ("B", 27.5, 29.0, 0.0, "dest")], zones=zones,
                     od_pairs=[("S", "N", 1), ("N", "S", 1)], persons_per_min={"quiet": 5, "normal": 30, "peak": 300},
                     primitives=[box(f"T{i}", 2.5 + 5 * i, 15.0, 0.8, 0.8) for i in range(6)])
    t0 = time.perf_counter()
    b, eps, bott = run_batch_full(twin, rect_robot(0.5, 0.6), Task(sequence=["A", "B"]), "peak", "nominal", 1, 5,
                                  record_episodes=True)
    assert eps[0]["outcome"] in ("success", "blocked_timeout", "collision")
    assert eps[0]["peds_peak"] >= 40 and bott.shape == (1200, 1200)
    assert time.perf_counter() - t0 < 120


def test_thousand_episode_batch_streams_its_recording():
    c = RoomCanvas(3.0, 2.0, res=0.025)
    twin = make_twin(c, "sites/dyn/tiny", [("A", 0.6, 1.0, 0.0, "dock"), ("B", 2.4, 1.0, 0.0, "dest")])
    tracemalloc.start()
    try:
        b = run_batch(twin, rect_robot(0.4, 0.5), Task(sequence=["A", "B"]), "none", "nominal", 1000, 0,
                      out_prefix="analyses/k/batches/", shard_size=250)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert b.results.n_episodes == 1000 and b.results.success_rate == 1.0
    lines = (storage.DATA / b.episodes_uri).read_text().splitlines()
    first = {int(l[11:l.index(",")]) for l in lines if l.startswith('{"episode":')}
    assert first == set(range(1000))
    assert peak < 300 * 2 ** 20
