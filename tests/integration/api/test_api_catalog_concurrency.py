"""Catalog review flow and concurrent Clearance checks / twin saves."""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor

from api_testkit import build_twin, create_site, wait_analysis


def test_catalog_lists_reviewed_and_all(client):
    reviewed = client.get("/catalog/robots").json()
    assert [r["robot_id"] for r in reviewed] == ["acme_tray_t1"]
    assert reviewed[0]["class"] == "A_service"
    everything = {r["robot_id"]: r for r in client.get("/catalog/robots?all=1").json()}
    assert set(everything) == {"acme_tray_t1", "acme_cart_c2"}
    assert everything["acme_cart_c2"]["verified_by_human"] is False
    assert client.get("/catalog/robots/nope").status_code == 404


def test_catalog_verify_records_who_and_when(client, env):
    r = client.post("/catalog/robots/acme_cart_c2/verify",
                    json={"verified_by": "Priya (ops)", "edits": {"max_speed_mps": 1.0}, "notes": "datasheet p2"})
    assert r.status_code == 200, r.text
    spec = r.json()
    assert spec["verified_by_human"] is True and spec["max_speed_mps"] == 1.0
    assert spec["review"]["verified_by"] == "Priya (ops)" and spec["review"]["verified_at"].endswith("Z")
    assert spec["review"]["edited_fields"] == ["max_speed_mps"]
    assert {x["robot_id"] for x in client.get("/catalog/robots").json()} == {"acme_tray_t1", "acme_cart_c2"}
    on_disk = json.loads((env["catalog"] / "acme_cart_c2.json").read_text())
    assert on_disk["verified_by_human"] is True and on_disk["review"]["verified_by"] == "Priya (ops)"
    # revoke
    r = client.post("/catalog/robots/acme_cart_c2/verify", json={"verified_by": "Priya (ops)", "verified": False})
    assert r.json()["verified_by_human"] is False
    assert [x["robot_id"] for x in client.get("/catalog/robots").json()] == ["acme_tray_t1"]


def test_catalog_verify_validation(client):
    assert client.post("/catalog/robots/acme_cart_c2/verify", json={"verified_by": ""}).status_code == 422
    r = client.post("/catalog/robots/acme_cart_c2/verify", json={"verified_by": "a", "edits": {"robot_id": "x"}})
    assert r.status_code == 422
    r = client.post("/catalog/robots/acme_cart_c2/verify", json={"verified_by": "a", "edits": {"wheels": 4}})
    assert r.status_code == 422 and "unknown" in r.text
    r = client.post("/catalog/robots/acme_cart_c2/verify", json={"verified_by": "a", "edits": {"drive": "legs"}})
    assert r.status_code == 422
    assert client.post("/catalog/robots/nope/verify", json={"verified_by": "a"}).status_code == 404


def test_concurrent_analyses_run_in_parallel(client, recon, orchestrator):
    sid = create_site(client)["site_id"]
    build_twin(client, sid)
    n = 4
    orchestrator.barrier = threading.Barrier(n)  # every run must be in flight at once to pass
    with ThreadPoolExecutor(n) as ex:
        resps = list(ex.map(lambda i: client.post(f"/sites/{sid}/analyses",
                                                  json={"business": {"deliveries_per_day": 100 + i}}), range(n)))
    assert all(r.status_code == 202 for r in resps), [r.text for r in resps]
    ids = [r.json()["analysis_id"] for r in resps]
    assert len(set(ids)) == n
    results = [wait_analysis(client, a, timeout=20) for a in ids]
    assert all(r["status"] == "done" for r in results), results
    batches = {r["batches"][0] for r in results}
    assert len(batches) == n
    for a in ids:
        assert client.get(f"/analyses/{a}/report").status_code == 200
    assert len(client.get(f"/sites/{sid}/analyses").json()) == n
    seen = sorted(c["business"]["deliveries_per_day"] for c in orchestrator.calls)
    assert seen == [100, 101, 102, 103]


def test_progress_visible_while_running(client, recon, orchestrator):
    sid = create_site(client)["site_id"]
    build_twin(client, sid)
    orchestrator.delay_s = 0.6
    aid = client.post(f"/sites/{sid}/analyses", json={}).json()["analysis_id"]
    from api_testkit import wait_for

    running = wait_for(lambda: (lambda s: s if s["status"] == "running" else None)(client.get(f"/analyses/{aid}").json()))
    assert 0.0 < running["progress"] < 1.0 and running["stages"]["simulation"]["state"] == "running"
    assert client.get(f"/analyses/{aid}/report").status_code == 404
    assert wait_analysis(client, aid)["status"] == "done"


def test_concurrent_twin_saves_get_distinct_versions(client, recon):
    sid = create_site(client)["site_id"]
    build_twin(client, sid)
    twin = client.get(f"/sites/{sid}/twin").json()

    def save(i):
        t = json.loads(json.dumps(twin))
        t["primitives"][0]["pose"]["xyz"] = [3.0 + 0.01 * i, 2.0, 0.375]
        return client.put(f"/sites/{sid}/twin?force=true", json=t)

    with ThreadPoolExecutor(8) as ex:
        resps = list(ex.map(save, range(8)))
    versions = sorted(r.json()["version"] for r in resps)
    assert versions == list(range(2, 10))
    assert [t["version"] for t in client.get(f"/sites/{sid}/twins").json()] == list(range(1, 10))
