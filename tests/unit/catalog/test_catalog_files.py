"""Every file in catalog/robots/ is a valid RobotSpec whose non-null fields all carry a verbatim source."""
import json
from pathlib import Path

import pytest

from clearance_core.schemas import RobotSpec

from agents.catalog_builder import catalog
from agents.catalog_builder.build import revalidate
from agents.catalog_builder.tavily_client import RECORDED_DIR, record_key

ROBOTS_DIR = Path(__file__).resolve().parents[3] / "catalog" / "robots"
FILES = sorted(ROBOTS_DIR.glob("*.json"))


def _recorded_text(url: str) -> str | None:
    p = RECORDED_DIR / "extract" / f"{record_key(url)}.json"
    return json.loads(p.read_text(encoding="utf-8"))["raw_content"] if p.exists() else None


def test_catalog_has_8_to_16_robots_of_both_classes():
    specs = [RobotSpec.model_validate_json(p.read_text(encoding="utf-8")) for p in FILES]
    assert 8 <= len(specs) <= 16
    classes = {s.robot_class for s in specs}
    assert classes == {"A_service", "B_amr"}


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_file_parses_and_is_sourced(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    spec = RobotSpec.model_validate(data)
    assert spec.robot_id == path.stem
    assert "class" in data
    assert catalog.unsourced_fields(spec) == []
    for name, src in spec.fields_sources.items():
        assert src.url.startswith("https://"), name
        assert src.quote.strip(), name
        assert src.retrieved_at.endswith("Z"), name
    if spec.footprint_polygon_m is not None:
        assert len(spec.footprint_polygon_m) >= 3
        assert spec.footprint_approx is True
    if spec.price.value is None:
        assert spec.price.is_public is False and spec.price.note == "price on request"
    else:
        assert spec.price.is_public and spec.price.source_url


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_notes_and_review_state(path):
    spec = RobotSpec.model_validate_json(path.read_text(encoding="utf-8"))
    notes = catalog.read_notes(spec)
    if not spec.verified_by_human:
        assert notes.get("review_status") == "awaiting review"
    assert notes.get("agent_quote_validated") is True


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_quotes_revalidate_against_recorded_page_text(path):
    spec = RobotSpec.model_validate_json(path.read_text(encoding="utf-8"))
    texts = {s.url: t for s in spec.fields_sources.values() if (t := _recorded_text(s.url)) is not None}
    assert texts, "recorded page text missing for this robot"
    res = revalidate(spec, texts)
    failing = {f: why for f, (ok, why) in res.items() if not ok}
    assert not failing


def test_no_images_in_catalog():
    exts = {p.suffix.lower() for p in (ROBOTS_DIR.parent).rglob("*") if p.is_file()}
    assert not exts & {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
