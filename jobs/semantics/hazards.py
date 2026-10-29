"""Vision-LLM hazard pass.

Scene model: Cosmos Reason 2 (self-hosted via vLLM, primary) or Nemotron 3.5 Super VL on Token
Factory (alternative); both reached through `agents.llm.client.LLM.chat(role="vision")` (VERIFY the
deployed model ids in config/models.yaml). The model sees 8-12 keyframes plus the detected object
list and returns JSON hazards that reference frames and object ids.

The model never supplies geometry: any coordinate/size fields in its answer are discarded and
recorded as violations, and a hazard's xy comes only from the referenced object's position.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import cv2
import numpy as np

from clearance_core.schemas import Hazard, PoseRecord, Primitive

log = logging.getLogger(__name__)

HAZARD_LABELS = ["cable on floor", "glass partition", "wet area", "door swings into aisle",
                 "step / level change", "loose rug or mat", "clutter in aisle", "fragile item near path",
                 "low overhang", "other"]
HAZARD_ALIASES = {"cable": "cable on floor", "cables": "cable on floor", "cord on floor": "cable on floor",
                  "wire on floor": "cable on floor", "glass wall": "glass partition", "glass door": "glass partition",
                  "wet floor": "wet area", "spill": "wet area", "door swing": "door swings into aisle",
                  "door": "door swings into aisle", "step": "step / level change", "stairs": "step / level change",
                  "threshold": "step / level change", "rug": "loose rug or mat", "mat": "loose rug or mat",
                  "clutter": "clutter in aisle", "obstacle in aisle": "clutter in aisle"}
GEOMETRY_KEYS = {"xy", "x", "y", "z", "xyz", "position", "pos", "location", "coordinates", "coords", "bbox",
                 "box", "polygon", "point", "points", "center", "centre", "distance", "distance_m", "size",
                 "width", "height", "length", "area", "radius", "diameter", "pose", "yaw", "angle", "pixel",
                 "pixels", "uv"}
_MEASURE_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:m|cm|mm|meters?|metres?|ft|feet|foot|in|inch(?:es)?|px|pixels?|°|deg(?:rees)?)\b",
                         re.IGNORECASE)
_COORD_RE = re.compile(r"\(\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?(?:\s*,\s*-?\d+(?:\.\d+)?)?\s*\)")
_ASSIGN_RE = re.compile(r"\b(?:x|y|z|xy|xyz|row|col|u|v)\s*[=:]\s*-?\d", re.IGNORECASE)
# Free-text labels outside HAZARD_LABELS reach the report: keep them short, plain and free of
# instructions or numbers; anything else becomes "other".
_FREE_LABEL_RE = re.compile(r"^[a-z][a-z /'-]{0,39}$")
_FREE_LABEL_MAX_WORDS = 5
_INSTRUCTION_WORDS = {"ignore", "instruction", "instructions", "system", "prompt", "assistant", "user",
                      "verdict", "cleared", "override", "disregard", "previous", "report", "approve",
                      "approved", "pass", "passed", "http", "https", "www", "json", "script"}
MAX_TEXT_CHARS = 200_000   # model replies longer than this are cut before JSON extraction
MAX_HAZARDS = 64           # hazard items read from one reply

SYSTEM_PROMPT = """You review indoor scenes for a wheeled delivery-robot site survey.
You get keyframes from a phone scan (each introduced by its frame name) and a list of detected objects with ids.
Find hazards for a floor robot, for example: {labels}.

Answer with ONE JSON object and nothing else after it:
{{"hazards": [{{"label": "<one of the labels above>", "object_id": "<id from the object list or null>",
  "frame_refs": ["<frame name where it is visible>"], "confidence": <0..1>, "reason": "<short text>"}}]}}
Rules:
- Use only frame names and object ids that were given to you.
- Do NOT output coordinates, distances, sizes, pixel positions or any other geometry. Location comes from object_id.
- If there are no hazards, answer {{"hazards": []}}.
- You may think first; if you do, put the final JSON after your reasoning (for example inside <answer></answer>)."""


HAZARD_SCHEMA = {
    "type": "object",
    "properties": {"hazards": {"type": "array", "items": {
        "type": "object",
        "properties": {"label": {"type": "string"}, "object_id": {"type": ["string", "null"]},
                       "frame_refs": {"type": "array", "items": {"type": "string"}},
                       "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                       "reason": {"type": "string"}},
        "required": ["label", "frame_refs"]}}},
    "required": ["hazards"],
}


@dataclass
class HazardPassResult:
    hazards: list[Hazard] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    frames_sent: list[str] = field(default_factory=list)
    raw_text: str = ""
    status: str = "ok"  # ok | no_json | llm_unavailable | llm_error | no_frames


# ---------- response parsing ----------
def response_text(resp: Any) -> str:
    """Text content of an OpenAI-compatible chat response (object, dict or plain string)."""
    if resp is None:
        return ""
    if isinstance(resp, str):
        return resp
    if isinstance(resp, dict):
        if "choices" in resp:
            msg = resp["choices"][0].get("message", {})
            return _content_to_text(msg.get("content"))
        for k in ("content", "text", "output"):
            if k in resp:
                return _content_to_text(resp[k])
        return json.dumps(resp)
    choices = getattr(resp, "choices", None)
    if choices:
        return _content_to_text(getattr(choices[0].message, "content", ""))
    for k in ("content", "text"):
        if hasattr(resp, k):
            return _content_to_text(getattr(resp, k))
    return str(resp)


def _content_to_text(c) -> str:
    if c is None:
        return ""
    if isinstance(c, list):  # content parts
        return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in c)
    return str(c)


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_ANSWER_RE = re.compile(r"<answer>(.*?)(?:</answer>|$)", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


MAX_JSON_DEPTH = 64  # hazard JSON nests 4 levels; deeper starts are not tried


def _shallow_starts(text: str, max_depth: int = MAX_JSON_DEPTH) -> list[int]:
    """Positions of '{' / '[' whose bracket nesting (counted naively, strings ignored) stays within
    max_depth levels. One linear pass; keeps the decoder away from pathological input such as
    '[[[[...' or '{"a":{"a":...' where every start would recurse to the interpreter limit."""
    stack: list[list[int]] = []  # [pos, depth_at_open, max_depth_seen]
    out: list[int] = []
    for i, ch in enumerate(text):
        if ch in "{[":
            stack.append([i, len(stack), len(stack) + 1])
            if len(stack) > 1:
                stack[-2][2] = max(stack[-2][2], len(stack))
        elif ch in "}]" and stack:
            pos, d0, dmax = stack.pop()
            if dmax - d0 <= max_depth:
                out.append(pos)
            if stack:
                stack[-1][2] = max(stack[-1][2], dmax)
    deepest = 0
    while stack:  # never closed: nesting counted to the end of the text
        pos, d0, dmax = stack.pop()
        deepest = max(deepest, dmax)
        if deepest - d0 <= max_depth:
            out.append(pos)
    return sorted(out)


def _json_candidates(text: str, max_starts: int = 20000):
    """JSON values found in text, scanning '{' / '[' positions left to right (see _shallow_starts).
    A RecursionError inside the decoder is treated like any other parse failure."""
    dec = json.JSONDecoder()
    end = 0
    for n, i in enumerate(_shallow_starts(text)):
        if n >= max_starts:
            break
        if i < end:
            continue
        try:
            obj, end = dec.raw_decode(text, i)
            yield obj
        except (json.JSONDecodeError, RecursionError, ValueError):
            pass


def extract_json_block(text: str) -> Optional[Any]:
    """First valid JSON value carrying hazards in a model answer.

    Handles reasoning text before the JSON (Cosmos Reason style <think>...</think> and
    <answer>...</answer>), markdown fences, and stray braces inside the reasoning. Preference:
    <answer> body > fenced blocks > text after </think> > whole text; within each, a dict with a
    "hazards" key wins over a bare list of hazard dicts, which wins over any other dict."""
    if not text:
        return None
    text = str(text)[:MAX_TEXT_CHARS]
    sources = [m.group(1) for m in _ANSWER_RE.finditer(text)]
    sources += [m.group(1) for m in _FENCE_RE.finditer(text)]
    stripped = _THINK_RE.sub(" ", text)
    if "</think>" in stripped.lower():  # unclosed/odd tags: keep what follows the last </think>
        stripped = re.split(r"</think>", stripped, flags=re.IGNORECASE)[-1]
    sources += [stripped, text]
    fallback = None
    for src in sources:
        cands = list(_json_candidates(src))
        for c in cands:
            if isinstance(c, dict) and "hazards" in c:
                return c
        for c in cands:
            if isinstance(c, list) and c and all(isinstance(x, dict) and "label" in x for x in c):
                return {"hazards": c}
        if fallback is None:
            fallback = next((c for c in cands if isinstance(c, dict)), None)
    return fallback


def normalize_hazard_label(label: str) -> str:
    s = " ".join(str(label)[:500].lower().replace("_", " ").split())
    s = _MEASURE_RE.sub("", s).strip(" ,;:-")
    if s in HAZARD_LABELS:
        return s
    if s in HAZARD_ALIASES:
        return HAZARD_ALIASES[s]
    names = {**{lab: lab for lab in HAZARD_LABELS if lab != "other"}, **HAZARD_ALIASES}
    for k in sorted(names, key=lambda t: (-len(t), t)):
        if re.search(rf"\b{re.escape(k)}\b", s):
            return names[k]
    return s if is_plain_free_label(s) else "other"


def is_plain_free_label(s: str) -> bool:
    """A free-text label short and plain enough to show verbatim (no digits, markup or instructions)."""
    words = s.replace("/", " ").replace("-", " ").split()
    return (bool(_FREE_LABEL_RE.match(s)) and len(words) <= _FREE_LABEL_MAX_WORDS
            and not any(w.strip("'") in _INSTRUCTION_WORDS for w in words))


def _find_geometry(obj: Any, path: str = "") -> list[str]:
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else str(k)
            if str(k).lower() in GEOMETRY_KEYS:
                hits.append(p)
            else:
                hits += _find_geometry(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits += _find_geometry(v, f"{path}[{i}]")
    return hits


def validate_hazards(payload: Any, *, frames: Sequence[str], objects: dict[str, tuple[float, float]],
                     source: str = "vision_llm") -> HazardPassResult:
    """Turn the parsed model JSON into Hazard records, enforcing references and the no-geometry rule.

    `objects` maps object id -> (x, y) site position (from our own primitives)."""
    res = HazardPassResult()
    if payload is None:
        res.status = "no_json"
        return res
    items = payload.get("hazards") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        res.status = "no_json"
        res.dropped.append("response has no 'hazards' list")
        return res
    frame_set = set(frames)
    merged: dict[tuple, Hazard] = {}
    if len(items) > MAX_HAZARDS:
        res.dropped.append(f"{len(items) - MAX_HAZARDS} hazards beyond the first {MAX_HAZARDS} ignored")
        items = items[:MAX_HAZARDS]
    for i, it in enumerate(items):
        if not isinstance(it, dict) or not isinstance(it.get("label"), str) or not it["label"].strip():
            res.dropped.append(f"hazards[{i}]: not an object with a string label")
            continue
        geo = _find_geometry(it)
        if geo:
            res.violations.append(f"hazards[{i}]: model supplied geometry fields {geo}; ignored")
        for text_key in ("label", "reason"):
            v = it.get(text_key)
            if isinstance(v, str) and (_MEASURE_RE.search(v) or _COORD_RE.search(v) or _ASSIGN_RE.search(v)):
                res.violations.append(f"hazards[{i}].{text_key}: measurements in text; removed")
        label = normalize_hazard_label(_ASSIGN_RE.sub("", _COORD_RE.sub("", it["label"])))
        if label == "other" and " ".join(it["label"].lower().split()) != "other":
            res.violations.append(f"hazards[{i}].label: free text not shown verbatim; recorded as 'other'")
        refs_raw = it.get("frame_refs", it.get("frames", []))
        if isinstance(refs_raw, str):
            refs_raw = [refs_raw]
        refs_raw = refs_raw[:64] if isinstance(refs_raw, list) else []
        refs = [str(r) for r in refs_raw if isinstance(r, (str, int)) and not isinstance(r, bool)
                and str(r) in frame_set]
        bad_refs = [r for r in refs_raw if not (isinstance(r, (str, int)) and str(r) in frame_set)]
        if bad_refs:
            res.dropped.append(f"hazards[{i}]: {len(bad_refs)} unknown frame refs removed")
        if not refs:
            res.dropped.append(f"hazards[{i}] '{label}': no valid frame reference; dropped")
            continue
        raw_oid = it.get("object_id")
        oid = str(raw_oid) if isinstance(raw_oid, (str, int)) and not isinstance(raw_oid, bool)             and str(raw_oid) in objects else None
        if raw_oid not in (None, "", "null") and oid is None:
            res.dropped.append(f"hazards[{i}]: unknown object_id {str(raw_oid)[:40]!r}; kept without location")
        try:
            c_raw = it.get("confidence", 0.5)
            if isinstance(c_raw, bool):
                raise ValueError
            conf = float(c_raw)
            if not np.isfinite(conf):
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            conf = 0.5
        conf = float(np.clip(conf, 0.05, 0.95)) * (1.0 if oid else 0.8)
        xy = tuple(float(v) for v in objects[oid]) if oid else None
        key = (label, oid)
        if key in merged:
            h = merged[key]
            h.frame_refs = sorted(set(h.frame_refs) | set(refs))
            h.confidence = max(h.confidence, conf)
        else:
            merged[key] = Hazard(label=label, xy=xy, source=source, confidence=round(conf, 3), frame_refs=sorted(set(refs)))
    res.hazards = list(merged.values())
    return res


# ---------- request building ----------
def encode_jpeg_b64(img_bgr: np.ndarray, max_side: int = 768, quality: int = 85) -> str:
    h, w = img_bgr.shape[:2]
    s = min(1.0, max_side / max(h, w))
    if s < 1.0:
        img_bgr = cv2.resize(img_bgr, (int(round(w * s)), int(round(h * s))), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img_bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("JPEG encoding failed")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def object_visibility(prims: Sequence[Primitive], frames: Sequence[PoseRecord]) -> dict[str, list[str]]:
    from jobs.semantics.camera import PinholeCamera

    out: dict[str, list[str]] = {p.id: [] for p in prims}
    if not prims:
        return out
    centers = np.array([p.pose.xyz for p in prims], dtype=np.float64)
    for f in frames:
        cam = PinholeCamera.from_pose_record(f)
        uv, z = cam.project(centers)
        for p, ok in zip(prims, cam.in_image(uv, z, near=0.2, far=8.0)):
            if ok:
                out[p.id].append(f.frame)
    return out


def build_messages(images: Sequence[tuple[str, str]], prims: Sequence[Primitive],
                   visibility: dict[str, list[str]]) -> list[dict]:
    """OpenAI-compatible multimodal messages. images = [(frame_name, base64 jpeg)]."""
    obj_lines = []
    for p in prims:
        vis = visibility.get(p.id, [])
        obj_lines.append({"id": p.id, "label": p.label, "shape": p.type, "movable": p.movable,
                          "visible_in": vis})
    intro = ("Detected objects (ids you may reference):\n" + json.dumps(obj_lines, indent=1)
             + "\n\nFrames follow. Frame names you may reference: " + ", ".join(n for n, _ in images))
    content: list[dict] = [{"type": "text", "text": intro}]
    for name, b64 in images:
        content.append({"type": "text", "text": f"Frame {name}:"})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    content.append({"type": "text", "text": "List the hazards as JSON now."})
    return [{"role": "system", "content": SYSTEM_PROMPT.format(labels=", ".join(HAZARD_LABELS[:-1]))},
            {"role": "user", "content": content}]


def get_llm():
    """Lazily import the shared client (agents/llm/client.py). None if it is not importable."""
    try:
        from agents.llm.client import LLM
    except Exception as e:
        log.warning("vision LLM client not importable (%s); hazard pass skipped", e)
        return None
    try:
        return LLM()
    except Exception as e:
        log.warning("vision LLM client could not start (%s); hazard pass skipped", e)
        return None


def run_hazard_pass(frames: Sequence[PoseRecord], read_frame, prims: Sequence[Primitive], *,
                    llm=None, n_frames: int = 10) -> HazardPassResult:
    """frames: keyframe candidates; read_frame(name) -> BGR image. Sends 8-12 of them (fewer only if
    fewer exist), chosen for coverage."""
    from jobs.semantics.keyframes import select_keyframes

    n_frames = int(np.clip(n_frames, 8, 12))
    chosen = list(frames)
    if len(chosen) > n_frames:
        chosen = select_keyframes(chosen, k=n_frames).frames
    images = []
    for f in chosen:
        img = read_frame(f.frame)
        if img is not None:
            images.append((f.frame, encode_jpeg_b64(img)))
    if not images:
        return HazardPassResult(status="no_frames")
    sent = [n for n, _ in images]
    llm = llm if llm is not None else get_llm()
    if llm is None:
        return HazardPassResult(status="llm_unavailable", frames_sent=sent)
    vis = object_visibility(prims, [f for f in chosen if f.frame in set(sent)])
    messages = build_messages(images, prims, vis)
    text, payload = "", None
    try:
        resp = llm.chat(role="vision", messages=messages,
                        response_format={"type": "json_schema", "json_schema": {"name": "hazards", "schema": HAZARD_SCHEMA}},
                        temperature=0.2, max_tokens=2048, kind="semantics.hazards")
        text = response_text(resp)
        parsed = getattr(resp, "parsed", None)
        payload = parsed if isinstance(parsed, dict) and "hazards" in parsed else extract_json_block(text)
    except Exception as e:
        # The shared client raises when its own (stricter) JSON check fails; the reply text is still
        # usable, so parse it with the tolerant extractor (reasoning text before the JSON, etc.).
        content = getattr(e, "content", None)
        if not content:
            return HazardPassResult(status="llm_error", frames_sent=sent, dropped=[f"{type(e).__name__}: {e}"[:300]])
        text = content
        payload = extract_json_block(text)
    objects = {p.id: (p.pose.xyz[0], p.pose.xyz[1]) for p in prims}
    res = validate_hazards(payload, frames=sent, objects=objects)
    res.frames_sent = sent
    res.raw_text = text[:4000]
    return res
