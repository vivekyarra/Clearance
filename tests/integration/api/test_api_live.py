"""WS /live/{session}: 20 Hz state frames, joystick control, coach relay, session lifecycle."""
from __future__ import annotations

import json
import time

from urllib.parse import parse_qs, urlsplit

import pytest
from api_testkit import build_twin, create_site
from starlette.websockets import WebSocketDisconnect


def _state_frames(ws, n):
    frames = []
    while len(frames) < n:
        m = ws.receive_json()
        if m["type"] == "state":
            frames.append(m)
    return frames


@pytest.fixture
def site_with_twin(client, recon):
    sid = create_site(client)["site_id"]
    build_twin(client, sid)
    return sid


def test_live_chaos_session(client, site_with_twin, live_sim, env):
    r = client.post("/live", json={"site_id": site_with_twin, "density": "peak", "seed": 3})
    assert r.status_code == 201, r.text
    sess = r.json()
    sid = sess["session_id"]
    assert sess["robot_id"] == "acme_tray_t1" and sess["hz"] == 20.0
    joy = urlsplit(sess["joystick_url"])
    assert joy.path == f"/joystick/{sid}" and parse_qs(joy.query) == {"token": [sess["token"]]}
    sim = live_sim.instances[-1]
    assert sim.density == "peak" and sim.seed == 3 and sim.task.sequence == ["DOCK", "PICKUP", "T1", "DOCK"]

    with client.websocket_connect(f"/live/{sid}?role=joystick") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and hello["mode"] == "chaos"
        t0 = time.monotonic()
        frames = _state_frames(ws, 10)
        elapsed = time.monotonic() - t0
        assert [f["seq"] for f in frames] == sorted(f["seq"] for f in frames)
        assert frames[0]["robot"]["v"] == 0.5  # numpy scalar serialised
        assert 0.25 < elapsed < 2.0, elapsed  # paced near 20 Hz, not a burst
        ws.send_json({"type": "joystick", "vx": 3.0, "vy": 4.0})  # clamped to 1.4 m/s
        ws.send_json({"type": "joystick", "vx": 0.5, "vy": -0.2})
        ws.send_json({"type": "ping"})
        pong = next(m for m in iter(ws.receive_json, None) if m["type"] == "pong")
        assert pong["type"] == "pong"
        ws.send_text("not json")
        err = next(m for m in iter(ws.receive_json, None) if m["type"] == "error")
        assert "JSON" in err["detail"]
        ws.send_json({"type": "joystick", "vx": "fast"})
        next(m for m in iter(ws.receive_json, None) if m["type"] == "error")
    (vx, vy), second = sim.joystick[0], sim.joystick[1]
    assert abs((vx ** 2 + vy ** 2) ** 0.5 - 1.4) < 1e-9 and abs(vx / vy - 0.75) < 1e-9
    assert second == (0.5, -0.2)
    deadline = time.monotonic() + 10  # the release runs after the socket closes; slow runners need the margin
    while sim.joystick[-1] != (0.0, 0.0) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert sim.joystick[-1] == (0.0, 0.0)  # released when the last client leaves

    info = client.get(f"/live/{sid}").json()
    assert info["n_frames"] >= 10 and info["n_events"] >= 1
    events = (env["data"] / "live" / sid / "events.jsonl").read_text().strip().splitlines()
    assert json.loads(events[0])["event"]["type"] == "near_miss"
    assert client.delete(f"/live/{sid}").status_code == 200
    assert client.get(f"/live/{sid}").status_code == 404


def test_coach_relay_between_scanner_and_coach_page(client, site_with_twin):
    r = client.post("/live", json={"site_id": site_with_twin, "mode": "coach"})
    sid = r.json()["session_id"]
    assert [s["session_id"] for s in client.get(f"/sites/{site_with_twin}/live").json()] == [sid]
    with client.websocket_connect(f"/live/{sid}?role=coach") as coach, \
            client.websocket_connect(f"/live/{sid}?role=scanner") as scanner:
        assert coach.receive_json()["type"] == "hello"
        assert scanner.receive_json()["type"] == "hello"
        cov = {"cell_m": 0.5, "origin": [-1.0, -0.5], "width": 2, "height": 2, "cells": [0, 1, 2, 1]}
        scanner.send_json({"type": "coach", "site_id": site_with_twin, "coverage": cov,
                           "prompt": "left corner not covered", "t": 12.5, "extra": "dropped"})
        msg = coach.receive_json()
        assert msg == {"type": "coach", "site_id": site_with_twin, "coverage": cov,
                       "prompt": "left corner not covered", "t": 12.5}
        coach.send_json({"type": "coach", "prompt": "walk to the door", "t": 13.0})
        assert scanner.receive_json() == {"type": "coach", "site_id": site_with_twin, "coverage": None,
                                          "prompt": "walk to the door", "t": 13.0}
        scanner.send_json({"type": "joystick", "vx": 1, "vy": 0})
        assert "no simulation" in scanner.receive_json()["detail"]


def test_two_viewers_share_one_stream(client, site_with_twin, live_sim):
    sid = client.post("/live", json={"site_id": site_with_twin}).json()["session_id"]
    with client.websocket_connect(f"/live/{sid}?role=console") as a, \
            client.websocket_connect(f"/live/{sid}?role=joystick") as b:
        a.receive_json()
        b.receive_json()
        fa, fb = _state_frames(a, 5), _state_frames(b, 5)
    assert len(live_sim.instances) == 1
    # both clients see the same session clock (one sim stepped once per tick)
    assert set(f["seq"] for f in fa) & set(f["seq"] for f in fb)


def test_live_errors(client, site_with_twin, no_components):
    with client.websocket_connect("/live/live_unknown") as ws:
        assert ws.receive_json()["type"] == "error"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    assert client.post("/live", json={"site_id": site_with_twin}).status_code == 503
    assert client.post("/live", json={"site_id": site_with_twin, "robot_id": "acme_cart_c2"}).status_code == 422
    assert client.post("/live", json={"site_id": "site_nope"}).status_code == 404
