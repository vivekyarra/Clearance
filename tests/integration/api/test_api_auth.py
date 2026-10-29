"""Operator token on write routes, site join tokens and per-capture device tokens for the phone flows,
public read routes, WebSocket token + Origin checks, and deployment guards."""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import pytest
from api_testkit import (AUTH, OPERATOR_TOKEN, ROBOT_AWAITING, build_twin, capture_files, create_site,
                         wait_for, zip_bytes)
from starlette.websockets import WebSocketDisconnect

ANON = {"Authorization": ""}  # overrides the operator header the test client sends by default
DEVICE = {"model": "iPhone14,5", "os": "iOS 18", "tier": "T2_VIO_MONO"}


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def join_token(site: dict) -> str:
    tok = parse_qs(urlsplit(site["join_url"]).query)["token"][0]
    assert tok == site["qr"]["token"]
    return tok


@pytest.fixture
def twin_site(client, recon):
    site = create_site(client)
    build_twin(client, site["site_id"])
    return site


def _write_routes(sid: str, cid: str, live_id: str, twin: dict):
    return [
        ("post", "/sites", {"name": "x"}),
        ("put", f"/sites/{sid}/twin", twin),
        ("post", f"/sites/{sid}/analyses", {}),
        ("post", f"/sites/{sid}/clearance-checks", {}),
        ("post", f"/catalog/robots/{ROBOT_AWAITING['robot_id']}/verify", {"verified_by": "mallory"}),
        ("post", "/live", {"site_id": sid}),
        ("post", "/live", {"site_id": sid, "mode": "coach"}),
        ("delete", f"/live/{live_id}", None),
        ("post", f"/sites/{sid}/captures", {"device": DEVICE, "n_chunks": 1}),
        ("post", f"/captures/{cid}/upload-urls", {}),
        ("post", f"/captures/{cid}/complete", None),
    ]


def _call(c, method, path, body, headers):
    kw = {"headers": headers}
    if body is not None:
        kw["json"] = body
    return getattr(c, method)(path, **kw) if method != "delete" else c.delete(path, headers=headers)


def test_every_write_route_needs_a_token(client, env, twin_site, orchestrator, live_sim):
    sid = twin_site["site_id"]
    cid = client.post(f"/sites/{sid}/captures", json={"device": DEVICE, "n_chunks": 1}).json()["capture_id"]
    live_id = client.post("/live", json={"site_id": sid}).json()["session_id"]
    twin = client.get(f"/sites/{sid}/twin").json()
    catalog_file = env["catalog"] / f"{ROBOT_AWAITING['robot_id']}.json"
    before = catalog_file.read_bytes()
    n_sites = len(client.get("/sites").json())
    for headers in (ANON, bearer("wrong-token-wrong-token"), {"Authorization": f"Basic {OPERATOR_TOKEN}"},
                    bearer(OPERATOR_TOKEN + "x")):
        for method, path, body in _write_routes(sid, cid, live_id, twin):
            r = _call(client, method, path, body, headers)
            assert r.status_code == 401, (headers, method, path, r.status_code, r.text)
            assert r.headers.get("www-authenticate") == "Bearer"
            # the /api mount is guarded the same way
            assert _call(client, method, "/api" + path, body, headers).status_code == 401, path
    # nothing changed: no review written, no site, no new twin version, session still open, no analysis
    assert catalog_file.read_bytes() == before
    assert len(client.get("/sites").json()) == n_sites
    assert [t["version"] for t in client.get(f"/sites/{sid}/twins").json()] == [1]
    assert client.get(f"/live/{live_id}").status_code == 200
    assert client.get(f"/sites/{sid}/analyses").json() == [] and orchestrator.calls == []
    # the operator token works on every one of them
    r = client.post(f"/catalog/robots/{ROBOT_AWAITING['robot_id']}/verify", json={"verified_by": "ops"})
    assert r.status_code == 200, r.text


def test_reads_stay_public_and_hide_the_join_token(client, twin_site):
    sid = twin_site["site_id"]
    for path in ("/health", "/sites", f"/sites/{sid}", f"/sites/{sid}/twin", f"/sites/{sid}/twins",
                 "/catalog/robots", f"/sites/{sid}/captures", f"/sites/{sid}/analyses", "/analyses",
                 f"/sites/{sid}/live", f"/sites/{sid}/marker.pdf", f"/sites/{sid}/twin/occupancy.png"):
        assert client.get(path, headers=ANON).status_code == 200, path
    pub = client.get(f"/sites/{sid}", headers=ANON).json()
    assert "token" not in parse_qs(urlsplit(pub["join_url"]).query) and "token" not in pub["qr"]
    op = client.get(f"/sites/{sid}").json()
    assert join_token(op)
    # a join token is not an operator token: it cannot read its way into the QR token either
    assert "token" not in client.get(f"/sites/{sid}", headers=bearer(join_token(op))).json()["qr"]


def test_phone_flow_with_join_and_device_tokens(client, env, recon):
    a, b = create_site(client, "A"), create_site(client, "B")
    jt_a = join_token(a)
    # the join token registers captures on its own site only, and nothing else
    r = client.post(f"/sites/{a['site_id']}/captures", headers=bearer(jt_a), json={"device": DEVICE, "n_chunks": 1})
    assert r.status_code == 201, r.text
    cap = r.json()
    dev = cap["device_token"]
    assert cap["device_token_expires_in_s"] >= 24 * 3600
    assert client.post(f"/sites/{b['site_id']}/captures", headers=bearer(jt_a),
                       json={"device": DEVICE}).status_code == 403
    for method, path, body in (("post", "/sites", {"name": "x"}), ("post", f"/sites/{a['site_id']}/analyses", {}),
                               ("post", "/live", {"site_id": a["site_id"]}),
                               ("post", f"/catalog/robots/{ROBOT_AWAITING['robot_id']}/verify",
                                {"verified_by": "phone"})):
        for tok in (jt_a, dev):
            assert _call(client, method, path, body, bearer(tok)).status_code == 403, (path, tok == dev)
    # the device token covers its own capture: refresh URLs, upload, complete
    r = client.post(f"/captures/{cap['capture_id']}/upload-urls", headers=bearer(dev), json={})
    assert r.status_code == 200
    url = r.json()["upload"]["chunks"][0]["url"]
    assert client.put(url, headers=ANON, content=zip_bytes(capture_files())).status_code == 200
    # but no other capture, even on the same site
    other = client.post(f"/sites/{a['site_id']}/captures", headers=bearer(jt_a), json={"device": DEVICE}).json()
    assert client.post(f"/captures/{other['capture_id']}/complete", headers=bearer(dev)).status_code == 403
    assert client.post(f"/captures/{other['capture_id']}/upload-urls", headers=bearer(dev),
                       json={}).status_code == 403
    r = client.post(f"/captures/{cap['capture_id']}/complete", headers=bearer(dev))
    assert r.status_code == 202, r.text
    st = wait_for(lambda: (lambda s: s if s["status"] in ("done", "failed") else None)(
        client.get(f"/captures/{cap['capture_id']}", headers=ANON).json()))
    assert st["status"] == "done"
    # tokens are not interchangeable with upload tokens
    upload_token = url.rsplit("/", 1)[1]
    assert client.post(f"/sites/{a['site_id']}/captures", headers=bearer(upload_token),
                       json={"device": DEVICE}).status_code == 401
    assert client.put(f"/upload/{jt_a}", headers=ANON, content=b"x").status_code == 403
    assert client.put(f"/upload/{dev}", headers=ANON, content=b"x").status_code == 403


def test_join_token_expires(make_client, env):
    c = make_client(join_ttl_s=-5)
    site = create_site(c)
    r = c.post(f"/sites/{site['site_id']}/captures", headers=bearer(join_token(site)), json={"device": DEVICE})
    assert r.status_code == 401


def test_join_token_for_another_deployment_is_rejected(make_client, env):
    from services.api.security import TokenSigner

    c = make_client()
    site = create_site(c)
    forged = TokenSigner(b"other-secret").sign({"t": "join", "s": site["site_id"]}, 600)
    assert c.post(f"/sites/{site['site_id']}/captures", headers=bearer(forged),
                  json={"device": DEVICE}).status_code == 401


def test_sample_sites_get_no_join_token(client, env):
    from services.api.db import SiteRow

    app = client.app.state.clearance
    with app.db.session() as s:
        s.add(SiteRow(id="ref_sample", name="Sample", type="cafe", marker={"family": "tag36h11", "id": 0, "size_m": 0.16},
                      geometry_source="reference", sample=True, readonly=True))
    site = client.get("/sites/ref_sample").json()
    assert "token" not in site["qr"] and "token" not in parse_qs(urlsplit(site["join_url"]).query)


# ---------------------------------------------------------------- live sessions + WebSockets
def test_coach_session_with_join_token_and_ws_tokens(client, twin_site, live_sim):
    sid = twin_site["site_id"]
    jt = join_token(twin_site)
    other = create_site(client, "Other")
    r = client.post("/live", headers=bearer(jt), json={"site_id": sid, "mode": "coach"})
    assert r.status_code == 201, r.text
    sess = r.json()
    assert client.post("/live", headers=bearer(jt), json={"site_id": other["site_id"],
                                                          "mode": "coach"}).status_code == 403
    assert client.delete(f"/live/{sess['session_id']}", headers=bearer(jt)).status_code == 403
    path = f"/live/{sess['session_id']}?role=scanner"
    # no token / wrong site / garbage -> refused with 4401
    for headers, q in ((ANON, ""), (ANON, f"&token={join_token(other)}"), (ANON, "&token=nope"),
                       (bearer(join_token(other)), "")):
        with client.websocket_connect(path + q, headers=headers) as ws:
            assert "token" in ws.receive_json()["detail"]
            with pytest.raises(WebSocketDisconnect) as e:
                ws.receive_json()
            assert e.value.code == 4401
    # join token (header or query), the session's live token, and the operator all work
    for headers, q in ((bearer(jt), ""), (ANON, f"&token={jt}"), (ANON, f"&token={sess['token']}"), (AUTH, "")):
        with client.websocket_connect(path + q, headers=headers) as ws:
            assert ws.receive_json()["type"] == "hello"
    # a live token is bound to its session
    chaos = client.post("/live", json={"site_id": sid}).json()
    with client.websocket_connect(f"/live/{chaos['session_id']}?token={sess['token']}", headers=ANON) as ws:
        ws.receive_json()
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
        assert e.value.code == 4401
    joy = parse_qs(urlsplit(chaos["joystick_url"]).query)["token"][0]
    with client.websocket_connect(f"/live/{chaos['session_id']}?role=joystick&token={joy}", headers=ANON) as ws:
        assert ws.receive_json()["type"] == "hello"


@pytest.mark.parametrize("origin,ok", [
    ("http://localhost:5173", True), ("http://testserver", True), ("https://console.example.com", True),
    ("https://evil.example", False), ("null", False), ("http://testserver.evil.example", False),
    ("http://localhost:5173.evil.example", False), ("", False)])
def test_ws_origin_allowlist(make_client, recon, live_sim, origin, ok):
    c = make_client(public_web_base="https://console.example.com")
    site = create_site(c)
    build_twin(c, site["site_id"])
    sess = c.post("/live", json={"site_id": site["site_id"], "mode": "coach"}).json()
    if ok:
        with c.websocket_connect(f"/live/{sess['session_id']}", headers={"origin": origin}) as ws:
            assert ws.receive_json()["type"] == "hello"
    else:
        with pytest.raises(WebSocketDisconnect) as e:
            with c.websocket_connect(f"/live/{sess['session_id']}", headers={"origin": origin}) as ws:
                ws.receive_json()
        assert e.value.code == 4403


# ---------------------------------------------------------------- operator token bootstrap + deployment guards
def test_local_operator_token_is_generated_once(make_client, env, capfd):
    c = make_client(operator_token=None, anonymous=True)
    tok_file = env["data"] / ".operator_token"
    tok = tok_file.read_text().strip()
    assert len(tok) >= 32
    err = capfd.readouterr().err
    assert tok in err and err.count(tok) == 1
    assert c.post("/sites", json={"name": "x"}).status_code == 401
    assert c.post("/sites", json={"name": "x"}, headers=bearer(tok)).status_code == 201
    c.__exit__(None, None, None)
    # restart: same token, not printed again
    c2 = make_client(operator_token=None, anonymous=True)
    assert tok not in capfd.readouterr().err
    assert c2.post("/sites", json={"name": "y"}, headers=bearer(tok)).status_code == 201


def test_operator_token_from_env_wins(make_client, env, monkeypatch):
    from services.api.settings import Settings

    monkeypatch.setenv("CLEARANCE_OPERATOR_TOKEN", "env-operator-token-abcdef")
    monkeypatch.setenv("CLEARANCE_CATALOG_WRITEBACK", "0")
    s = Settings.from_env()
    assert s.operator_token == "env-operator-token-abcdef" and s.catalog_writeback is False
    c = make_client(operator_token="env-operator-token-abcdef", anonymous=True)
    assert c.post("/sites", json={"name": "x"}, headers=bearer("env-operator-token-abcdef")).status_code == 201
    assert not (env["data"] / ".operator_token").exists()


def test_short_operator_token_is_refused(make_client):
    with pytest.raises(ValueError):
        make_client(operator_token="short")


def test_deployment_guards(env):
    from services.api.main import create_app
    from services.api.settings import Settings

    base = dict(data_dir=env["data"], sample_dir=env["samples"], catalog_dir=env["catalog"],
                web_dist=env["tmp"] / "nowhere", secret="s", deployed=True)
    with pytest.raises(RuntimeError, match="OPERATOR_TOKEN"):
        create_app(Settings(**base, public_api_base="https://api.example.com"))
    with pytest.raises(RuntimeError, match="PUBLIC_API_BASE"):
        create_app(Settings(**base, operator_token=OPERATOR_TOKEN))
    create_app(Settings(**base, operator_token=OPERATOR_TOKEN, public_api_base="https://api.example.com"))


def test_trusted_hosts_stop_host_header_upload_urls(make_client, env):
    c = make_client(allowed_hosts=["testserver"])
    sid = create_site(c)["site_id"]
    r = c.post(f"/sites/{sid}/captures", json={"device": DEVICE}, headers={**AUTH, "host": "evil.example"})
    assert r.status_code == 400
    r = c.post(f"/sites/{sid}/captures", json={"device": DEVICE, "n_chunks": 1})
    assert r.status_code == 201 and r.json()["upload"]["chunks"][0]["url"].startswith("http://testserver/")


def test_public_api_base_is_used_for_upload_urls_regardless_of_host(make_client, env):
    c = make_client(public_api_base="https://api.example.com")
    sid = create_site(c)["site_id"]
    r = c.post(f"/sites/{sid}/captures", json={"device": DEVICE, "n_chunks": 1},
               headers={**AUTH, "host": "evil.example"})
    assert r.status_code == 201
    assert r.json()["upload"]["chunks"][0]["url"].startswith("https://api.example.com/upload/")
    assert json.dumps(r.json()).count("evil.example") == 0
