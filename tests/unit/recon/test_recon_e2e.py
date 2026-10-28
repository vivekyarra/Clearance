"""End-to-end smoke: reference room -> rendered capture -> build_twin_from_capture -> accuracy."""
import numpy as np
import pytest

from clearance_core import grid as cgrid
from clearance_core import storage
from jobs.recon.measure import aisle_passable, evaluate
from jobs.recon.pipeline import build_twin_from_capture
from jobs.reference.render import render_capture
from jobs.reference.rooms import make_room


@pytest.mark.parametrize("tier,limit", [("T1_LIDAR", 0.03), ("T2_VIO_MONO", 0.05)])
def test_golden_exact_aisle_end_to_end(tmp_path, tier, limit):
    room = make_room("golden_exact_aisle", 0)
    cap = render_capture(room, str(tmp_path / "cap"), tier, seed=3)
    twin = build_twin_from_capture(cap, "e2e", "sites/e2e/twin_v1/")
    assert twin.geometry_source == "scan" and twin.version == 1
    assert (storage.DATA / "sites/e2e/twin_v1/occ.png").is_file()
    assert (storage.DATA / "sites/e2e/twin_v1/twin.json").is_file()
    rep = storage.read_json("sites/e2e/twin_v1/recon_report.json")
    assert rep["n_depth_frames"] > 100 and rep["timings"]["total_s"] > 0
    ev = evaluate(twin, room.gt_measurements)
    assert ev["median_abs_err_m"] <= limit, ev
    # the 0.52 m aisle stays open and reads close to its width
    g = cgrid.load(twin.occupancy)
    a = aisle_passable(g, (2.3, 2.0), (3.7, 2.0), 0.52, tol_m=0.05, end_margin_m=0.15)
    assert a["centerline_free"], a
    assert abs(a["median_width_m"] - 0.52) <= 0.05


def test_threshold_room_lip_found_and_door_open(tmp_path):
    room = make_room("golden_threshold", 0)
    cap = render_capture(room, str(tmp_path / "cap"), "T1_LIDAR", seed=1)
    twin = build_twin_from_capture(cap, "lip", "sites/lip/twin_v2/")
    assert twin.version == 2
    g = cgrid.load(twin.occupancy)
    r, c = g.world_to_cell(3.0, 2.0)
    assert g.data[r, c] == cgrid.FREE  # a 3 cm lip is below the 3 cm band: not an obstacle
    near = [t for t in twin.thresholds if abs(np.mean([p[0] for p in t.segment]) - 3.0) < 0.2]
    assert near, twin.thresholds
    assert near[0].height_m == pytest.approx(0.03, abs=0.012)
    assert near[0].status in ("measured", "flagged_manual")
