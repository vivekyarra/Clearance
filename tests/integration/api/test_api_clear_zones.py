"""Twin PUT validates operator-confirmed clear zones and stamps who confirmed them and when."""
from __future__ import annotations

import pytest
from api_testkit import build_twin, create_site


@pytest.fixture
def twin_site(client, recon):
    sid = create_site(client)["site_id"]
    build_twin(client, sid)
    return sid


def _box(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _put(client, sid, zones):
    twin = client.get(f"/sites/{sid}/twin").json()
    twin["clear_zones"] = zones
    return client.put(f"/sites/{sid}/twin", json=twin)


def test_clear_zone_saved_and_stamped(client, twin_site):
    r = _put(client, twin_site, [{"id": "C1", "polygon": _box(1.0, 1.0, 2.0, 2.0)}])
    assert r.status_code == 201, r.text
    z = r.json()["clear_zones"][0]
    assert z["reason"] == "floor confirmed clear by operator"
    assert z["confirmed_by"] == "operator" and z["confirmed_at"]
    # a later save keeps the original confirmation
    again = client.put(f"/sites/{twin_site}/twin", json=r.json())
    assert again.status_code == 201 and again.json()["clear_zones"][0]["confirmed_at"] == z["confirmed_at"]
    kept = _put(client, twin_site, [{"id": "C1", "polygon": _box(1, 1, 2, 2), "confirmed_by": "Asha",
                                     "confirmed_at": "2026-10-27T10:00:00Z"}]).json()["clear_zones"][0]
    assert kept["confirmed_by"] == "Asha" and kept["confirmed_at"] == "2026-10-27T10:00:00Z"


@pytest.mark.parametrize("zones, msg", [
    ([{"id": "C1", "polygon": [[1, 1], [2, 1]]}], "at least 3 points"),
    ([{"id": "C1", "polygon": _box(1.0, 1.0, 3.5, 3.0)}], "exceeds 4"),          # 5 m2
    ([{"id": "C1", "polygon": _box(5.0, 3.0, 7.0, 3.5)}], "inside the occupancy grid"),  # grid is 6 x 4 m
    ([{"id": "C1", "polygon": _box(-1.0, 1.0, 0.5, 2.0)}], "inside the occupancy grid"),
    ([{"id": "C1", "polygon": [[1, 1], [3, 1], [1, 2], [1.5, 0.5]]}], "must not cross"),
    ([{"id": "C1", "polygon": [[1, 1], [2, 1], [3, 1]]}], "no area"),
    ([{"id": "C1", "polygon": _box(1, 1, 1.5, 1.5)}, {"id": "C1", "polygon": _box(2, 2, 2.5, 2.5)}], "duplicate"),
    ([{"id": f"C{i}", "polygon": _box(0.2 * i, 0.5, 0.2 * i + 0.1, 0.6)} for i in range(21)], "at most 20"),
])
def test_clear_zone_validation(client, twin_site, zones, msg):
    r = _put(client, twin_site, zones)
    assert r.status_code == 422 and msg in r.text, r.text


def test_twenty_small_zones_accepted(client, twin_site):
    zones = [{"id": f"C{i}", "polygon": _box(0.25 * i + 0.2, 0.5, 0.25 * i + 0.4, 0.7)} for i in range(20)]
    r = _put(client, twin_site, zones)
    assert r.status_code == 201, r.text
    assert len(r.json()["clear_zones"]) == 20
