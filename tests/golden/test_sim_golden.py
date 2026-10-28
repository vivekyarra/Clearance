"""Golden simulation tests (PLAN 15.2): reference rooms with analytically known answers."""
import json
import math

import pytest

from clearance_core import storage
from clearance_core.schemas import Task
from jobs.sim.api import check_feasibility, run_batch
from jobs.sim.twin_builder import RoomCanvas, box, make_twin, rect_robot

ROBOT_W, ROBOT_L = 0.50, 0.60
CALM = {"clutter_max": 0, "jitter": False}


def two_rooms_with_aisle(aisle_w: float, res: float = 0.01):
    """Two rooms joined by a straight 1.2 m long aisle of width aisle_w, centered on y = 2.0."""
    c = RoomCanvas(7.0, 4.0, res=res)
    c.rect(2.9, 0.0, 4.1, 2.0 - aisle_w / 2)
    c.rect(2.9, 2.0 + aisle_w / 2, 4.1, 4.0)
    return make_twin(c, "sites/golden/aisle", [("A", 1.2, 2.0, math.pi / 2, "dock"), ("B", 5.8, 2.0, 0.0, "dest")])


def corridor_with_side_aisle(aisle_w: float = 0.52, res: float = 0.01):
    """1.0 m corridor along x; a perpendicular aisle (aisle_w) leads north to an upper room."""
    c = RoomCanvas(6.0, 5.0, res=res)
    c.rect(0.0, 1.1, 3.0, 4.0)
    c.rect(3.0 + aisle_w, 1.1, 6.0, 4.0)
    return make_twin(c, "sites/golden/side", [("A", 1.0, 0.6, 0.0, "dock"), ("B", 3.0 + aisle_w / 2, 4.5, 0.0, "dest")])


def test_aisle_narrower_than_robot_is_no_path_static():
    twin = two_rooms_with_aisle(0.48)
    robot = rect_robot(ROBOT_W, ROBOT_L)
    task = Task(sequence=["A", "B"])
    f = check_feasibility(twin, robot, task)
    leg = f.legs[0]
    assert f.all_reachable is False and leg.reachable is False
    assert leg.narrowest_gap_m == pytest.approx(0.48, abs=0.015)
    assert leg.required_gap_m == pytest.approx(ROBOT_W)
    assert "narrowest gap" in leg.reason
    b = run_batch(twin, robot, task, "none", "nominal", 6, 0, record_episodes=False)
    assert b.results.success_rate == 0.0
    assert b.results.fail_reasons["no_path_static"] == 6


def test_aisle_two_cm_wider_with_in_place_rotation_has_path():
    twin = two_rooms_with_aisle(ROBOT_W + 0.02)
    f = check_feasibility(twin, rect_robot(ROBOT_W, ROBOT_L), Task(sequence=["A", "B"]))
    leg = f.legs[0]
    assert leg.reachable is True
    assert leg.narrowest_gap_m == pytest.approx(ROBOT_W + 0.02, abs=0.015)
    assert leg.required_gap_m == pytest.approx(ROBOT_W)
    assert 1.2 < leg.narrowest_xy[0] < 6.0
    assert leg.path_length_m == pytest.approx(4.6, abs=0.3)


def test_tight_turn_needs_in_place_rotation():
    twin = corridor_with_side_aisle(ROBOT_W + 0.02)
    task = Task(sequence=["A", "B"])
    spinning = check_feasibility(twin, rect_robot(ROBOT_W, ROBOT_L), task)
    assert spinning.all_reachable is True
    car = rect_robot(ROBOT_W, ROBOT_L, in_place=False, turn_radius=1.0, drive="ackermann", robot_id="car_ref")
    f = check_feasibility(twin, car, task)
    assert f.legs[0].reachable is False
    assert f.legs[0].reason.startswith("no path")


def door_room(height, status="measured"):
    c = RoomCanvas(6.0, 3.0, res=0.025)
    c.rect(2.9, 0.0, 3.1, 1.0)
    c.rect(2.9, 2.0, 3.1, 3.0)
    return make_twin(c, "sites/golden/door", [("A", 1.0, 1.5, 0.0, "dock"), ("B", 5.0, 1.5, 0.0, "dest")],
                     thresholds=[("D1", ((3.0, 1.0), (3.0, 2.0)), height, status)])


def test_threshold_above_robot_limit_fails_at_threshold():
    twin = door_room(0.03)
    robot = rect_robot(ROBOT_W, ROBOT_L, max_threshold=0.02)
    task = Task(sequence=["A", "B"])
    leg = check_feasibility(twin, robot, task).legs[0]
    assert leg.reachable is False
    assert leg.blocking_threshold == "D1"
    assert "above the robot limit" in leg.reason
    b = run_batch(twin, robot, task, "none", "nominal", 3, 0, record_episodes=False)
    assert b.results.fail_reasons["no_path_static"] == 3


def test_threshold_within_limit_passes_and_flagged_is_unknown():
    task = Task(sequence=["A", "B"])
    robot = rect_robot(ROBOT_W, ROBOT_L, max_threshold=0.02)
    assert check_feasibility(door_room(0.015), robot, task).legs[0].reachable is True
    leg = check_feasibility(door_room(None, "flagged_manual"), robot, task).legs[0]
    assert leg.reachable is None
    assert leg.blocking_threshold == "D1"
    unknown_limit = rect_robot(ROBOT_W, ROBOT_L, max_threshold=None)
    assert check_feasibility(door_room(0.015), unknown_limit, task).all_reachable is None


def test_empty_room_no_pedestrians_full_success_and_nominal_time():
    c = RoomCanvas(14.0, 4.0, res=0.025)
    twin = make_twin(c, "sites/golden/empty", [("A", 1.0, 2.0, 0.0, "dock"), ("B", 13.0, 2.0, 0.0, "dest")])
    robot = rect_robot(ROBOT_W, ROBOT_L, speed=0.5)
    b = run_batch(twin, robot, Task(sequence=["A", "B"]), "none", "nominal", 8, 11, sim_config=CALM,
                  out_prefix="analyses/golden/batches/")
    assert b.results.success_rate == 1.0
    eps = storage.read_json(f"analyses/golden/batches/{b.batch_id}.summary.json")["episodes"]
    for e in eps:
        expected = e["path_length_m"] / 0.5
        assert e["mission_time_s"] == pytest.approx(expected, rel=0.05)
        assert e["path_length_m"] == pytest.approx(12.0, abs=0.1)


def ped_room():
    c = RoomCanvas(10.0, 6.0, res=0.025)
    c.rect(4.9, 0, 5.1, 2.4)
    c.rect(4.9, 3.6, 5.1, 6)
    return make_twin(
        c, "sites/golden/peds", [("A", 1.0, 3.0, 0.0, "dock"), ("B", 9.0, 3.0, 0.0, "dest")],
        primitives=[box("T1", 2.5, 1.5, 0.8, 0.8), box("C1", 7.0, 4.5, 0.45, 0.45, label="chair")],
        zones=[("Z1", [(0.3, 0.3), (1.5, 0.3), (1.5, 1.5), (0.3, 1.5)]),
               ("Z2", [(8.5, 4.5), (9.7, 4.5), (9.7, 5.7), (8.5, 5.7)])],
        od_pairs=[("Z1", "Z2", 1.0), ("Z2", "Z1", 1.0)], persons_per_min={"quiet": 2, "normal": 8, "peak": 16})


def test_same_seed_bit_identical_output():
    twin = ped_room()
    robot = rect_robot(ROBOT_W, ROBOT_L)
    task = Task(sequence=["A", "B", "A"])
    a = run_batch(twin, robot, task, "normal", "nominal", 4, 42, out_prefix="analyses/d1/batches/")
    b = run_batch(twin, robot, task, "normal", "nominal", 4, 42, out_prefix="analyses/d2/batches/")
    ja = (storage.DATA / a.episodes_uri).read_bytes()
    jb = (storage.DATA / b.episodes_uri).read_bytes()
    assert ja == jb and len(ja) > 1000
    assert (storage.DATA / a.bottleneck_uri).read_bytes() == (storage.DATA / b.bottleneck_uri).read_bytes()
    assert a.results == b.results
    first = json.loads(ja.splitlines()[0])
    assert first["episode"] == 0 and set(first) >= {"t", "robot", "peds", "events"}
    c = run_batch(twin, robot, task, "normal", "nominal", 4, 43, record_episodes=False)
    assert c.batch_id != a.batch_id
