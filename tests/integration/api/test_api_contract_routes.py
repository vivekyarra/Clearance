"""Cross-module routes: per-site capture list, iOS upload protocol, twin assets, Pinch Map grid, coach session."""
from __future__ import annotations

import io
import json
from urllib.parse import parse_qs, urlsplit

import numpy as np
import pytest
from api_testkit import build_twin, capture_files, create_site, upload_capture, wait_analysis, wait_for, zip_bytes

IOS_MANIFEST = {
    "capture_id": "cap_local_1", "site_id": "set_by_server",
    "device": {"model": "iPhone14,5", "os": "iOS 18.1", "tier": "T2_VIO_MONO"},
    "role": "scanner", "marker": {"family": "tag36h11", "id": 0, "size_m": 0.16},
    "files": {"frames": "frames", "poses": "poses.jsonl", "feature_points": "feature_points.jsonl",
              "relative_depth": "reldepth"},
    "privacy": {"faces_blurred_on_device": True, "face_detector": "vision", "face_boxes_blurred": 2},
    "started_at": "2026-10-27T10:00:00Z", "duration_s": 95.5,
    "anchoring": {"method": "arkit_image_leveled", "T_arkitworld_site": [1.0] + [0.0] * 14 + [1.0], "relocks": 1},
    "clock": {"t0_uptime_s": 1234.5, "t0_unix_s": 1761559200.0},
    "app": {"version": "1.0", "build": "7"},
    "stats": {"keyframes": 3, "rejected_blur": 1, "limited_tracking_keyframes": 0, "covered_cells": 12,
              "seen_cells": 20},
}


def ios_register_body(manifest: dict, n_parts: int) -> dict:
    """The body apps/ios/Clearance/Upload/UploadManager.swift sends (docs/CONTRACTS.md "Capture upload")."""
    return {"device": manifest["device"], "role": manifest["role"], "marker": manifest["marker"],
            "n_chunks": n_parts, "chunking": "independent", "started_at": manifest["started_at"],
            "duration_s": manifest["duration_s"], "privacy": manifest["privacy"], "capture_files": manifest["files"]}


# ---------------------------------------------------------------- capture list + iOS protocol
def test_site_capture_list_tracks_upload_and_processing(client, recon):
    sid = create_site(client)["site_id"]
    assert client.get(f"/sites/{sid}/captures").json() == []
    assert client.get("/sites/site_missing/captures").status_code == 404

    files = capture_files()
    names = sorted(files)
    r = client.post(f"/sites/{sid}/captures", json={"device": {"model": "iPhone14,5", "tier": "T2_VIO_MONO"},
                                                    "n_chunks": 2})
    cap = r.json()
    cid = cap["capture_id"]
    row = client.get(f"/sites/{sid}/captures").json()[0]
    assert row["capture_id"] == cid and row["upload"] == {"status": "waiting", "n_expected": 2, "n_uploaded": 0,
                                                          "progress": 0.0}
    assert row["processing"]["status"] == "waiting" and row["device"]["model"] == "iPhone14,5"

    client.put(cap["upload"]["chunks"][0]["url"], content=zip_bytes({n: files[n] for n in names[::2]}))
    row = client.get(f"/sites/{sid}/captures").json()[0]
    assert row["upload"]["status"] == "uploading" and row["upload"]["progress"] == 0.5
    client.put(cap["upload"]["chunks"][1]["url"], content=zip_bytes({n: files[n] for n in names[1::2]}))
    assert client.get(f"/sites/{sid}/captures").json()[0]["upload"]["status"] == "uploaded"

    assert client.post(f"/captures/{cid}/complete").status_code == 202
    wait_for(lambda: client.get(f"/captures/{cid}").json()["status"] == "done")
    row = client.get(f"/sites/{sid}/captures").json()[0]
    assert row["processing"] == {"status": "done", "twin_version": 1, "error": None}
    # a second capture is listed after the first, scoped to its site
    upload_capture(client, sid, seed=3)
    other = create_site(client, "Other")["site_id"]
    assert len(client.get(f"/sites/{sid}/captures").json()) == 2
    assert client.get(f"/sites/{other}/captures").json() == []


def test_ios_upload_protocol_keeps_app_fields(client, recon, env):
    sid = create_site(client)["site_id"]
    files = capture_files()
    manifest = dict(IOS_MANIFEST, site_id=sid)
    files["capture.json"] = json.dumps(manifest).encode()
    files["reldepth/000000.png"] = b"\x89PNG"
    names = sorted(files, key=lambda n: (n != "capture.json", n))
    parts = [{n: files[n] for n in names[:3]}, {n: files[n] for n in names[3:]}]

    r = client.post(f"/sites/{sid}/captures", json=ios_register_body(manifest, len(parts)))
    assert r.status_code == 201, r.text
    reg = r.json()
    cid = reg["capture_id"]
    chunks = reg["upload"]["chunks"]
    assert [c["part"] for c in chunks] == [0, 1]
    assert all(c["method"] == "PUT" and c["url"] and "headers" in c for c in chunks)
    assert reg["complete_url"].endswith(f"/captures/{cid}/complete")

    up = client.put(chunks[0]["url"], content=zip_bytes(parts[0]), headers=chunks[0]["headers"])
    assert up.status_code == 200 and up.headers["ETag"]
    # an expired URL is refreshed for the same capture (the app's retry path), never re-registered
    fresh = client.post(f"/captures/{cid}/upload-urls", json={"parts": [1]}).json()
    assert fresh["capture_id"] == cid and [c["part"] for c in fresh["upload"]["chunks"]] == [1]
    client.put(fresh["upload"]["chunks"][0]["url"], content=zip_bytes(parts[1]))

    # the app posts its receipts as the body; the API accepts and ignores them
    receipts = {"capture_id": cid, "site_id": sid, "n_parts": 2, "content_type": "application/zip",
                "parts": [{"index": 0, "name": "part-0000.zip", "size": 1, "sha256": "00"}]}
    done = client.post(reg["complete_url"].replace("http://testserver", ""), json=receipts)
    assert done.status_code == 202, done.text
    wait_for(lambda: client.get(f"/captures/{cid}").json()["status"] == "done")

    stored = json.loads((env["data"] / "captures" / cid / "capture" / "capture.json").read_text())
    assert stored["capture_id"] == cid and stored["site_id"] == sid
    assert stored["files"]["relative_depth"] == "reldepth"
    for k in ("anchoring", "clock", "app", "stats"):
        assert stored[k] == manifest[k]


def test_register_rejects_wrapped_manifest(client):
    """The pre-contract body shape ({"capture": {...}, "n_parts"}) is refused with a schema error."""
    sid = create_site(client)["site_id"]
    r = client.post(f"/sites/{sid}/captures", json={"capture": IOS_MANIFEST, "n_parts": 2})
    assert r.status_code == 422 and "device" in r.text


# ---------------------------------------------------------------- twin assets
@pytest.fixture
def twin_site(client, recon):
    sid = create_site(client)["site_id"]
    build_twin(client, sid)
    return sid


def _set_assets(client, env, sid, assets: dict, files: dict[str, bytes]):
    for key, data in files.items():
        p = env["data"] / key
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    twin = client.get(f"/sites/{sid}/twin").json()
    twin["assets"] = {**twin["assets"], **assets}
    r = client.put(f"/sites/{sid}/twin", json=twin)
    assert r.status_code == 201, r.text
    return r.json()["version"]


def test_twin_assets_served_by_key_and_file_name(client, env, twin_site):
    sid = twin_site
    r = client.get(f"/sites/{sid}/twin/assets/mesh_glb")
    assert r.status_code == 200 and r.content == b"glTF" and r.headers["content-type"] == "model/gltf-binary"
    assert client.get(f"/sites/{sid}/twin/assets/mesh.glb").content == b"glTF"

    v = _set_assets(client, env, sid, {
        "splat": f"sites/{sid}/twin_v1/splat.ply", "occluder_usdz": f"sites/{sid}/twin_v1/occluder.usdz",
        "pointcloud": f"sites/{sid}/twin_v1/points.ply"},
        {f"sites/{sid}/twin_v1/splat.ply": b"ply\nsplat", f"sites/{sid}/twin_v1/occluder.usdz": b"PK\x03\x04usd",
         f"sites/{sid}/twin_v1/points.ply": b"ply\npoints"})
    assert v == 2
    assert client.get(f"/sites/{sid}/twin/assets/splat").content == b"ply\nsplat"
    assert client.get(f"/sites/{sid}/twin/assets/splat.ply").content == b"ply\nsplat"
    occ = client.get(f"/sites/{sid}/twin/assets/occluder.usdz")
    assert occ.status_code == 200 and occ.headers["content-type"] == "model/vnd.usdz+zip"
    assert client.get(f"/sites/{sid}/twin/assets/pointcloud.ply").content == b"ply\npoints"
    assert client.get(f"/api/sites/{sid}/twin/assets/splat.ply?version=2").status_code == 200
    # version 1 has no splat; a wrong extension is not served
    assert client.get(f"/sites/{sid}/twin/assets/splat?version=1").status_code == 404
    assert client.get(f"/sites/{sid}/twin/assets/splat.ksplat").status_code == 404


BAD_ASSETS = {"splat": "../secret.ply", "pointcloud": "sites/{other}/x.ply", "mesh": "analyses/a/b.glb",
              "occluder_usdz": "https://cdn.example.com/o.usdz", "mesh_glb": "http://cdn.example.com/m.glb",
              "capture_id": "cap_1"}


def test_twin_assets_path_safety(client, env, twin_site):
    """PUT refuses asset values outside this site's storage; serving re-checks twins already on disk."""
    sid = twin_site
    other = create_site(client, "Other")["site_id"]
    (env["data"] / "secret.ply").write_bytes(b"secret")
    for f in (f"sites/{other}/x.ply", "analyses/a/b.glb"):
        (env["data"] / f).parent.mkdir(parents=True, exist_ok=True)
        (env["data"] / f).write_bytes(b"data")
    base = client.get(f"/sites/{sid}/twin").json()
    for name, value in BAD_ASSETS.items():
        twin = dict(base, assets={**base["assets"], name: value.format(other=other)})
        r = client.put(f"/sites/{sid}/twin", json=twin)
        assert r.status_code == 422 and f"assets.{name}" in r.text, (name, r.status_code, r.text)
    # a twin written before these checks (or by a job) is still served safely
    twin_path = env["data"] / "sites" / sid / "twin_v1" / "twin.json"
    doc = json.loads(twin_path.read_text())
    doc["assets"] = {**doc["assets"], **{k: v.format(other=other) for k, v in BAD_ASSETS.items()}}
    twin_path.write_text(json.dumps(doc))
    for name in ("splat", "pointcloud", "mesh", "capture_id", "splat.ply.exe", "a%2F..%2Fb"):
        assert client.get(f"/sites/{sid}/twin/assets/{name}").status_code == 404, name
    assert client.get(f"/sites/{sid}/twin/assets/..%2F..%2Fsecret.ply").status_code == 404
    # no open redirect: absolute URLs need an allowlisted https host
    for name in ("occluder_usdz", "mesh_glb"):
        r = client.get(f"/sites/{sid}/twin/assets/{name}", follow_redirects=False)
        assert r.status_code == 404 and "location" not in r.headers, name
    assert client.get("/sites/site_missing/twin/assets/splat").status_code == 404


def test_twin_asset_redirect_to_allowlisted_host(make_client, env, recon):
    c = make_client(asset_hosts=["cdn.example.com"])
    sid = create_site(c)["site_id"]
    build_twin(c, sid)
    twin = c.get(f"/sites/{sid}/twin").json()
    for bad in ("http://cdn.example.com/o.usdz", "https://evil.example/o.usdz",
                "https://user@cdn.example.com/o.usdz", "javascript://cdn.example.com/x", "//cdn.example.com/o"):
        r = c.put(f"/sites/{sid}/twin", json=dict(twin, assets={**twin["assets"], "occluder_usdz": bad}))
        assert r.status_code == 422, bad
    r = c.put(f"/sites/{sid}/twin",
              json=dict(twin, assets={**twin["assets"], "occluder_usdz": "https://cdn.example.com/o.usdz"}))
    assert r.status_code == 201, r.text
    redirect = c.get(f"/sites/{sid}/twin/assets/occluder_usdz", follow_redirects=False)
    assert redirect.status_code == 307 and redirect.headers["location"] == "https://cdn.example.com/o.usdz"


# ---------------------------------------------------------------- Pinch Map grid
def test_bottleneck_npy_route(client, env, recon, orchestrator):
    sid = create_site(client)["site_id"]
    build_twin(client, sid)
    aid = client.post(f"/sites/{sid}/analyses", json={}).json()["analysis_id"]
    bid = wait_analysis(client, aid)["batches"][0]
    assert client.get(f"/sim/batches/{bid}/bottleneck.npy").status_code == 404

    grid = np.zeros((80, 120), np.float32)
    grid[40, 60] = 7.5
    buf = io.BytesIO()
    np.save(buf, grid)
    (env["data"] / "analyses" / aid / "batches" / f"{bid}.bottleneck.npy").write_bytes(buf.getvalue())
    r = client.get(f"/sim/batches/{bid}/bottleneck.npy")
    assert r.status_code == 200 and r.headers["content-type"] == "application/octet-stream"
    got = np.load(io.BytesIO(r.content))
    assert got.dtype == np.float32 and got.shape == (80, 120) and got[40, 60] == 7.5
    assert client.get(f"/api/sim/batches/{bid}/bottleneck.npy").status_code == 200

    # bottleneck_uri in the batch doc wins when it points inside analyses/
    alt = env["data"] / "analyses" / aid / "pinch.npy"
    np.save(alt, grid * 2)
    doc_path = env["data"] / "analyses" / aid / "batches" / f"{bid}.json"
    doc = json.loads(doc_path.read_text())
    doc["bottleneck_uri"] = f"analyses/{aid}/pinch.npy"
    doc_path.write_text(json.dumps(doc))
    assert np.load(io.BytesIO(client.get(f"/sim/batches/{bid}/bottleneck.npy").content))[40, 60] == 15.0
    doc["bottleneck_uri"] = "../../secret.npy"
    doc_path.write_text(json.dumps(doc))
    assert np.load(io.BytesIO(client.get(f"/sim/batches/{bid}/bottleneck.npy").content))[40, 60] == 7.5
    assert client.get("/sim/batches/sb_missing/bottleneck.npy").status_code == 404


# ---------------------------------------------------------------- coach session
def test_coach_session_is_shared_per_site_and_replays_latest(client, twin_site):
    sid = twin_site
    a = client.post("/live", json={"site_id": sid, "mode": "coach"})
    b = client.post("/live", json={"site_id": sid, "mode": "coach"})
    assert a.status_code == 201 and b.status_code == 200
    session = a.json()["session_id"]
    coach = urlsplit(b.json()["coach_url"])
    assert b.json()["session_id"] == session and coach.path == f"/coach/{sid}"
    assert parse_qs(coach.query)["session"] == [session] and parse_qs(coach.query)["token"] == [b.json()["token"]]

    cov = {"cell_m": 0.5, "origin": [0.0, 0.0], "width": 3, "height": 1, "cells": [2, 1, 0]}
    with client.websocket_connect(f"/live/{session}?role=scanner") as scanner:
        assert scanner.receive_json()["type"] == "hello"
        scanner.send_json({"type": "coach", "site_id": sid, "coverage": cov, "prompt": "Scan the door side",
                           "t": 4.0, "status": {"device": "iPhone14,5", "frames": 42, "tracking": "normal",
                                                "bogus": 1}})
        bad = [{"type": "coach", "site_id": "site_other", "prompt": "x"},
               {"type": "coach", "coverage": {**cov, "cells": [1, 2]}},
               {"type": "coach", "coverage": {**cov, "cell_m": 0}},
               {"type": "coach", "coverage": {**cov, "width": 1000, "height": 1000, "cells": []}},
               {"type": "coach", "prompt": 5},
               {"type": "coach", "t": "now"}]
        for m in bad:
            scanner.send_json(m)
            assert scanner.receive_json()["type"] == "error", m
        with client.websocket_connect(f"/live/{session}?role=coach") as coach:
            assert coach.receive_json()["type"] == "hello"
            late = coach.receive_json()
    assert late == {"type": "coach", "site_id": sid, "coverage": cov, "prompt": "Scan the door side", "t": 4.0,
                    "status": {"device": "iPhone14,5", "frames": 42, "tracking": "normal"}}
    # a closed coach session is replaced by a new one
    client.delete(f"/live/{session}")
    assert client.post("/live", json={"site_id": sid, "mode": "coach"}).json()["session_id"] != session
