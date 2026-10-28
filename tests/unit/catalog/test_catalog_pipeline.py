"""End-to-end catalog builder in offline mode (recorded Tavily responses + rule-based extraction),
the LLM path with a scripted test double, and the review helper."""
import json

import pytest

from clearance_core.schemas import RobotSpec

from agents.catalog_builder import build, catalog, review
from agents.catalog_builder.extract import extract_raw, extract_rule_based, parse_json_object, verify_raw
from agents.catalog_builder.tavily_client import TavilyClient, TavilyError, record_key


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARANCE_CATALOG_DIR", str(tmp_path / "robots"))
    monkeypatch.setenv("CLEARANCE_CATALOG_PAGES", str(tmp_path / "pages"))
    monkeypatch.setenv("CLEARANCE_TAVILY_MODE", "offline")
    monkeypatch.setenv("CLEARANCE_LLM_MODE", "offline")
    monkeypatch.delenv("CLEARANCE_TAVILY_RECORDED", raising=False)
    return tmp_path


def test_offline_build_bellabot_end_to_end():
    spec = build.build_spec("Pudu Robotics", "BellaBot", "A_service")
    assert isinstance(spec, RobotSpec)
    assert spec.robot_id == "pudu_bellabot"
    assert spec.verified_by_human is False
    assert spec.max_speed_mps == pytest.approx(1.2)
    assert spec.payload_kg == pytest.approx(40.0)
    assert spec.max_slope_deg == pytest.approx(5.0)
    assert spec.max_threshold_m == pytest.approx(0.010)
    assert spec.min_aisle_width_vendor_m == pytest.approx(0.70)
    assert spec.height_m == pytest.approx(1.29)
    assert spec.footprint_approx is True
    xs = [p[0] for p in spec.footprint_polygon_m]
    ys = [p[1] for p in spec.footprint_polygon_m]
    assert max(xs) - min(xs) == pytest.approx(0.565) and max(ys) - min(ys) == pytest.approx(0.537)
    assert spec.nominal_speed_mps is None and spec.safety_stop_distance_m is None
    assert spec.price.value is None and spec.price.note == "price on request"
    assert catalog.unsourced_fields(spec) == []
    notes = catalog.read_notes(spec)
    assert notes["agent_quote_validated"] is True
    assert notes["review_status"] == "awaiting review"
    # vendor product page outranks the documentation site; the 12 h vs 13 h runtime is kept as a conflict
    assert spec.fields_sources["max_speed_mps"].url.startswith("https://www.pudurobotics.com/")
    assert any(c["field"] == "runtime_h" for c in notes["conflicts"])


def test_offline_build_wdh_order_and_inch_threshold():
    spec = build.build_spec("Bear Robotics", "Servi Plus", "A_service")
    xs = [p[0] for p in spec.footprint_polygon_m]
    ys = [p[1] for p in spec.footprint_polygon_m]
    # page says W x D x H = 21 x 23 x 48 in: length (x) is the depth
    assert max(xs) - min(xs) > max(ys) - min(ys)
    assert spec.max_threshold_m == pytest.approx(0.0127)
    assert spec.price.note == "price on request" and spec.price.source_url
    assert spec.fields_sources["price"].quote


def test_offline_build_percent_grade_to_degrees():
    spec = build.build_spec("MiR", "MiR250", "B_amr")
    assert spec.max_slope_deg == pytest.approx(2.862405, rel=1e-5)
    assert spec.payload_kg == pytest.approx(250.0)
    assert spec.max_speed_mps == pytest.approx(2.0)


def test_unknown_robot_gives_all_null_spec():
    spec = build.build_spec("Nonexistent Vendor", "Model Z", "B_amr")
    for f in catalog.NUMERIC_FIELDS:
        assert getattr(spec, f) is None
    assert spec.footprint_polygon_m is None
    assert spec.fields_sources == {}
    assert catalog.read_notes(spec)["agent_quote_validated"] is False


def test_cli_writes_catalog_file_and_refuses_overwrite(tmp_path, capsys):
    rc = build.main(["--vendor", "Keenon Robotics", "--model", "DINERBOT T9", "--class", "A_service"])
    assert rc == 0
    p = tmp_path / "robots" / "keenon_dinerbot_t9.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["verified_by_human"] is False and data["class"] == "A_service"
    spec = RobotSpec.model_validate(data)
    assert spec.payload_kg == pytest.approx(40.0)
    assert build.main(["--vendor", "Keenon Robotics", "--model", "DINERBOT T9", "--class", "A_service"]) == 2
    assert build.main(["--vendor", "Keenon Robotics", "--model", "DINERBOT T9", "--class", "A_service", "--force"]) == 0


# ---------------- LLM path with a scripted test double
PAGE = """Product Parameters
Moving Speed
1.0m/s (3.28ft/s)
Total Load Capacity
20 kg (44 lbs)
Battery Life
Up to 15h
Dimensions(WxDxH)
38.4x46.8x111.1cm
Minimum Passage Width
55 cm (21.65")
Price
Contact sales for pricing
"""


class ScriptedNano:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def chat(self, role, messages, **kw):
        self.calls.append((role, messages, kw))
        return self.reply


def test_llm_path_converts_validates_and_nulls_bad_fields():
    reply = json.dumps({
        "fields": {
            "max_speed": {"value": 3.28, "unit": "ft/s", "quote": "Moving Speed 1.0m/s (3.28ft/s)"},
            "payload": {"value": 25, "unit": "kg", "quote": "Total Load Capacity 20 kg (44 lbs)"},  # number not in quote
            "runtime": {"value": 15, "unit": "h", "quote": "Battery life up to 15 hours"},          # quote not on page
            "min_aisle_width": {"value": 21.65, "unit": "in", "quote": 'Minimum Passage Width 55 cm (21.65")'},
            "max_slope": {"value": 5, "unit": "kg", "quote": "Moving Speed 1.0m/s"},                # unit/dimension wrong
            "nominal_speed": None,
        },
        "dimensions": {"length": 46.8, "width": 38.4, "height": 111.1, "unit": "cm",
                       "quote": "Dimensions(WxDxH) 38.4x46.8x111.1cm"},
        "price": {"value": None, "currency": "USD", "quote": None, "on_request_quote": "Contact sales"},
    })
    nano = ScriptedNano("```json\n" + reply + "\n```")
    raw = extract_raw(PAGE, "Keenon Robotics", "DINERBOT T8", "https://example.com/t8", llm=nano)
    assert nano.calls and nano.calls[0][0] == "nano"
    assert nano.calls[0][2]["response_format"] == {"type": "json_object"}
    verified, rejected, price = verify_raw(raw, PAGE, "https://example.com/t8", "2026-10-27T00:00:00Z")
    assert verified["max_speed_mps"].value == pytest.approx(0.999744)
    assert verified["min_aisle_width_vendor_m"].value == pytest.approx(0.54991)
    assert "footprint_polygon_m" in verified and verified["height_m"].value == pytest.approx(1.111)
    reasons = {r["field"]: r["reason"] for r in rejected}
    assert "payload_kg" in reasons and "runtime_h" in reasons and "max_slope_deg" in reasons
    assert "not found" in reasons["runtime_h"]
    assert reasons["max_slope_deg"].startswith("unit")
    assert price["is_public"] is False and price["quote"] == "Contact sales"

    spec = build.assemble_spec("Keenon Robotics", "DINERBOT T8", "A_service",
                               [{"url": "https://example.com/t8", "verified": verified, "rejected": rejected,
                                 "price": price, "extractor": raw["_extractor"]}],
                               page_texts={"https://example.com/t8": PAGE})
    assert spec.payload_kg is None and spec.runtime_h is None and spec.max_slope_deg is None
    assert catalog.unsourced_fields(spec) == []
    assert catalog.read_notes(spec)["agent_quote_validated"] is True


def test_llm_error_uses_rule_based_reader():
    class Failing:
        def chat(self, *a, **k):
            raise RuntimeError("endpoint unavailable")

    raw = extract_raw(PAGE, "Keenon Robotics", "DINERBOT T8", "u", llm=Failing())
    assert raw["_extractor"].startswith("rule_based (llm error")
    verified, _, _ = verify_raw(raw, PAGE, "u", "t")
    assert verified["max_speed_mps"].value == pytest.approx(1.0)


def test_llm_response_object_with_parsed_dict():
    class Resp:
        parsed = {"fields": {"max_speed": {"value": 1.0, "unit": "m/s", "quote": "Moving Speed 1.0m/s"}}}
        content = ""

    raw = extract_raw(PAGE, "v", "m", "u", llm=ScriptedNano(Resp()))
    assert raw["fields"]["max_speed"]["value"] == 1.0


def test_parse_json_object_variants():
    assert parse_json_object('{"a": 1}') == {"a": 1}
    assert parse_json_object('<think>x</think> here: {"a": {"b": 2}} done') == {"a": {"b": 2}}
    with pytest.raises(ValueError):
        parse_json_object("no json here")
    with pytest.raises(ValueError):
        parse_json_object("[1, 2]")


def test_rule_based_reader_ignores_zero_counters_and_other_items():
    page = "Payload capacity\n0 kg\nMaximum travel speed\n0 m/s\nTray load\n10kg/layer\nCharging time\n4.5 h\n"
    raw = extract_rule_based(page)
    verified, rejected, _ = verify_raw(raw, page, "u", "t")
    assert "payload_kg" not in verified and "max_speed_mps" not in verified and "runtime_h" not in verified
    assert any("sanity" in r["reason"] for r in rejected)


def test_public_price_is_kept_with_source():
    page = "Servi robot\nPurchase Price $11,990\n"
    raw = {"fields": {}, "price": {"value": 11990, "currency": "USD", "quote": "Purchase Price $11,990"}}
    _, _, price = verify_raw(raw, page, "https://shop.example/servi", "t")
    assert price["value"] == 11990 and price["is_public"] is True
    spec = build.assemble_spec("Bear Robotics", "Servi", "A_service",
                               [{"url": "https://shop.example/servi", "verified": {}, "rejected": [], "price": price}],
                               page_texts={"https://shop.example/servi": page})
    assert spec.price.is_public and spec.price.value == 11990
    assert spec.fields_sources["price"].quote == "Purchase Price $11,990"
    # a price that is not on the page is never used
    raw = {"fields": {}, "price": {"value": 9990, "currency": "USD", "quote": "Purchase Price $9,990"}}
    _, rejected, price = verify_raw(raw, page, "u", "t")
    assert price is None and rejected[0]["field"] == "price"


# ---------------- Tavily client
def test_tavily_offline_missing_records(tmp_path):
    t = TavilyClient(mode="offline", recorded_dir=tmp_path / "none")
    assert t.search("anything")["results"] == []
    out = t.extract(["https://a.example/x"])
    assert out["results"] == [] and out["failed_results"][0]["url"] == "https://a.example/x"


def test_tavily_live_requires_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with pytest.raises(TavilyError):
        TavilyClient(mode="live").search("x")


def test_tavily_live_request_shape_and_recording(monkeypatch, tmp_path):
    import httpx

    sent = []

    def recorded_post(url, json=None, headers=None, timeout=None):
        sent.append((url, json, headers))
        if url.endswith("/search"):
            body = {"query": json["query"], "results": [{"url": "https://v.example/p", "score": 0.9}],
                    "response_time": 0.1, "usage": {"credits": 1}}
        else:
            body = {"results": [{"url": u, "raw_content": "Max. Speed 2.0 m/s"} for u in json["urls"]],
                    "failed_results": [], "response_time": 0.2, "usage": {"credits": 1}}
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", recorded_post)
    t = TavilyClient(mode="live", api_key="tvly-test", recorded_dir=tmp_path / "rec", record=True)
    r = t.search("Vendor Model specifications", max_results=3)
    assert r["results"][0]["url"] == "https://v.example/p"
    e = t.extract([f"https://v.example/{i}" for i in range(25)])  # split into 20 + 5
    assert len(e["results"]) == 25 and len(sent) == 3
    assert sent[0][2]["Authorization"] == "Bearer tvly-test"
    assert sent[1][1]["extract_depth"] == "advanced" and len(sent[1][1]["urls"]) == 20
    # recorded responses replay offline
    off = TavilyClient(mode="offline", recorded_dir=tmp_path / "rec")
    assert off.search("Vendor Model specifications")["results"][0]["url"] == "https://v.example/p"
    assert off.extract(["https://v.example/3"])["results"][0]["raw_content"] == "Max. Speed 2.0 m/s"
    assert record_key("Vendor Model specifications") != record_key("Vendor Model datasheet pdf")


# ---------------- review helper
def test_review_approve_edit_and_clear(tmp_path, capsys):
    spec = build.build_spec("Pudu Robotics", "T300", "B_amr")
    catalog.save_spec(spec)
    assert catalog.load_catalog() == []  # unreviewed robots are not loaded for simulation
    assert review.main([spec.robot_id, "--approve", "--reviewer", "vivek"]) == 0
    s = catalog.load_spec(spec.robot_id)
    assert s.verified_by_human is True and catalog.read_notes(s)["reviewed_by"] == "vivek"
    assert [r.robot_id for r in catalog.load_catalog()] == [spec.robot_id]

    # an edit needs a source and resets the review flag
    assert review.main([spec.robot_id, "--set", "payload_kg=250"]) == 2
    assert review.main([spec.robot_id, "--set", "payload_kg=250", "--url", "https://x.example",
                        "--quote", "Max load 250 kg"]) == 0
    s = catalog.load_spec(spec.robot_id)
    assert s.payload_kg == 250 and s.verified_by_human is False
    assert s.fields_sources["payload_kg"].quote == "Max load 250 kg"

    assert review.main([spec.robot_id, "--clear", "max_threshold_m"]) == 0
    s = catalog.load_spec(spec.robot_id)
    assert s.max_threshold_m is None and "max_threshold_m" not in s.fields_sources
    assert review.main(["--list"]) == 0
    assert spec.robot_id in capsys.readouterr().out


def test_review_refuses_unsourced_fields():
    spec = RobotSpec(robot_id="acme_x1", robot_class="B_amr", vendor="Acme", model="X1", payload_kg=10.0)
    with pytest.raises(ValueError):
        review.approve(spec, "r")
    assert review.approve(spec, "r", allow_unsourced=True).verified_by_human


def test_robot_id_and_path_rules():
    assert catalog.robot_id_for("Pudu Robotics", "BellaBot") == "pudu_bellabot"
    assert catalog.robot_id_for("OTTO Motors", "OTTO 100") == "otto_100"
    assert catalog.robot_id_for("Bear Robotics", "Servi Plus") == "bear_servi_plus"
    assert catalog.robot_id_for("Robotics", "R1") == "robotics_r1"
    with pytest.raises(ValueError):
        catalog.spec_path("../etc/passwd")
