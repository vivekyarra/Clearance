"""Hosted deployment config: API auth on, catalog write-back off, WebSocket frame cap, no CDN scripts."""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def _env_file(p: Path) -> dict[str, str]:
    out = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.split("#", 1)[0].strip()
    return out


def test_dockerfile_caps_websocket_frames():
    text = (REPO / "infra" / "Dockerfile.api").read_text(encoding="utf-8")
    cmd = text[text.index("CMD ["):]
    assert re.search(r'"--ws-max-size",\s*"131072"', cmd), cmd


def test_api_endpoint_env_turns_auth_on_and_writeback_off():
    env = _env_file(REPO / "infra" / "nebius" / "endpoints" / "api.env")
    assert env["CLEARANCE_CATALOG_WRITEBACK"] == "0"
    assert env["ENDPOINT_AUTH"] in ("none", "token")
    script = (REPO / "infra" / "nebius" / "deploy_api_endpoint.sh").read_text(encoding="utf-8")
    for needle in ('--env "CLEARANCE_DEPLOYED=1"', '--env "CLEARANCE_OPERATOR_TOKEN=$OPERATOR_TOKEN"',
                   'CLEARANCE_CATALOG_WRITEBACK=${CLEARANCE_CATALOG_WRITEBACK:-0}', "CLEARANCE_ALLOWED_HOSTS=",
                   '"${AUTH_ARGS[@]}"'):
        assert needle in script, needle
    assert "\r\n" not in script  # runs under bash on Linux


def test_deployed_settings_refuse_unsafe_config(tmp_path, monkeypatch):
    import pytest

    from services.api.settings import Settings

    monkeypatch.setenv("CLEARANCE_DEPLOYED", "1")
    monkeypatch.setenv("CLEARANCE_DATA", str(tmp_path))
    monkeypatch.delenv("CLEARANCE_OPERATOR_TOKEN", raising=False)
    monkeypatch.delenv("CLEARANCE_PUBLIC_API_BASE", raising=False)
    monkeypatch.delenv("CLEARANCE_ALLOWED_HOSTS", raising=False)
    s = Settings.from_env()
    assert s.deployed
    with pytest.raises(RuntimeError) as e:
        s.check_deployment()
    assert "CLEARANCE_OPERATOR_TOKEN" in str(e.value) and "CLEARANCE_PUBLIC_API_BASE" in str(e.value)
    monkeypatch.setenv("CLEARANCE_OPERATOR_TOKEN", "x" * 32)
    monkeypatch.setenv("CLEARANCE_ALLOWED_HOSTS", "api.example.com, *.nebius.cloud")
    s = Settings.from_env()
    assert s.allowed_hosts == ["api.example.com", "*.nebius.cloud"]
    s.check_deployment()


def test_observer_page_loads_no_cdn_scripts():
    obs = REPO / "apps" / "web" / "src" / "observe"
    for f in ("faceblur.ts", "apriltag.ts"):
        text = (obs / f).read_text(encoding="utf-8")
        assert not re.search(r"https?://", text.split("*/", 1)[1]), f
    model = obs / "models" / "blaze_face_short_range.tflite"
    assert model.is_file() and model.stat().st_size < 1 << 20
    pkg = (REPO / "apps" / "web" / "package.json").read_text(encoding="utf-8")
    assert '"@mediapipe/tasks-vision": "0.10.14"' in pkg and '"@techstark/opencv-js": "4.10.0-release.1"' in pkg
