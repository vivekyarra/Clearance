import json
import math

import numpy as np
import pytest

from clearance_core import grid as gridio
from clearance_core import storage
from clearance_core.schemas import Hazard, Pose2, Primitive, Twin
from jobs.semantics.run import extract_semantics

from .scene_builder import RefBox, RefCyl, orbit_poses, rasterize_scene, write_capture
from .test_lift import MaskFromRenderDetector

SCENE = [RefBox("table", 0.3, 0.2, 0.4, 1.0, 0.6, 0.74), RefCyl("trash bin", -1.2, -0.9, 0.4, 0.9)]


class ObjectAwareVision:
    """Test double for the vision LLM: flags a cable at the first listed object in the first frame."""

    def __init__(self):
        self.calls = 0

    def chat(self, role, messages, **kw):
        self.calls += 1
        content = messages[1]["content"]
        objs = json.loads(content[0]["text"].split("\n", 1)[1].split("\n\nFrames follow")[0])
        frame = next(c["text"][6:-1] for c in content if c["type"] == "text" and c["text"].startswith("Frame "))
        oid = objs[0]["id"] if objs else None
        return {"choices": [{"message": {"content": "<think>checking {floor}</think>" + json.dumps(
            {"hazards": [{"label": "cable on floor", "object_id": oid, "frame_refs": [frame], "confidence": 0.7,
                          "position": [5, 5]}]})}}]}


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA", tmp_path / "data")
    poses = orbit_poses((0, 0), 2.4, 16)
    cap = write_capture(tmp_path / "cap", poses, SCENE)
    occ = gridio.save(rasterize_scene(SCENE), "sites/s1/twin_v1/occ.png")
    user = Primitive(id="U1", type="box", label="counter", pose=Pose2(xyz=(2.2, 2.2, 0.5)), size=(0.8, 0.5, 1.0),
                     source="user")
    stale = Primitive(id="T9", type="box", label="table", pose=Pose2(xyz=(0.3, 0.2, 0.37)), size=(1, 0.6, 0.74))
    twin = Twin(twin_id="tw1", site_id="s1", occupancy=occ, primitives=[user, stale],
                hazards=[Hazard(label="old", source="vision_llm"), Hazard(label="kept", source="user")])
    return cap, twin, poses


def test_geometry_based_detector_end_to_end(site):
    cap, twin, _ = site
    llm = ObjectAwareVision()
    out = extract_semantics(str(cap), twin, detector="geometric", llm=llm, out_prefix="sites/s1/twin_v1")
    auto = [p for p in out.primitives if p.source == "auto"]
    assert {p.label for p in auto} == {"table", "trash bin"}
    assert "T9" not in {p.id for p in out.primitives}           # previous auto result replaced
    assert any(p.id == "U1" and p.source == "user" for p in out.primitives)
    t = next(p for p in auto if p.label == "table")
    d = (t.pose.yaw - 0.4) % math.pi
    assert min(d, math.pi - d) < math.radians(4)
    assert abs(t.size[0] - 1.0) < 0.06 and abs(t.size[1] - 0.6) < 0.06
    assert abs(t.size[2] - 0.74) < 0.05                          # height from the depth-derived cloud
    assert all(p.confidence <= 0.35 for p in auto)
    assert t.movable
    # hazards: user one kept, old vision one replaced, new one located at a primitive (never model xy)
    labels = [h.label for h in out.hazards]
    assert "kept" in labels and "old" not in labels
    vh = [h for h in out.hazards if h.source == "vision_llm"]
    assert len(vh) == 1
    prim_xy = {(p.pose.xyz[0], p.pose.xyz[1]) for p in out.primitives}
    assert vh[0].xy in prim_xy and vh[0].xy != (5, 5)
    rep = storage.read_json(out.assets["semantics_report"])
    assert rep["detector"] == "geometric" and len(rep["keyframes"]) == 16
    assert rep["hazard_pass"]["violations"]
    assert twin.primitives[1].id == "T9"  # input twin untouched


def test_image_detector_end_to_end(site):
    cap, twin, poses = site
    det = MaskFromRenderDetector(poses, SCENE)
    out = extract_semantics(str(cap), twin, detector=det, llm=ObjectAwareVision(), n_keyframes=10)
    auto = {p.label: p for p in out.primitives if p.source == "auto"}
    assert set(auto) == {"table", "trash bin"}
    assert auto["trash bin"].type == "cylinder" and abs(auto["trash bin"].size[0] - 0.4) < 0.06
    assert auto["table"].confidence > 0.6
    assert np.hypot(auto["table"].pose.xyz[0] - 0.3, auto["table"].pose.xyz[1] - 0.2) < 0.05


def test_hazard_detector_labels_and_no_hazard_pass(site):
    cap, twin, poses = site

    class CableToo(MaskFromRenderDetector):
        def detect_frame(self, image_bgr, frame):
            dets = super().detect_frame(image_bgr, frame)
            for d in dets:
                if d.label == "trash bin":
                    d.label = "cable"
            return dets

    out = extract_semantics(str(cap), twin, detector=CableToo(poses, SCENE), hazards=False)
    assert "trash bin" not in {p.label for p in out.primitives}
    cab = [h for h in out.hazards if h.label == "cable on floor"]
    assert len(cab) == 1 and cab[0].source.startswith("detector:")
    assert np.hypot(cab[0].xy[0] + 1.2, cab[0].xy[1] + 0.9) < 0.1


def test_auto_detector_falls_back_without_models(site, monkeypatch):
    cap, twin, _ = site
    monkeypatch.delenv("CLEARANCE_SEMANTICS_DETECTOR", raising=False)
    out = extract_semantics(str(cap), twin, hazards=False, out_prefix="sites/s1/twin_v1")
    rep = storage.read_json(out.assets["semantics_report"])
    assert rep["detector"] == "geometric"


def test_missing_grid_and_depth_still_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA", tmp_path / "data")
    poses = orbit_poses((0, 0), 2.4, 6)
    cap = write_capture(tmp_path / "cap", poses, SCENE, with_depth=False)
    twin = Twin(twin_id="tw", site_id="s", occupancy={"uri": "missing.png"})
    out = extract_semantics(str(cap), twin, detector="geometric", hazards=False)
    assert out.primitives == []
