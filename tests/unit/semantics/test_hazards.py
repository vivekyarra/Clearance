import base64
import json

import cv2
import numpy as np
import pytest

from clearance_core.schemas import Pose2, Primitive
from jobs.semantics.hazards import (HAZARD_SCHEMA, build_messages, extract_json_block, normalize_hazard_label,
                                    response_text, run_hazard_pass, validate_hazards)

from .scene_builder import orbit_poses

FRAMES = [f"{i:06d}.jpg" for i in range(10)]
OBJECTS = {"T1": (1.0, 2.0), "C3": (-0.5, 0.25)}


def prims():
    return [Primitive(id="T1", type="box", label="table", pose=Pose2(xyz=(1.0, 2.0, 0.37), yaw=0.0),
                      size=(1.2, 0.6, 0.74), movable=True),
            Primitive(id="C3", type="box", label="chair", pose=Pose2(xyz=(-0.5, 0.25, 0.45), yaw=0.0),
                      size=(0.45, 0.45, 0.9), movable=True)]


class ScriptedVision:
    """Test double for agents.llm.client.LLM: records calls, returns scripted replies."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, role, messages, **kw):
        self.calls.append({"role": role, "messages": messages, **kw})
        r = self.replies.pop(0) if self.replies else '{"hazards": []}'
        if isinstance(r, Exception):
            raise r
        if callable(r):
            r = r(messages)
        return {"choices": [{"message": {"content": r}}]}


# ---------- JSON extraction ----------
def test_extracts_json_after_reasoning_with_braces():
    text = ("<think>The user wants hazards. A JSON like {\"x\": 1} would be wrong. Frame 000002 shows a cable; "
            "maybe {not json}. I will answer.</think>\n\n<answer>\n{\"hazards\": [{\"label\": \"cable on floor\", "
            "\"object_id\": \"T1\", \"frame_refs\": [\"000002.jpg\"], \"confidence\": 0.7}]}\n</answer>")
    out = extract_json_block(text)
    assert out["hazards"][0]["label"] == "cable on floor"


def test_extracts_json_from_plain_reasoning_without_tags():
    text = ('Let me look. Objects: {"T1": "table"} is a table. Reasoning done.\n'
            'Final answer: {"hazards": [{"label": "wet area", "object_id": null, "frame_refs": ["000001.jpg"]}]}')
    assert extract_json_block(text)["hazards"][0]["label"] == "wet area"


def test_extracts_fenced_and_bare_list_and_unclosed_think():
    fenced = 'Sure!\n```json\n{"hazards": []}\n```'
    assert extract_json_block(fenced) == {"hazards": []}
    bare = 'Here: [{"label": "glass partition", "frame_refs": ["000003.jpg"]}]'
    assert extract_json_block(bare)["hazards"][0]["label"] == "glass partition"
    unclosed = 'reasoning about {braces} ...</think>{"hazards": [{"label": "step", "frame_refs": ["000004.jpg"]}]}'
    assert extract_json_block(unclosed)["hazards"][0]["label"] == "step"
    assert extract_json_block("no json here at all") is None
    assert extract_json_block("") is None


def test_response_text_variants():
    class Msg:
        content = "hello"

    class Choice:
        message = Msg()

    class Resp:
        choices = [Choice()]

    class ClientResp:  # shape of agents.llm.client.LLMResponse
        content = "x"
        choices = None

    assert response_text(Resp()) == "hello"
    assert response_text(ClientResp()) == "x"
    assert response_text({"choices": [{"message": {"content": [{"type": "text", "text": "a"}, {"text": "b"}]}}]}) == "ab"
    assert response_text("raw") == "raw"
    assert response_text(None) == ""


# ---------- validation and the no-geometry rule ----------
def test_geometry_from_model_is_ignored_and_xy_comes_from_object():
    payload = {"hazards": [
        {"label": "cable on floor", "object_id": "T1", "frame_refs": ["000001.jpg"], "confidence": 0.8,
         "xy": [9.0, 9.0], "bbox": [1, 2, 3, 4], "reason": "cable 1.5 m from the table"},
        {"label": "door swings into aisle", "object_id": None, "frame_refs": ["000002.jpg"],
         "location": {"x": 3, "y": 4}},
    ]}
    res = validate_hazards(payload, frames=FRAMES, objects=OBJECTS)
    assert len(res.hazards) == 2
    h0, h1 = res.hazards
    assert h0.xy == (1.0, 2.0) and h0.source == "vision_llm" and h0.frame_refs == ["000001.jpg"]
    assert h1.xy is None and h1.confidence < 0.5  # no object -> no location, lower confidence
    assert any("xy" in v and "bbox" in v for v in res.violations)
    assert any("location" in v for v in res.violations)
    assert any("measurements in text" in v for v in res.violations)


def test_references_are_checked():
    payload = {"hazards": [
        {"label": "wet area", "object_id": "T1", "frame_refs": ["999999.jpg"]},           # unknown frame only
        {"label": "wet area", "object_id": "NOPE", "frame_refs": ["000003.jpg", "x.jpg"]},  # unknown object
        {"label": 5, "frame_refs": ["000003.jpg"]},                                         # bad label
        "not a dict",
        {"label": "glass partition", "object_id": "C3", "frame_refs": "000004.jpg", "confidence": "high"},
        {"label": "glass partition", "object_id": "C3", "frame_refs": ["000005.jpg"], "confidence": 7},
    ]}
    res = validate_hazards(payload, frames=FRAMES, objects=OBJECTS)
    labels = [(h.label, h.xy) for h in res.hazards]
    assert ("wet area", None) in labels
    glass = [h for h in res.hazards if h.label == "glass partition"]
    assert len(glass) == 1  # duplicates merged
    assert glass[0].frame_refs == ["000004.jpg", "000005.jpg"] and glass[0].xy == (-0.5, 0.25)
    assert glass[0].confidence <= 0.95
    assert len(res.dropped) >= 4
    for h in res.hazards:
        assert set(h.frame_refs) <= set(FRAMES)


def test_payload_shapes():
    assert validate_hazards(None, frames=FRAMES, objects=OBJECTS).status == "no_json"
    assert validate_hazards({"other": 1}, frames=FRAMES, objects=OBJECTS).hazards == []
    assert validate_hazards({"hazards": []}, frames=FRAMES, objects=OBJECTS).hazards == []


def test_label_normalization():
    assert normalize_hazard_label("Cable") == "cable on floor"
    assert normalize_hazard_label("wet floor near sink") == "wet area"
    assert normalize_hazard_label("Glass Door 2 m wide") == "glass partition"
    assert normalize_hazard_label("unusual thing") == "unusual thing"


# ---------- full pass with a test double ----------
def _frames(n=14):
    poses = orbit_poses((0, 0), 2.5, n)
    imgs = {p.frame: np.full((120, 160, 3), 40 + 10 * i, np.uint8) for i, p in enumerate(poses)}
    return poses, imgs


def test_hazard_pass_sends_8_to_12_frames_and_object_list():
    poses, imgs = _frames(14)
    def reply(messages):
        first = next(c["text"].split()[1].rstrip(":") for c in messages[1]["content"]
                     if c["type"] == "text" and c["text"].startswith("Frame "))
        return ('<think>looking at frames {}</think>{"hazards": [{"label": "cable on floor", "object_id": "C3", '
                f'"frame_refs": ["{first}"], "confidence": 0.6, "xy": [0, 0]}}]}}')

    llm = ScriptedVision(reply)
    res = run_hazard_pass(poses, imgs.get, prims(), llm=llm, n_frames=10)
    call = llm.calls[0]
    assert call["role"] == "vision"
    content = call["messages"][1]["content"]
    images = [c for c in content if c["type"] == "image_url"]
    assert 8 <= len(images) <= 12 and len(images) == len(res.frames_sent)
    b64 = images[0]["image_url"]["url"].split(",", 1)[1]
    assert cv2.imdecode(np.frombuffer(base64.b64decode(b64), np.uint8), cv2.IMREAD_COLOR) is not None
    assert '"T1"' in content[0]["text"] and '"C3"' in content[0]["text"]
    assert "coordinates" in call["messages"][0]["content"]  # the prompt forbids geometry
    assert len(res.hazards) == 1 and res.hazards[0].xy == (-0.5, 0.25)
    assert res.hazards[0].frame_refs == [res.frames_sent[0]]
    assert res.violations
    # n_frames is clamped into [8, 12]
    llm2 = ScriptedVision()
    res2 = run_hazard_pass(poses, imgs.get, prims(), llm=llm2, n_frames=40)
    assert len(res2.frames_sent) == 12


def test_hazard_pass_failure_modes():
    poses, imgs = _frames(5)
    res = run_hazard_pass(poses, imgs.get, prims(), llm=ScriptedVision(RuntimeError("down")))
    assert res.status == "llm_error" and res.hazards == []
    res = run_hazard_pass(poses, imgs.get, prims(), llm=ScriptedVision("I see no problems."))
    assert res.status == "no_json" and res.hazards == []
    res = run_hazard_pass(poses, lambda name: None, prims(), llm=ScriptedVision())
    assert res.status == "no_frames"


def test_hazard_pass_with_shared_client_offline():
    """Runs through agents/llm/client.py in offline mode with a scripted Cosmos-style reply."""
    client = pytest.importorskip("agents.llm.client")
    poses, imgs = _frames(10)
    reply = ('<think>Frame ' + poses[1].frame + ' has a cable near {the table}.</think>\n'
             '{"hazards": [{"label": "cable on floor", "object_id": "T1", "frame_refs": ["' + poses[1].frame + '"], '
             '"confidence": 0.9}]}')
    llm = client.LLM(mode="offline", offline=client.OfflineLLM(script={"vision": [reply]}), log=False)
    res = run_hazard_pass(poses, imgs.get, prims(), llm=llm)
    assert res.status == "ok"
    assert [(h.label, h.xy) for h in res.hazards] == [("cable on floor", (1.0, 2.0))]
    # default offline policy (no script) -> schema-valid empty answer
    llm = client.LLM(mode="offline", log=False)
    assert run_hazard_pass(poses, imgs.get, prims(), llm=llm).hazards == []


def test_schema_and_messages_are_json_serializable():
    json.dumps(HAZARD_SCHEMA)
    msgs = build_messages([("a.jpg", "AAAA")], prims(), {"T1": ["a.jpg"], "C3": []})
    json.dumps(msgs)
