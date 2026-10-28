import math

import pytest

from agents.catalog_builder import units
from agents.catalog_builder.build import validate_field as contract_validate_field
from agents.catalog_builder.validate import check_field, normalize_text, validate_field

PAGE = """Product Overview
| Robot dimensions
| 565 mm×537 mm×1290 mm
| Cruise speed
| 0.5m/s - 1.2m/s(adjustable）
| Max. climbing angle
| 5 °
Payload
22 lb (10kg) per tray, total 66 lb (30kg)
Battery life
10-12 hrs
Min. Aisle Width 1,099 mm (43 in)
Operational corridor width: 1 450 mm | 57 in
Maximum incline/decline ± 5% at 0.5 m/s
60 min charging = 10 h and 20 min runtime
glides across ADA ramps, and thresholds up to 1/2”.
Max. Speed 2.0 m/sec (4.5 mph)
Max. Turning Speed 1.25 rad/sec (75º/sec)
**Maximum payload** 250 kg | 551 lbs
"""


# ---------------- unit conversion (code, not the LLM)
@pytest.mark.parametrize("value,unit,field,expected", [
    (565, "mm", "length_m", 0.565),
    (70, "cm", "min_aisle_width_vendor_m", 0.70),
    (25.6, "in", "min_aisle_width_vendor_m", 0.65024),
    (2, "ft", "height_m", 0.6096),
    (0.5, '"', "max_threshold_m", 0.0127),
    (7.2, "km/h", "max_speed_mps", 2.0),
    (4.5, "mph", "max_speed_mps", 2.01168),
    (3.28, "ft/s", "max_speed_mps", 0.999744),
    (2.0, "m/sec", "max_speed_mps", 2.0),
    (66, "lb", "payload_kg", 29.93709642),
    (88, "lbs", "payload_kg", 39.91614856),
    (500, "g", "payload_kg", 0.5),
    (90, "min", "runtime_h", 1.5),
    (12, "hrs", "runtime_h", 12.0),
    (5, "°", "max_slope_deg", 5.0),
    (5, "%", "max_slope_deg", math.degrees(math.atan(0.05))),
])
def test_unit_conversion(value, unit, field, expected):
    assert units.to_si(value, unit, field) == pytest.approx(expected, rel=1e-6)


@pytest.mark.parametrize("value,unit,field", [
    (40, "kg", "max_speed_mps"),     # wrong dimension
    (1.2, "m/s", "payload_kg"),
    (5, "%", "payload_kg"),          # grade only converts to an angle
    (3, "furlong", "height_m"),      # unknown unit
])
def test_unit_conversion_rejects_mismatch(value, unit, field):
    with pytest.raises(units.UnitError):
        units.to_si(value, unit, field)


def test_number_parsing_variants():
    assert units.parse_number("1,099") == 1099
    assert units.parse_number("1 450") == 1450
    assert units.parse_number("1/2") == 0.5
    assert units.parse_number("1,2") == 1.2
    assert 450.0 in units.number_variants("1 450")


def test_parse_dims_and_ranges():
    qs = units.parse_quantities(normalize_text("56.5*53.7*129 cm(22.24*21.14*50.79 inches)"))
    dims = [q for q in qs if q.kind == "dims"]
    assert dims[0].si == pytest.approx([0.565, 0.537, 1.29])
    qs = units.parse_quantities(normalize_text("0.5 m/s~1.2 m/s"))
    assert any(q.kind == "range" and q.si == pytest.approx([0.5, 1.2]) for q in qs)
    qs = units.parse_quantities("10 h and 20 min")
    assert any(q.kind == "compound" and q.si[0] == pytest.approx(10 + 1 / 3) for q in qs)


# ---------------- normalization
def test_normalization_whitespace_unicode_markdown():
    assert normalize_text("Max.  Speed\n|  2.0 m/s") == "Max. Speed 2.0 m/s"
    assert normalize_text("0.5–1.2 m/s") == "0.5-1.2 m/s"
    assert normalize_text("565 mm×537 mm") == "565 mm x 537 mm"
    assert normalize_text("（adjustable）") == "(adjustable)"
    assert normalize_text("**Payload** 40 kg") == "Payload 40 kg"
    assert normalize_text("[Spec sheet](https://x.y/z.pdf) 2 m/s") == "Spec sheet 2 m/s"
    assert normalize_text("75º/sec") == "75°/sec"
    assert normalize_text("1/2”") == '1/2"'
    assert normalize_text("so­ft hy​phen") == "soft hyphen"


def test_quote_matches_across_table_cells_and_newlines():
    assert validate_field(1.2, "Cruise speed 0.5m/s - 1.2m/s(adjustable)", PAGE, field="max_speed_mps")
    assert validate_field(5.0, "Max. climbing angle | 5 °", PAGE, field="max_slope_deg")
    assert validate_field(250.0, "Maximum payload 250 kg", PAGE, field="payload_kg")


# ---------------- acceptance with conversions
@pytest.mark.parametrize("value,quote,field", [
    (0.565, "565 mm×537 mm×1290 mm", "length_m"),
    (1.29, "565 mm×537 mm×1290 mm", "height_m"),
    (1.2, "Cruise speed 0.5m/s - 1.2m/s", "max_speed_mps"),
    (30.0, "total 66 lb (30kg)", "payload_kg"),
    (29.9371, "total 66 lb (30kg)", "payload_kg"),
    (10.0, "Battery life 10-12 hrs", "runtime_h"),
    (1.099, "Min. Aisle Width 1,099 mm (43 in)", "min_aisle_width_vendor_m"),
    (1.45, "Operational corridor width: 1 450 mm", "min_aisle_width_vendor_m"),
    (2.86241, "Maximum incline/decline ± 5% at 0.5 m/s", "max_slope_deg"),
    (10.3333, "10 h and 20 min runtime", "runtime_h"),
    (0.0127, "thresholds up to 1/2”", "max_threshold_m"),
    (2.0, "Max. Speed 2.0 m/sec (4.5 mph)", "max_speed_mps"),
])
def test_accepts_converted_values(value, quote, field):
    ok, why = check_field(value, quote, PAGE, field=field)
    assert ok, why


# ---------------- rejections
def test_quote_not_in_page_rejected():
    ok, why = check_field(1.5, "Max speed 1.5 m/s", PAGE, field="max_speed_mps")
    assert not ok and "not found" in why


def test_paraphrased_quote_rejected():
    assert not validate_field(1.2, "cruise speed up to 1.2 m/s", PAGE, field="max_speed_mps")
    assert not validate_field(1.2, "Cruise Speed 0.5m/s - 1.2m/s", PAGE, field="max_speed_mps")  # case differs


def test_number_not_in_quote_rejected():
    ok, why = check_field(1.5, "Cruise speed 0.5m/s - 1.2m/s", PAGE, field="max_speed_mps")
    assert not ok and "quantity" in why
    assert not validate_field(1.5, "Cruise speed", PAGE)


def test_number_with_wrong_unit_rejected():
    # 66 is in the quote, but as lb: as kg it must be rejected
    assert not validate_field(66.0, "total 66 lb (30kg)", PAGE, field="payload_kg")
    # the 0.5 m/s in the slope quote is a speed, not an angle
    assert not validate_field(0.5, "Maximum incline/decline ± 5% at 0.5 m/s", PAGE, field="max_slope_deg")
    # rad/sec turning speed is not a linear speed
    assert not validate_field(1.25, "Max. Turning Speed 1.25 rad/sec", PAGE, field="max_speed_mps")


def test_lower_end_of_range_is_not_a_max_speed():
    ok, why = check_field(0.5, "Cruise speed 0.5m/s - 1.2m/s", PAGE, field="max_speed_mps")
    assert not ok and "lower end" in why


def test_unconverted_value_rejected():
    assert not validate_field(565, "565 mm×537 mm×1290 mm", PAGE, field="length_m")


@pytest.mark.parametrize("value,quote", [
    (None, "Max. Speed 2.0 m/sec"),
    (2.0, ""),
    (2.0, None),
    (2.0, "2."),
    (float("nan"), "Max. Speed 2.0 m/sec"),
    (float("inf"), "Max. Speed 2.0 m/sec"),
    ("abc", "Max. Speed 2.0 m/sec"),
    (2.0, "Max. Speed 2.0 m/sec " * 40),
])
def test_degenerate_inputs_rejected(value, quote):
    assert not validate_field(value, quote, PAGE, field="max_speed_mps")


def test_empty_page_rejects_everything():
    assert not validate_field(2.0, "Max. Speed 2.0 m/sec", "", field="max_speed_mps")
    assert not validate_field(2.0, "Max. Speed 2.0 m/sec", None)


def test_field_free_mode_and_non_numeric_values():
    assert validate_field(2.0, "Max. Speed 2.0 m/sec", PAGE)
    assert validate_field(2.0, "Max. Speed 2.0 m/sec", PAGE, field="unknown_field")
    assert validate_field(True, "Max. Turning Speed", PAGE)
    assert validate_field("rad/sec", "Max. Turning Speed 1.25 rad/sec", PAGE)
    assert not validate_field("knots", "Max. Turning Speed 1.25 rad/sec", PAGE)
    assert validate_field([0.565, 0.537], "565 mm×537 mm×1290 mm", PAGE, field="length_m")
    assert not validate_field([0.565, 0.6], "565 mm×537 mm×1290 mm", PAGE, field="length_m")


def test_contract_entry_point_is_reexported():
    assert contract_validate_field is validate_field
    assert contract_validate_field(2.0, "Max. Speed 2.0 m/sec", PAGE)


def test_large_page_and_many_quotes():
    big = ("filler text with numbers 1 2 3 4 5 mm kg m/s\n" * 20000) + PAGE
    assert validate_field(2.0, "Max. Speed 2.0 m/sec (4.5 mph)", big, field="max_speed_mps")
    for i in range(200):
        assert not validate_field(2.0 + i * 0.01 + 0.005, "Max. Speed 2.0 m/sec (4.5 mph)", PAGE, field="max_speed_mps")
