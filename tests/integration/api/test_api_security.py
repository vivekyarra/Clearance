"""Upload token integrity, path traversal, zip-slip, and 503s when a component is absent."""
from __future__ import annotations

import io
import zipfile

import pytest
from api_testkit import build_twin, capture_files, create_site, upload_capture, zip_bytes

DEVICE = {"model": "iPhone14,5", "tier": "T2_VIO_MONO"}


def _register(c, sid, **kw):
    return c.post(f"/sites/{sid}/captures", json={"device": DEVICE, **kw})


def _flip(s: str, i: int) -> str:
    return s[:i] + ("A" if s[i] != "A" else "B") + s[i + 1:]


def test_upload_token_tamper_rejected(client, env):
    sid = create_site(client)["site_id"]
    cap = _register(client, sid, n_chunks=1).json()
    url = cap["upload"]["chunks"][0]["url"]
    prefix, token = url.rsplit("/", 1)
    body, mac = token.split(".")
    for bad in (f"{_flip(body, 5)}.{mac}", f"{body}.{_flip(mac, 3)}", f"{body}.", "malformed", f"{body}.{mac}.x"):
        r = client.put(f"{prefix}/{bad}", content=b"x")
        assert r.status_code == 403, (bad, r.text)
    # a token signed with another secret is rejected too
    from services.api.security import TokenSigner

    forged = TokenSigner(b"other-secret").sign({"k": f"captures/{cap['capture_id']}/upload/part_0000.zip",
                                                "c": cap["capture_id"], "m": 10}, 60)
    assert client.put(f"{prefix}/{forged}", content=b"x").status_code == 403
    assert not any((env["data"] / "captures").rglob("part_0000.zip"))
    assert client.put(url, content=zip_bytes(capture_files())).status_code == 200


def test_expired_and_oversized_uploads(client, env):
    from services.api.security import TokenSigner

    sid = create_site(client)["site_id"]
    cid = _register(client, sid, n_chunks=1).json()["capture_id"]
    signer = TokenSigner(b"integration-secret")
    key = f"captures/{cid}/upload/part_0000.zip"
    expired = signer.sign({"k": key, "c": cid, "m": 100}, -5)
    r = client.put(f"/upload/{expired}", content=b"x")
    assert r.status_code == 403 and "expired" in r.text
    small = signer.sign({"k": key, "c": cid, "m": 10}, 60)
    assert client.put(f"/upload/{small}", content=b"y" * 11).status_code == 413
    assert not (env["data"] / key).exists()
    assert not list((env["data"] / "captures" / cid / "upload").glob(".tmp_*"))
    # key outside the capture named in the token
    other = signer.sign({"k": "sites/x/site.json", "c": cid, "m": 100}, 60)
    assert client.put(f"/upload/{other}", content=b"{}").status_code == 403


@pytest.mark.parametrize("name", ["../escape.txt", "frames/../../x.jpg", "/etc/passwd", "C:/x.jpg",
                                  "..\\x.jpg", "frames//a.jpg", ".hidden", "a/./b", "NUL.txt", "x\x00y", ""])
def test_path_traversal_in_upload_keys_rejected(client, name):
    sid = create_site(client)["site_id"]
    r = _register(client, sid, n_chunks=0, files=[name])
    assert r.status_code == 422, (name, r.text)
    cid = _register(client, sid, n_chunks=1).json()["capture_id"]
    r = client.post(f"/captures/{cid}/upload-urls", json={"add_files": [name]})
    assert r.status_code == 422, (name, r.text)


def _zip_with(names: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n in names:
            zf.writestr(zipfile.ZipInfo(n), b"data")
        for n, d in capture_files().items():
            zf.writestr(n, d)
    return buf.getvalue()


@pytest.mark.parametrize("entry", ["../../outside.txt", "/abs.txt", "frames/../../../o.txt", "a\\..\\b.txt"])
def test_zip_slip_rejected(client, env, recon, entry):
    sid = create_site(client)["site_id"]
    cap = _register(client, sid, n_chunks=1).json()
    assert client.put(cap["upload"]["chunks"][0]["url"], content=_zip_with([entry])).status_code == 200
    r = client.post(f"/captures/{cap['capture_id']}/complete")
    assert r.status_code == 422, r.text
    assert not (env["tmp"] / "outside.txt").exists() and not (env["data"] / "outside.txt").exists()
    assert client.get(f"/captures/{cap['capture_id']}").json()["status"] == "failed"
    assert recon == []


def test_bad_zip_and_bad_capture_json(client, recon):
    sid = create_site(client)["site_id"]
    cap = _register(client, sid, n_chunks=1).json()
    client.put(cap["upload"]["chunks"][0]["url"], content=b"not a zip")
    assert client.post(f"/captures/{cap['capture_id']}/complete").status_code == 422
    cap = _register(client, sid, n_chunks=1).json()
    files = dict(capture_files(), **{"capture.json": b'{"device": {"model": "x", "tier": "T9"}}'})
    client.put(cap["upload"]["chunks"][0]["url"], content=zip_bytes(files))
    r = client.post(f"/captures/{cap['capture_id']}/complete")
    assert r.status_code == 422 and "Capture schema" in r.text


def test_traversal_in_read_paths(client):
    for path in ("/sim/batches/..%2F..%2Fsites/episodes/0", "/sim/batches/a..b/episodes/0",
                 "/analyses/..%2Fsites", "/sites/..%2F..%2Fetc/twin", "/captures/..%2Fx"):
        r = client.get(path)
        assert r.status_code == 404, (path, r.status_code)


def test_web_dist_served_without_escape(make_client, env):
    dist = env["tmp"] / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>console</html>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    (env["tmp"] / "secret.txt").write_text("secret")
    c = make_client(web_dist=dist)
    assert "console" in c.get("/").text
    assert c.get("/assets/app.js").text == "console.log(1)"
    assert "console" in c.get("/observe/site_x").text  # client-side route
    assert c.get("/..%2Fsecret.txt").status_code == 404
    assert c.get("/assets/missing.js").status_code == 404
    assert c.get("/health").json()["ok"] is True


def test_missing_components_return_503(client, no_components):
    sid = create_site(client)["site_id"]
    cap = upload_capture(client, sid)
    r = client.post(f"/captures/{cap['capture_id']}/complete")
    assert r.status_code == 503 and "component unavailable in this deployment" in r.json()["detail"]
    assert client.get("/usage/summary").status_code == 503
    assert client.get(f"/captures/{cap['capture_id']}").json()["status"] == "registered"


def test_cors_for_vite_dev_server(client):
    r = client.options("/sites", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"
    r = client.options("/sites", headers={"Origin": "http://other.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in r.headers


def test_browser_navigation_to_console_pages_gets_web_app(make_client, env):
    dist = env["tmp"] / "dist_nav"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html>console</html>")
    c = make_client(web_dist=dist)
    sid = create_site(c)["site_id"]
    html = {"accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
    assert "console" in c.get(f"/sites/{sid}", headers=html).text          # page refresh / deep link
    assert "console" in c.get("/analyses/an_x", headers=html).text
    assert c.get(f"/sites/{sid}", headers={"accept": "application/json"}).json()["site_id"] == sid
    assert c.get(f"/sites/{sid}").json()["site_id"] == sid                 # API clients (no HTML accept)
    assert c.get(f"/api/sites/{sid}", headers=html).json()["site_id"] == sid
    assert c.get(f"/sites/{sid}/marker.pdf", headers=html).headers["content-type"].startswith("application/pdf")


def test_scan_built_twin_round_trips_through_put(client, recon):
    """GET -> edit -> PUT works for twins whose assets carry builder provenance (capture_id) or server-stored keys,
    while a new foreign asset value is still refused."""
    sid = create_site(client)["site_id"]
    cap, _ = build_twin(client, sid)
    tw = client.get(f"/sites/{sid}/twin").json()
    tw["assets"]["capture_id"] = cap["capture_id"]  # what the real twin builder records
    r = client.put(f"/sites/{sid}/twin", json=tw)
    assert r.status_code in (200, 201), r.text
    tw2 = client.get(f"/sites/{sid}/twin").json()
    tw2["accuracy"] = {"method": "tape_check", "median_abs_err_m": 0.01, "p90_abs_err_m": 0.03}
    assert client.put(f"/sites/{sid}/twin", json=tw2).status_code in (200, 201)
    tw3 = client.get(f"/sites/{sid}/twin").json()
    tw3["assets"]["splat"] = "sites/another_site/x.ply"
    assert client.put(f"/sites/{sid}/twin", json=tw3).status_code == 422
