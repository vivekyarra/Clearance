"""Unit tests for the cross-module contracts: job spec loading, coach messages, capture schema, join URL."""
from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from clearance_core import schemas, storage
from services.api import tasks
from services.api.live import normalize_coach
from services.api.service import App


# ---------------------------------------------------------------- job spec (CLEARANCE_JOB_SPEC / _B64 / _KEY)
def _b64(obj) -> str:
    return base64.b64encode(json.dumps(obj).encode()).decode()


def test_load_spec_forms_and_precedence(tmp_path):
    spec = {"job_id": "j1", "entrypoint": "json:dumps", "kwargs": {"obj": 1}}
    assert tasks.load_spec("CLEARANCE_JOB_SPEC", {"CLEARANCE_JOB_SPEC_B64": _b64(spec)}) == spec
    (tmp_path / "jobs" / "j1").mkdir(parents=True)
    (tmp_path / "jobs" / "j1" / "spec.json").write_text(json.dumps(spec))
    env = {"CLEARANCE_JOB_SPEC_KEY": "jobs/j1/spec.json", "CLEARANCE_DATA": str(tmp_path)}
    assert tasks.load_spec("CLEARANCE_JOB_SPEC", env) == spec
    raw_first = {"CLEARANCE_JOB_SPEC": json.dumps({"job_id": "raw", "entrypoint": "m:f"}),
                 "CLEARANCE_JOB_SPEC_B64": _b64(spec)}
    assert tasks.load_spec("CLEARANCE_JOB_SPEC", raw_first)["job_id"] == "raw"


@pytest.mark.parametrize("env,match", [
    ({}, "no job spec"),
    ({"CLEARANCE_JOB_SPEC_B64": "%%%"}, "base64"),
    ({"CLEARANCE_JOB_SPEC": "{oops"}, "JSON"),
    ({"CLEARANCE_JOB_SPEC": "[]"}, "object"),
    ({"CLEARANCE_JOB_SPEC_KEY": "../outside.json"}, "invalid storage key"),
])
def test_load_spec_errors(env, match):
    with pytest.raises(tasks.SpecError, match=match):
        tasks.load_spec("CLEARANCE_JOB_SPEC", env)


def test_main_runs_b64_spec(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(storage, "DATA", tmp_path)
    spec = {"job_id": "job_b64", "entrypoint": "json:dumps", "kwargs": {"obj": {"ok": True}}}
    assert tasks.main([], env={"CLEARANCE_JOB_SPEC_B64": _b64(spec)}) == 0
    assert json.loads((tmp_path / "jobs" / "job_b64" / "result.json").read_text()) == '{"ok": true}'
    assert json.loads(capsys.readouterr().out.strip())["status"] == "succeeded"


def test_main_reports_bad_spec(capsys):
    assert tasks.main([], env={"CLEARANCE_JOB_SPEC_B64": _b64({"job_id": "x"})}) == 2
    assert "entrypoint" in capsys.readouterr().err


# ---------------------------------------------------------------- coach messages
COV = {"cell_m": 0.5, "origin": [-2, 1.5], "width": 2, "height": 1, "cells": [0, 2]}


def test_normalize_coach_fixed_form():
    out = normalize_coach({"type": "coach", "coverage": COV, "prompt": "p", "t": 3, "from": "x"}, "site_a")
    assert out == {"type": "coach", "site_id": "site_a", "prompt": "p", "t": 3.0,
                   "coverage": {"cell_m": 0.5, "origin": [-2.0, 1.5], "width": 2, "height": 1, "cells": [0, 2]}}
    bare = normalize_coach({"type": "coach"}, "site_a")
    assert bare["coverage"] is None and bare["prompt"] is None and bare["t"] > 1e9
    assert len(normalize_coach({"prompt": "x" * 900}, "s")["prompt"]) == 500


@pytest.mark.parametrize("msg", [
    {"site_id": "other"},
    {"coverage": [[0, 1]]},
    {"coverage": {**COV, "origin": [0]}},
    {"coverage": {**COV, "width": True}},
    {"coverage": {**COV, "cells": [0, 256]}},
    {"coverage": {**COV, "cells": [0, 1.5]}},
    {"coverage": {**COV, "cell_m": float("nan")}},
    {"t": float("inf")},
    {"prompt": ["a"]},
])
def test_normalize_coach_rejects(msg):
    with pytest.raises(ValueError):
        normalize_coach({"type": "coach", **msg}, "site_a")


# ---------------------------------------------------------------- capture.json written by the iOS app
def test_capture_schema_keeps_app_fields_and_relative_depth():
    doc = {"capture_id": "c", "site_id": "s", "role": "scanner",
           "device": {"model": "iPhone14,5", "os": "iOS 18", "tier": "T2_VIO_MONO"},
           "files": {"frames": "frames", "relative_depth": "reldepth"},
           "anchoring": {"method": "arkit_image_leveled", "T_arkitworld_site": [0.0] * 16, "relocks": 0},
           "clock": {"t0_uptime_s": 1.0, "t0_unix_s": 2.0}, "app": {"version": "1.0"},
           "stats": {"keyframes": 10}}
    out = schemas.Capture.model_validate(doc).model_dump(mode="json")
    assert out["files"]["relative_depth"] == "reldepth"
    for k in ("anchoring", "clock", "app", "stats"):
        assert out[k] == doc[k]
    plain = schemas.Capture.model_validate({k: doc[k] for k in ("capture_id", "site_id", "role", "device")})
    assert plain.anchoring is None and plain.files.relative_depth is None


# ---------------------------------------------------------------- site QR code
def test_join_url_encodes_api_and_name():
    row = SimpleNamespace(id="site_ab12", name="Café & Bar", marker={"family": "tag36h11", "id": 0, "size_m": 0.16})
    url = App.join_url(row, "http://10.0.0.5:8000/", "https://console.example.com/")
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://console.example.com/join/site_ab12"
    assert parse_qs(parts.query) == {"api": ["http://10.0.0.5:8000"], "name": ["Café & Bar"]}
    qr = App.qr_payload(row, "http://10.0.0.5:8000", "https://console.example.com")
    assert qr["join_url"] == url and qr["kind"] == "clearance-site" and qr["v"] == 1
