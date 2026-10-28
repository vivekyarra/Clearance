"""Malformed model output never crashes verification and never yields a value."""
import pytest

from agents.catalog_builder import units
from agents.catalog_builder.extract import verify_raw
from agents.catalog_builder.validate import check_footprint

PAGE = "Specifications\nRobot dimensions 435*450*1120 (mm)\nMax Speed 1.2 m/s\nPayload 30 kg\n"


@pytest.mark.parametrize("raw", [
    {"fields": {"max_speed": 1.2}},
    {"fields": {"max_speed": "1.2 m/s"}},
    {"fields": {"max_speed": ["1.2"]}},
    {"fields": ["max_speed"]},
    {"fields": None, "dimensions": "435*450*1120"},
    {"fields": {}, "dimensions": {"length": 435, "width": 450, "unit": None, "quote": "435*450*1120 (mm)"}},
    {"fields": {}, "dimensions": {"length": None, "width": 450, "unit": "mm", "quote": "435*450*1120 (mm)"}},
    {"fields": {"max_speed": {"value": 1.2, "unit": None, "quote": "Max Speed 1.2 m/s"}}},
    {"fields": {}, "in_place_rotation": "yes", "price": "contact us"},
    {"fields": {}, "price": {"value": "abc", "quote": None}},
])
def test_malformed_shapes_are_rejected_not_raised(raw):
    verified, rejected, price = verify_raw(raw, PAGE, "https://x.example/p", "2026-10-27T00:00:00Z")
    assert verified == {}
    assert price is None


def test_unit_in_brackets_after_dimensions():
    qs = [q for q in units.parse_quantities("435*450*1120 (mm)") if q.kind == "dims"]
    assert qs and qs[0].values == [435.0, 450.0, 1120.0] and qs[0].unit == "mm"
    assert check_footprint(0.435, 0.45, "435*450*1120 (mm)", PAGE) == (True, "ok")
    # a bracketed unit does not create numbers that are not on the page
    assert check_footprint(0.435, 0.46, "435*450*1120 (mm)", PAGE)[0] is False


def test_bracket_rewrite_keeps_positions():
    text = "Weight 38 (kg) and size 435*450 (mm)"
    qs = units.parse_quantities(text)
    for q in qs:
        assert text[q.start].isdigit()
