import json

import numpy as np
import pytest

from clearance_core import storage
from clearance_core.grid import FREE, OCC
from clearance_core.schemas import Twin
from jobs.recon.measure import aisle_passable, evaluate_grid
from jobs.reference.rooms import GOLDEN, GOLDEN_ROBOT_WIDTH_M, KINDS, make_room, save_sample_site


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("seed", [0, 1])
def test_every_room_is_consistent(kind, seed):
    room = make_room(kind, seed)
    t = room.twin
    assert t.geometry_source == "reference" and t.accuracy.method == "reference_ground_truth"
    assert len(room.gt_measurements) >= 10
    names = [m["name"] for m in room.gt_measurements]
    assert len(set(names)) == len(names)
    assert {"DOCK", "PICKUP"} <= {w.id for w in t.waypoints}
    assert any(w.kind == "dest" for w in t.waypoints)
    assert t.traffic.zones and t.traffic.od_pairs
    zone_ids = {z.id for z in t.traffic.zones}
    assert all(a in zone_ids and b in zone_ids for a, b, _ in t.traffic.od_pairs)
    assert all(p.source == "reference" for p in t.primitives)
    assert (storage.DATA / t.occupancy.uri).is_file()
    # waypoints sit on free cells
    for w in t.waypoints:
        r, c = room.grid.world_to_cell(*w.xy)
        assert room.grid.data[r, c] == FREE, w.id
    # ground-truth grid measured with the same tool reads its own distances to raster precision
    ev = evaluate_grid(room.grid, room.gt_measurements)
    assert ev["max_abs_err_m"] <= 0.026, ev["rows"]
    assert all(m["value_m"] > 0 for m in room.gt_measurements)


def test_movable_flags_by_label():
    room = make_room("cafe", 0)
    by = {p.label: p.movable for p in room.twin.primitives}
    assert by["table"] and by["chair"] and not by["counter"]
    st = make_room("storeroom", 0)
    by = {p.label: p.movable for p in st.twin.primitives}
    assert not by["shelf"] and not by["pillar"] and by["cart"]


def test_cafe_has_lip_and_named_tables():
    room = make_room("cafe", 2)
    assert [t.height_m for t in room.twin.thresholds] == [0.02]
    assert {f"T{i}" for i in range(1, 7)} <= {w.id for w in room.twin.waypoints}
    d = {m["name"]: m["value_m"] for m in room.gt_measurements}
    assert d["room_width_x"] == pytest.approx(8.0) and d["kitchen_door_width"] == pytest.approx(0.9)


def test_storeroom_aisles():
    for seed in (0, 1, 4):
        room = make_room("storeroom", seed)
        d = {m["name"]: m["value_m"] for m in room.gt_measurements}
        assert d["aisle_BC_0.80_x5"] == pytest.approx(0.80, abs=1e-6)
        assert d["aisle_AB_1.2_x5"] == pytest.approx(1.20, abs=1e-6)
        a = room.notes["aisle_080"]
        res = aisle_passable(room.grid, a["p0"], a["p1"], 0.80, tol_m=0.03)
        assert res["passable"], res


def test_golden_rooms_answers():
    narrow = make_room("golden_narrow_aisle")
    exact = make_room("golden_exact_aisle")
    assert narrow.notes["aisle_width_m"] < GOLDEN_ROBOT_WIDTH_M
    assert exact.notes["aisle_width_m"] == pytest.approx(GOLDEN_ROBOT_WIDTH_M + 0.02)
    for room in (narrow, exact):
        r, c = room.grid.world_to_cell(3.0, 2.0)
        assert room.grid.data[r, c] == FREE
        assert room.room_yaw == 0.0 and room.room_offset == (0.0, 0.0)
    thr = make_room("golden_threshold")
    assert thr.twin.thresholds[0].height_m == pytest.approx(0.03)
    r, c = thr.grid.world_to_cell(3.0, 2.0)
    assert thr.grid.data[r, c] == FREE  # the lip is below the height band
    r, c = thr.grid.world_to_cell(3.0, 1.0)
    assert thr.grid.data[r, c] == OCC
    empty = make_room("golden_empty")
    assert not empty.twin.primitives
    assert set(GOLDEN) <= set(KINDS)


def test_seeds_change_furniture_not_fixed_geometry():
    a, b = make_room("cafe", 0), make_room("cafe", 5)
    pa = {p.id: p.pose.xyz for p in a.twin.primitives}
    pb = {p.id: p.pose.xyz for p in b.twin.primitives}
    assert pa.keys() == pb.keys()
    assert pa["COUNTER"][2] == pb["COUNTER"][2]
    assert make_room("cafe", 5).gt_measurements == b.gt_measurements  # deterministic


def test_rotated_room_is_rotated():
    r = make_room("corridor", 1)
    assert abs(r.room_yaw) > np.deg2rad(7)
    d = {m["name"]: m["value_m"] for m in r.gt_measurements}
    assert d["corridor_width_x5"] == pytest.approx(1.8, abs=1e-6)


def test_unknown_kind():
    with pytest.raises(ValueError):
        make_room("ballroom")


def test_save_sample_site(tmp_path):
    twin = save_sample_site("storeroom", "store_ref_site", root=tmp_path)
    base = tmp_path / "store_ref_site"
    site = json.loads((base / "site.json").read_text())
    assert site["geometry_source"] == "reference" and site["latest_version"] == 1
    t2 = Twin.model_validate_json((base / "twin_v1" / "twin.json").read_text())
    assert t2.site_id == "store_ref_site" and t2.occupancy.uri == "sites/store_ref_site/twin_v1/occ.png"
    assert (base / "twin_v1" / "occ.png").is_file()
    assert len(json.loads((base / "twin_v1" / "gt_measurements.json").read_text())) >= 10
    assert twin.twin_id == t2.twin_id
