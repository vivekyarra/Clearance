"""Catalog files: catalog/robots/<robot_id>.json (RobotSpec, by_alias JSON). Notes are a JSON object
serialized into RobotSpec.notes so the review UI and tools can read them."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from clearance_core.schemas import RobotSpec

REPO = Path(__file__).resolve().parents[2]


def catalog_dir() -> Path:
    return Path(os.environ.get("CLEARANCE_CATALOG_DIR", REPO / "catalog" / "robots"))


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_CORP_WORDS = {"robotics", "robotic", "robots", "robot", "motors", "inc", "ltd", "co", "corp",
               "technology", "technologies", "tech", "gmbh", "ag", "as", "llc"}


def robot_id_for(vendor: str, model: str) -> str:
    """'Pudu Robotics', 'BellaBot' -> 'pudu_bellabot'; 'OTTO Motors', 'OTTO 100' -> 'otto_100'."""
    words = [w for w in re.split(r"[^a-z0-9]+", vendor.lower()) if w]
    words = [w for w in words if w not in _CORP_WORDS] or words
    v = "_".join(words)
    m = re.sub(r"[^a-z0-9]+", "_", model.lower()).strip("_")
    if m.startswith(v + "_"):
        m = m[len(v) + 1:]
    return f"{v}_{m}"


def spec_path(robot_id: str) -> Path:
    if not re.fullmatch(r"[a-z0-9_]+", robot_id):
        raise ValueError(f"invalid robot_id {robot_id!r}")
    return catalog_dir() / f"{robot_id}.json"


def save_spec(spec: RobotSpec, path: Path | None = None) -> Path:
    p = Path(path) if path else spec_path(spec.robot_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(spec.model_dump(mode="json", by_alias=True), indent=2, ensure_ascii=False) + "\n",
                 encoding="utf-8")
    return p


def load_spec(robot_id_or_path: str | Path) -> RobotSpec:
    p = Path(robot_id_or_path)
    if not p.suffix:
        p = spec_path(str(robot_id_or_path))
    return RobotSpec.model_validate_json(p.read_text(encoding="utf-8"))


def load_catalog(*, verified_only: bool = True) -> list[RobotSpec]:
    """All catalog robots; by default only human-reviewed ones (only verified robots simulate)."""
    out = []
    for p in sorted(catalog_dir().glob("*.json")):
        s = load_spec(p)
        if s.verified_by_human or not verified_only:
            out.append(s)
    return out


def read_notes(spec: RobotSpec) -> dict:
    if not spec.notes:
        return {}
    try:
        obj = json.loads(spec.notes)
        return obj if isinstance(obj, dict) else {"text": spec.notes}
    except ValueError:
        return {"text": spec.notes}


def write_notes(spec: RobotSpec, notes: dict) -> RobotSpec:
    spec.notes = json.dumps(notes, ensure_ascii=False, sort_keys=False)
    return spec


NUMERIC_FIELDS = ["height_m", "max_speed_mps", "nominal_speed_mps", "max_threshold_m", "max_slope_deg",
                  "min_aisle_width_vendor_m", "payload_kg", "runtime_h", "safety_stop_distance_m"]


def unsourced_fields(spec: RobotSpec) -> list[str]:
    """Non-null fields that lack a fields_sources entry (must be empty for every catalog file)."""
    missing = [f for f in NUMERIC_FIELDS if getattr(spec, f) is not None and f not in spec.fields_sources]
    if spec.footprint_polygon_m is not None and "footprint_polygon_m" not in spec.fields_sources:
        missing.append("footprint_polygon_m")
    if spec.min_turn_radius_m not in (0, 0.0) and "min_turn_radius_m" not in spec.fields_sources:
        missing.append("min_turn_radius_m")
    if spec.price.value is not None and "price" not in spec.fields_sources:
        missing.append("price")
    return missing
