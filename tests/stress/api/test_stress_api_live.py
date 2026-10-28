"""Live sessions under abuse: hostile joystick/coach values, floods, oversized frames, client caps, session
exhaustion (per-site cap, per-IP budget, unclaimed TTL)."""
from __future__ import annotations

import json
import math
import random
import time

import pytest
from api_testkit import build_twin, create_site
from starlette.websockets import WebSocketDisconnect

from services.api.live import (MAX_MSG_BYTES, KeyedRateLimiter, TokenBucket, clamp_joystick, normalize_coach,
                               origin_allowed)


@pytest.fixture
def site(client, recon):
    s = create_site(client)
    build_twin(client, s["site_id"])
    return s


def _until(ws, kind, limit=400):
    for _ in range(limit):
        m = ws.receive_json()
        if m["type"] == kind:
            return m
    raise AssertionError(f"no {kind} message")


# ---------------------------------------------------------------- value validation (property style)
HOSTILE = [10 ** 400, -(10 ** 400), 10 ** 5000, float("nan"), float("inf"), -float("inf"), True, False, None, "1",
           "nan", [], {}, [1.0], 1e308, -1e308, 2 ** 1023 * 1.9999]


def test_clamp_joystick_never_raises_unexpected_errors():
    rng = random.Random(7)
    pool = HOSTILE + [rng.uniform(-1e6, 1e6) for _ in range(200)] + [rng.randint(-10 ** 30, 10 ** 30) for _ in range(50)]
    for _ in range(5000):
        vx, vy = rng.choice(pool), rng.choice(pool)
        try:
            out = clamp_joystick(vx, vy)
        except ValueError:
            continue
        assert all(math.isfinite(v) for v in out)
        assert math.hypot(*out) <= 1.4 + 1e-9


def test_normalize_coach_rejects_overflowing_numbers_with_value_error():
    for bad in ({"t": 10 ** 400}, {"t": float("nan")},
                {"coverage": {"cell_m": 10 ** 400, "origin": [0, 0], "width": 1, "height": 1, "cells": [0]}},
                {"coverage": {"cell_m": 0.5, "origin": [10 ** 400, 0], "width": 1, "height": 1, "cells": [0]}},
                {"coverage": {"cell_m": 0.5, "origin": [0, 0], "width": 10 ** 400, "height": 1, "cells": []}}):
        with pytest.raises(ValueError):
            normalize_coach(bad, "s")
    # status numbers that overflow are dropped, not fatal
    out = normalize_coach({"t": 1.0, "status": {"frames": 10 ** 400, "covered_frac": 0.5}}, "s")
    assert out["status"] == {"covered_frac": 0.5}


def test_token_bucket_and_keyed_limiter():
    b = TokenBucket(rate=2, burst=3, now=0.0)
    assert [b.take(0.0) for _ in range(4)] == [True, True, True, False]
    assert b.take(0.5) and not b.take(0.5)
    lim = KeyedRateLimiter(per_minute=2, max_keys=3)
    assert lim.allow("a", 0.0) and lim.allow("a", 0.0) and not lim.allow("a", 1.0)
    assert lim.allow("a", 31.0)  # refills at 2 per minute
    for k in "bcdef":
        lim.allow(k, 0.0)
    assert len(lim._b) == 3  # bounded memory


@pytest.mark.parametrize("origin,host,ok", [
    (None, "h", True), ("http://h", "h", True), ("https://h:8443", "h:8443", True), ("http://H", "h", True),
    ("http://h.evil", "h", False), ("http://evil", "h", False), ("null", "h", False), ("", "h", False),
    ("ftp://h", "h", False), ("http://h", None, False), ("http://localhost:5173/", None, True),
    ("http://[::1", "h", False)])
def test_origin_allowed(origin, host, ok):
    assert origin_allowed(origin, host, ["http://localhost:5173"]) is ok


# ---------------------------------------------------------------- WebSocket behaviour
def test_hostile_joystick_frames_keep_the_connection(client, site, live_sim):
    sess = client.post("/live", json={"site_id": site["site_id"]}).json()
    with client.websocket_connect(f"/live/{sess['session_id']}?role=joystick") as ws:
        assert ws.receive_json()["type"] == "hello"
        big = "1" + "0" * 400
        for raw in ('{"type":"joystick","vx":%s,"vy":0}' % big, '{"type":"joystick","vx":NaN,"vy":0}',
                    '{"type":"joystick","vx":Infinity,"vy":0}', '{"type":"joystick","vx":1e400,"vy":0}',
                    '{"type":"joystick","vx":true,"vy":0}', '{"type":"coach","t":%s}' % big,
                    '{"type":"joystick","vx":%s,"vy":0}' % ("9" * 5000)):
            ws.send_text(raw)
            assert _until(ws, "error")["type"] == "error", raw
        ws.send_json({"type": "ping"})
        assert _until(ws, "pong")["type"] == "pong"  # still connected
    sim = live_sim.instances[-1]
    assert all(math.isfinite(v) for xy in sim.joystick for v in xy)


def test_oversized_messages_refused_before_parsing(client, site, live_sim):
    sess = client.post("/live", json={"site_id": site["site_id"], "mode": "coach"}).json()
    with client.websocket_connect(f"/live/{sess['session_id']}?role=scanner") as ws:
        ws.receive_json()
        # fewer characters than the limit, more bytes: still refused
        text = json.dumps({"type": "coach", "prompt": "é" * (MAX_MSG_BYTES // 2 + 10)}, ensure_ascii=False)
        assert len(text) < MAX_MSG_BYTES < len(text.encode())
        ws.send_text(text)
        assert "too large" in _until(ws, "error")["detail"]
        ws.send_text("[" * (MAX_MSG_BYTES + 1))
        assert "too large" in _until(ws, "error")["detail"]
        ws.send_text("[" * 100_000)  # deep nesting under the size cap (RecursionError in json.loads)
        assert _until(ws, "error")["type"] == "error"
        ws.send_json({"type": "ping"})
        assert _until(ws, "pong")


def test_client_cap_per_session(make_client, recon, live_sim):
    c = make_client(live_max_clients=2)
    s = create_site(c)
    build_twin(c, s["site_id"])
    sid = c.post("/live", json={"site_id": s["site_id"], "mode": "coach"}).json()["session_id"]
    with c.websocket_connect(f"/live/{sid}") as a, c.websocket_connect(f"/live/{sid}") as b:
        a.receive_json(), b.receive_json()
        with c.websocket_connect(f"/live/{sid}") as third:
            assert "2 clients" in third.receive_json()["detail"]
            with pytest.raises(WebSocketDisconnect) as e:
                third.receive_json()
            assert e.value.code == 4429
    with c.websocket_connect(f"/live/{sid}") as again:  # slots free up when clients leave
        assert again.receive_json()["type"] == "hello"


def test_message_flood_is_rate_limited_then_disconnected(make_client, recon, live_sim):
    c = make_client(ws_msg_rate=1.0, ws_msg_burst=5.0)
    s = create_site(c)
    build_twin(c, s["site_id"])
    sid = c.post("/live", json={"site_id": s["site_id"], "mode": "coach"}).json()["session_id"]
    with c.websocket_connect(f"/live/{sid}") as ws:
        ws.receive_json()
        for _ in range(20):
            ws.send_json({"type": "ping"})
        got = [ws.receive_json() for _ in range(6)]
        assert sum(m["type"] == "pong" for m in got) == 5
        assert any(m["type"] == "error" and "rate limit" in m["detail"] for m in got)
        for _ in range(400):
            ws.send_json({"type": "ping"})
        with pytest.raises(WebSocketDisconnect) as e:
            for _ in range(500):
                ws.receive_json()
        assert e.value.code == 4408


# ---------------------------------------------------------------- session exhaustion
def test_per_site_cap_leaves_other_sites_usable(make_client, recon, live_sim):
    c = make_client(live_max_per_site=3)
    a, b = create_site(c, "A"), create_site(c, "B")
    for s in (a, b):
        build_twin(c, s["site_id"])
    for _ in range(3):
        assert c.post("/live", json={"site_id": a["site_id"]}).status_code == 201
    r = c.post("/live", json={"site_id": a["site_id"]})
    assert r.status_code == 429 and "already has 3" in r.text
    assert c.post("/live", json={"site_id": b["site_id"]}).status_code == 201


def test_unclaimed_sessions_expire_quickly(make_client, recon, live_sim):
    c = make_client(live_unclaimed_ttl_s=0.05, live_max_sessions=4)
    s = create_site(c)
    build_twin(c, s["site_id"])
    ids = [c.post("/live", json={"site_id": s["site_id"]}).json()["session_id"] for _ in range(2)]
    with c.websocket_connect(f"/live/{ids[1]}") as ws:
        ws.receive_json()
    time.sleep(0.1)
    # creating more sessions prunes the never-joined one; the one a client used stays (idle TTL applies)
    for _ in range(2):
        assert c.post("/live", json={"site_id": s["site_id"]}).status_code == 201
    assert c.get(f"/live/{ids[0]}").status_code == 404
    assert c.get(f"/live/{ids[1]}").status_code == 200


def test_per_ip_budget_for_join_token_holders(make_client, recon, live_sim):
    c = make_client(live_create_per_min=2)
    sites = [create_site(c, f"S{i}") for i in range(4)]
    codes = []
    for s in sites:
        jt = s["qr"]["token"]
        codes.append(c.post("/live", json={"site_id": s["site_id"], "mode": "coach"},
                            headers={"Authorization": f"Bearer {jt}"}).status_code)
    assert codes == [201, 201, 429, 429]
    # joining the already open coach session of a site is not a new session and not limited
    jt0 = sites[0]["qr"]["token"]
    assert c.post("/live", json={"site_id": sites[0]["site_id"], "mode": "coach"},
                  headers={"Authorization": f"Bearer {jt0}"}).status_code == 200
    # the operator is not limited
    assert c.post("/live", json={"site_id": sites[3]["site_id"], "mode": "coach"}).status_code == 201
