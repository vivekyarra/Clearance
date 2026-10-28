"""End to end: site -> capture upload -> twin -> edited version -> Clearance check -> report -> replay."""
from __future__ import annotations

import json

import cv2
import numpy as np
from api_testkit import build_twin, create_site, upload_capture, wait_analysis, wait_for


def test_full_flow(client, recon, orchestrator, usage):
    site = create_site(client)
    sid = site["site_id"]
    assert site["qr"]["site_id"] == sid and f"/join/{sid}?api=" in site["qr"]["join_url"]
    assert site["marker_pdf_url"].endswith(f"/sites/{sid}/marker.pdf")
    assert site["geometry_source"] == "scan"

    cap, st = build_twin(client, sid)
    assert st["twin_version"] == 1 and st["missing"] == []
    assert recon[0]["n_poses"] == 3 and recon[0]["tier"] == "T2_VIO_MONO"
    assert recon[0]["out_prefix"].startswith("jobs/job_recon_")

    twin = client.get(f"/sites/{sid}/twin").json()
    assert twin["version"] == 1 and twin["site_id"] == sid
    assert twin["occupancy"]["uri"] == f"sites/{sid}/twin_v1/occ.png"
    assert twin["assets"]["mesh_glb"] == f"sites/{sid}/twin_v1/mesh.glb"
    png = client.get(f"/sites/{sid}/twin/occupancy.png")
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"
    img = cv2.imdecode(np.frombuffer(png.content, np.uint8), cv2.IMREAD_GRAYSCALE)
    assert img.shape == (80, 120)

    # edit: move the table, save as v2
    twin["primitives"][0]["pose"]["xyz"] = [3.2, 2.0, 0.375]
    r = client.put(f"/sites/{sid}/twin", json=twin)
    assert r.status_code == 201, r.text
    v2 = r.json()
    assert v2["version"] == 2 and v2["twin_id"] != twin["twin_id"]
    assert v2["occupancy"]["uri"] == f"sites/{sid}/twin_v2/occ.png"
    # stale base version is rejected; versions stay immutable
    stale = client.put(f"/sites/{sid}/twin", json=twin)
    assert stale.status_code == 409 and stale.json()["detail"]["latest_version"] == 2
    assert client.get(f"/sites/{sid}/twin?version=1").json()["primitives"][0]["pose"]["xyz"] == [3.0, 2.0, 0.375]
    assert [t["version"] for t in client.get(f"/sites/{sid}/twins").json()] == [1, 2]

    # Clearance check on v2 (default task: dock -> pickup -> dests -> dock, reviewed robots only)
    r = client.post(f"/sites/{sid}/analyses", json={"business": {"staff_cost_per_h": 15}})
    assert r.status_code == 202, r.text
    aid = r.json()["analysis_id"]
    done = wait_analysis(client, aid)
    assert done["status"] == "done" and done["progress"] == 1.0
    assert done["verdicts"][0]["label"] == "Not cleared"
    call = orchestrator.calls[0]
    assert call["twin_version"] == 2 and call["robots"] == ["acme_tray_t1"]
    assert call["task"] == ["DOCK", "PICKUP", "T1", "DOCK"]

    rep = client.get(f"/analyses/{aid}/report").json()
    assert "[f_001]" in rep["markdown"] and len(rep["facts"]) == 2
    chips = {c["fact_id"]: c for c in rep["chips"]}
    bid = done["batches"][0]
    assert chips["f_001"]["link"] == f"/sim/batches/{bid}/episodes/0"
    assert chips["f_002"]["link"] == f"/sites/{sid}/twin?version=2"
    assert client.get(f"/analyses/{aid}/facts").json()[0]["fact_id"] == "f_001"
    assert client.get(f"/analyses/{aid}/investigation").json()["model_id"] == "deterministic-policy"

    ep = client.get(f"/sim/batches/{bid}/episodes/1").json()
    assert ep["n_episodes"] == 2 and ep["n_frames"] == 5 and ep["events"][0]["type"] == "near_miss"
    assert client.get(f"/sim/batches/{bid}/episodes/2").status_code == 404

    # alias route + content-addressed reuse of an identical request
    again = client.post(f"/sites/{sid}/clearance-checks", json={"business": {"staff_cost_per_h": 15}})
    assert again.status_code == 200 and again.json()["analysis_id"] == aid and again.json()["cached"]
    assert len(orchestrator.calls) == 1

    usage_r = client.get("/usage/summary").json()
    assert usage_r["calls_by_model"] == {"nano": 3}
    assert usage_r["api_jobs"]["recon"]["count"] == 1 and usage_r["api_jobs"]["analysis"]["count"] == 1

    # the /api prefix used by the built web console reaches the same routes
    assert client.get(f"/api/sites/{sid}").json()["latest_twin_version"] == 2


def test_capture_complete_is_idempotent_and_content_addressed(client, recon):
    sid = create_site(client)["site_id"]
    cap, _ = build_twin(client, sid)
    again = client.post(f"/captures/{cap['capture_id']}/complete").json()
    assert again["cached"] and again["twin_version"] == 1
    # a second capture with byte-identical content reuses the twin build
    cap2 = upload_capture(client, sid)
    r = client.post(f"/captures/{cap2['capture_id']}/complete").json()
    assert r["status"] == "done" and r["cached"] and r["twin_version"] == 1
    assert len(recon) == 1
    # different content -> new build, new version
    build_twin(client, sid, seed=7)
    assert len(recon) == 2
    assert client.get(f"/sites/{sid}/twin").json()["version"] == 2


def test_split_chunks_files_mode_and_upload_refresh(client, recon):
    from api_testkit import capture_files, zip_bytes

    sid = create_site(client)["site_id"]
    blob = zip_bytes(capture_files())
    third = len(blob) // 3 + 1
    r = client.post(f"/sites/{sid}/captures", json={"device": {"model": "iPhone", "tier": "T1_LIDAR"},
                                                    "n_chunks": 3, "chunking": "split", "files": ["notes.txt"]})
    cap = r.json()
    cid = cap["capture_id"]
    incomplete = client.post(f"/captures/{cid}/complete")
    assert incomplete.status_code == 409 and len(incomplete.json()["detail"]["missing"]) == 4
    refreshed = client.post(f"/captures/{cid}/upload-urls", json={"parts": [2], "files": []}).json()
    assert [c["part"] for c in refreshed["upload"]["chunks"]] == [2]
    for i, info in enumerate(cap["upload"]["chunks"]):
        assert client.put(info["url"], content=blob[i * third:(i + 1) * third]).status_code == 200
    assert client.put(cap["upload"]["files"][0]["url"], content=b"scan notes").status_code == 200
    r = client.post(f"/captures/{cid}/complete")
    assert r.status_code == 202, r.text
    st = wait_for(lambda: (lambda s: s if s["status"] == "done" else None)(client.get(f"/captures/{cid}").json()))
    assert st["twin_version"] == 1 and recon[0]["tier"] == "T1_LIDAR"
    # uploads close once processing starts
    assert client.put(cap["upload"]["files"][0]["url"], content=b"late").status_code == 409


def test_observer_capture_is_stored(client, recon):
    sid = create_site(client)["site_id"]
    r = client.post(f"/sites/{sid}/captures", json={"device": {"model": "Narzo", "tier": "T3_FIXED_OBSERVER"},
                                                    "role": "observer", "n_chunks": 0, "files": ["observer.webm"]})
    cap = r.json()
    client.put(cap["upload"]["files"][0]["url"], content=b"\x1aE\xdf\xa3webm")
    out = client.post(f"/captures/{cap['capture_id']}/complete").json()
    assert out["status"] == "stored" and recon == []


def test_failed_reconstruction_reports_error_and_retries(client, recon, monkeypatch):
    import sys

    mod = sys.modules["jobs.recon.pipeline"]
    ok = mod.build_twin_from_capture

    def failing(*a, **k):
        raise ValueError("too many tracking-limited frames")

    monkeypatch.setattr(mod, "build_twin_from_capture", failing)
    sid = create_site(client)["site_id"]
    cap = upload_capture(client, sid)
    cid = cap["capture_id"]
    client.post(f"/captures/{cid}/complete")
    st = wait_for(lambda: (lambda s: s if s["status"] == "failed" else None)(client.get(f"/captures/{cid}").json()))
    assert "tracking-limited" in st["error"]
    assert client.get(f"/sites/{sid}/twin").status_code == 404
    monkeypatch.setattr(mod, "build_twin_from_capture", ok)
    client.post(f"/captures/{cid}/complete")
    st = wait_for(lambda: (lambda s: s if s["status"] == "done" else None)(client.get(f"/captures/{cid}").json()))
    assert st["twin_version"] == 1


def test_analysis_failure_is_reported(client, recon, orchestrator):
    sid = create_site(client)["site_id"]
    build_twin(client, sid)
    orchestrator.fail_with = "sim shard stopped"
    aid = client.post(f"/sites/{sid}/analyses", json={}).json()["analysis_id"]
    st = wait_analysis(client, aid)
    assert st["status"] == "failed" and "sim shard stopped" in st["error"]
    assert client.get(f"/analyses/{aid}/report").status_code == 404
    # a failed request is not reused
    orchestrator.fail_with = None
    r = client.post(f"/sites/{sid}/analyses", json={})
    assert r.status_code == 202 and r.json()["analysis_id"] != aid


def test_analysis_validation(client, recon, orchestrator):
    sid = create_site(client)["site_id"]
    assert client.post(f"/sites/{sid}/analyses", json={}).status_code == 404  # no twin yet
    build_twin(client, sid)
    bad_wp = client.post(f"/sites/{sid}/analyses", json={"task": {"sequence": ["DOCK", "NOWHERE"]}})
    assert bad_wp.status_code == 422 and "NOWHERE" in bad_wp.text
    awaiting = client.post(f"/sites/{sid}/analyses", json={"robot_ids": ["acme_cart_c2"]})
    assert awaiting.status_code == 422 and "awaiting review" in awaiting.text
    assert client.post(f"/sites/{sid}/analyses", json={"robot_ids": ["nope"]}).status_code == 422
    assert client.get("/analyses/an_missing").status_code == 404


def test_twin_put_validation(client, recon):
    sid = create_site(client)["site_id"]
    build_twin(client, sid)
    twin = client.get(f"/sites/{sid}/twin").json()
    bad = json.loads(json.dumps(twin))
    bad["occupancy"]["uri"] = "../../etc/passwd"
    assert client.put(f"/sites/{sid}/twin", json=bad).status_code == 422
    other = create_site(client, "Other")["site_id"]
    bad["occupancy"]["uri"] = f"sites/{other}/twin_v1/occ.png"
    assert client.put(f"/sites/{sid}/twin", json=bad).status_code == 422
    dup = json.loads(json.dumps(twin))
    dup["waypoints"].append(dict(dup["waypoints"][0]))
    assert "duplicate waypoint" in client.put(f"/sites/{sid}/twin", json=dup).text
    assert client.put(f"/sites/{sid}/twin", json={"site_id": sid}).status_code == 422
    forced = dict(twin, version=99)
    assert client.put(f"/sites/{sid}/twin?force=true", json=forced).json()["version"] == 2


def test_sample_sites_import_read_only(env, make_client, recon, orchestrator):
    d = env["samples"] / "reference_cafe"
    (d / "twin_v1").mkdir(parents=True)
    (d / "analyses" / "an_sample01" / "batches").mkdir(parents=True)
    (d / "site.json").write_text(json.dumps({"site_id": "ref_cafe", "name": "Reference cafe", "type": "cafe",
                                             "geometry_source": "reference"}))
    occ = np.full((40, 60), 255, np.uint8)
    cv2.imwrite(str(d / "twin_v1" / "occ.png"), occ)
    (d / "twin_v1" / "twin.json").write_text(json.dumps({
        "twin_id": "twin_ref_cafe_1", "site_id": "ref_cafe", "version": 1, "geometry_source": "reference",
        "occupancy": {"uri": "sites/ref_cafe/twin_v1/occ.png", "resolution_m": 0.05, "width": 60, "height": 40},
        "waypoints": [{"id": "DOCK", "xy": [0.3, 0.3], "kind": "dock"},
                      {"id": "PICKUP", "xy": [1, 1], "kind": "pickup"},
                      {"id": "T1", "xy": [2, 1], "kind": "dest"}]}))
    a = d / "analyses" / "an_sample01"
    (a / "analysis.json").write_text(json.dumps({"status": "done", "inputs": {"twin_version": 1},
                                                 "verdicts": [{"robot_id": "acme_tray_t1", "label": "Cleared"}]}))
    (a / "report.md").write_text("Cleared [f_1].")
    (a / "facts.json").write_text(json.dumps([{"fact_id": "f_1", "value": 0.95, "label": "success",
                                               "origin": {"type": "sim_batch", "ref": "sb_ref1"}}]))
    (a / "batches" / "sb_ref1.json").write_text(json.dumps({"batch_id": "sb_ref1"}))
    (a / "batches" / "sb_ref1.episodes.jsonl").write_text(
        "\n".join(json.dumps({"t": t, "robot": [t, 0, 0, 0.5], "peds": [], "events": []})
                  for t in (0.0, 0.1, 0.2, 0.0, 0.1)) + "\n")
    before = sorted((p.as_posix(), p.stat().st_mtime) for p in d.rglob("*") if p.is_file())

    c = make_client()
    sites = {s["site_id"]: s for s in c.get("/sites").json()}
    ref = sites["ref_cafe"]
    assert ref["geometry_source"] == "reference" and ref["sample"] and ref["readonly"]
    assert c.get("/sites/ref_cafe/twin").json()["occupancy"]["uri"] == "sites/ref_cafe/twin_v1/occ.png"
    assert c.get("/sites/ref_cafe/twin/occupancy.png").status_code == 200
    assert c.get("/analyses/an_sample01").json()["verdicts"][0]["label"] == "Cleared"
    assert c.get("/analyses/an_sample01/report").json()["chips"][0]["link"] == "/sim/batches/sb_ref1/episodes/0"
    ep1 = c.get("/sim/batches/sb_ref1/episodes/1").json()
    assert ep1["n_episodes"] == 2 and ep1["n_frames"] == 2

    # captures are refused, but editing creates a new version in storage
    r = c.post("/sites/ref_cafe/captures", json={"device": {"model": "x", "tier": "T2_VIO_MONO"}})
    assert r.status_code == 403
    twin = c.get("/sites/ref_cafe/twin").json()
    twin["waypoints"][2]["xy"] = [2.5, 1.0]
    assert c.put("/sites/ref_cafe/twin", json=twin).json()["version"] == 2
    after = sorted((p.as_posix(), p.stat().st_mtime) for p in d.rglob("*") if p.is_file())
    assert before == after

    # restart: re-import keeps the user's v2
    c.__exit__(None, None, None)
    c2 = make_client()
    assert [t["version"] for t in c2.get("/sites/ref_cafe/twins").json()] == [1, 2]
    aid = c2.post("/sites/ref_cafe/analyses", json={}).json()["analysis_id"]
    assert wait_analysis(c2, aid)["status"] == "done"
