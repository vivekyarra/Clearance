"""Upload and request-body abuse: single-use upload keys, racing PUTs, oversized JSON bodies (with and
without Content-Length), oversized capture.json."""
from __future__ import annotations

import json
import threading

import pytest
from api_testkit import capture_files, create_site, zip_bytes

from services.api.limits import DEFAULT_LIMIT, body_limit

DEVICE = {"model": "iPhone14,5", "tier": "T2_VIO_MONO"}


def _register(c, sid, **kw):
    r = c.post(f"/sites/{sid}/captures", json={"device": DEVICE, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def test_upload_url_is_single_use(client, env, recon):
    sid = create_site(client)["site_id"]
    cap = _register(client, sid, n_chunks=1)
    url = cap["upload"]["chunks"][0]["url"]
    good = zip_bytes(capture_files())
    assert client.put(url, content=good).status_code == 200
    r = client.put(url, content=b"replacement")
    assert r.status_code == 409 and "single-use" in r.text
    # a refreshed URL for the same key is refused as well
    fresh = client.post(f"/captures/{cap['capture_id']}/upload-urls", json={}).json()["upload"]["chunks"][0]["url"]
    assert client.put(fresh, content=b"replacement").status_code == 409
    key = env["data"] / "captures" / cap["capture_id"] / "upload" / "part_0000.zip"
    assert key.read_bytes() == good


def test_failed_capture_can_be_reuploaded(client, env, recon):
    sid = create_site(client)["site_id"]
    cap = _register(client, sid, n_chunks=1)
    url = cap["upload"]["chunks"][0]["url"]
    assert client.put(url, content=b"not a zip").status_code == 200
    assert client.post(f"/captures/{cap['capture_id']}/complete").status_code == 422
    assert client.get(f"/captures/{cap['capture_id']}").json()["status"] == "failed"
    assert client.put(url, content=zip_bytes(capture_files())).status_code == 200
    assert client.post(f"/captures/{cap['capture_id']}/complete").status_code == 202


def test_racing_puts_on_one_key_land_once(client, env):
    sid = create_site(client)["site_id"]
    cap = _register(client, sid, n_chunks=1)
    url = cap["upload"]["chunks"][0]["url"]
    codes: list[int] = []
    bodies = [bytes([i]) * 200_000 for i in range(6)]

    def put(b):
        codes.append(client.put(url, content=b).status_code)

    threads = [threading.Thread(target=put, args=(b,)) for b in bodies]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(codes) == [200] + [409] * 5, codes
    data = (env["data"] / "captures" / cap["capture_id"] / "upload" / "part_0000.zip").read_bytes()
    assert data in bodies
    assert not list((env["data"] / "captures" / cap["capture_id"] / "upload").glob(".tmp_*"))


def test_body_limit_table():
    assert body_limit("PUT", "/upload/abc.def") is None
    assert body_limit("PUT", "/api/sites/s/twin") == 8 << 20
    assert body_limit("POST", "/sites/s/captures") == 4 << 20
    assert body_limit("POST", "/sites") == DEFAULT_LIMIT
    assert body_limit("POST", "/upload/abc") == DEFAULT_LIMIT  # only PUT uploads stream unlimited


def test_oversized_json_bodies_are_refused(client):
    big = json.dumps({"name": "x", "pad": "y" * (2 << 20)}).encode()
    r = client.post("/sites", content=big, headers={"Content-Type": "application/json"})
    assert r.status_code == 413
    # no Content-Length (chunked): counted while streaming
    r = client.post("/sites", content=(big[i:i + 65536] for i in range(0, len(big), 65536)),
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 413
    # anonymous callers are refused before the body is read
    r = client.post("/sites", content=big, headers={"Content-Type": "application/json", "Authorization": ""})
    assert r.status_code == 413
    sid = create_site(client)["site_id"]
    r = client.put(f"/sites/{sid}/twin", content=b"{" + b" " * (9 << 20) + b"}",
                   headers={"Content-Type": "application/json"})
    assert r.status_code == 413
    files = [f"frames/{i:07d}_{'a' * 200}.jpg" for i in range(30_000)]  # ~6.5 MB of names
    r = client.post(f"/sites/{sid}/captures", json={"device": DEVICE, "files": files})
    assert r.status_code == 413
    # normal requests are unaffected
    assert client.post("/sites", json={"name": "ok"}).status_code == 201


def test_invalid_content_length(client):
    r = client.post("/sites", content=b"{}", headers={"Content-Type": "application/json", "Content-Length": "x"})
    assert r.status_code == 400


@pytest.mark.parametrize("doc,code", [
    (b'{"device": {"model": "x", "tier": "T2_VIO_MONO"}, "pad": "' + b"z" * (1 << 20) + b'"}', 422),
    (b"[" * 50_000 + b"]" * 50_000, 422),
    (b"[1, 2, 3]", 422),
], ids=["over_1mb", "deep_nesting", "not_an_object"])
def test_capture_json_is_capped(client, recon, doc, code):
    sid = create_site(client)["site_id"]
    cap = _register(client, sid, n_chunks=1)
    files = dict(capture_files(), **{"capture.json": doc})
    assert client.put(cap["upload"]["chunks"][0]["url"], content=zip_bytes(files)).status_code == 200
    r = client.post(f"/captures/{cap['capture_id']}/complete")
    assert r.status_code == code, r.text
    assert recon == []
