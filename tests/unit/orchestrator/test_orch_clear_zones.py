"""Route-gap pre-screen honours operator-confirmed clear zones and notes them for the report."""
from __future__ import annotations

from clearance_core import grid as cgrid
from clearance_core.grid import UNKNOWN
from clearance_core.schemas import ClearZone, Task
from agents.orchestrator.candidates import clear_zone_note, static_filter, with_clear_zones
from jobs.sim.api import rasterize_twin
from jobs.sim.twin_builder import RoomCanvas, make_twin, rect_robot

ZONE = [(2.8, 1.4), (3.2, 1.4), (3.2, 2.6), (2.8, 2.6)]


def _twin(data_dir, zones=()):
    c = RoomCanvas(6.0, 4.0, res=0.025)
    c.rect(2.9, 0.0, 3.1, 1.5)
    c.rect(2.9, 2.5, 3.1, 4.0)
    c.rect(2.9, 1.5, 3.1, 2.25, value=UNKNOWN)
    t = make_twin(c, "sites/orch/pinch", [("A", 1.0, 2.0, 0.0, "pickup"), ("B", 5.0, 2.0, 0.0, "dest")])
    return t.model_copy(update={"clear_zones": [ClearZone(id=f"C{i + 1}", polygon=z, confirmed_by="operator")
                                                for i, z in enumerate(zones)]})


def test_prescreen_without_zone_drops_the_robot(data_dir):
    t = _twin(data_dir)
    res = static_filter(t, [rect_robot(0.5, 0.6)], Task(sequence=["A", "B"]), grid=cgrid.load(t.occupancy))
    assert not res["candidates"] and res["excluded"][0]["rule"] == "footprint_vs_route_gap"
    assert not any("confirmed clear" in n for n in res["notes"])


def test_prescreen_with_zone_on_raw_and_rasterized_grid(data_dir):
    t = _twin(data_dir, [ZONE])
    for g in (cgrid.load(t.occupancy), rasterize_twin(t)):
        res = static_filter(t, [rect_robot(0.5, 0.6)], Task(sequence=["A", "B"]), grid=g)
        assert [c["robot_id"] for c in res["candidates"]] == ["robot_ref"]
        assert res["route_gaps"][0]["gap_m"] >= 0.9
        note = [n for n in res["notes"] if n.startswith("operator-confirmed clear floor")]
        assert len(note) == 1 and "1 zone," in note[0] and "0.48 m²" in note[0] and "operator" in note[0]


def test_with_clear_zones_is_idempotent_and_no_grid_still_notes(data_dir):
    t = _twin(data_dir, [ZONE])
    g1, s = with_clear_zones(cgrid.load(t.occupancy), t)
    g2, _ = with_clear_zones(g1, t)
    assert (g1.data == g2.data).all() and s["unseen_area_m2"] > 0
    res = static_filter(t, [rect_robot(0.5, 0.6)], Task(sequence=["A", "B"]), grid=None)
    assert any(n.startswith("operator-confirmed clear floor") for n in res["notes"])
    assert clear_zone_note(None) is None
