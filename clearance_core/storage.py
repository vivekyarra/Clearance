"""Storage root. Locally everything lives under CLEARANCE_DATA (default <repo>/data); in Nebius the same
relative keys map to an S3 bucket (services/api switches on CLEARANCE_S3_BUCKET). Keys are POSIX-style."""
from __future__ import annotations

import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("CLEARANCE_DATA", REPO / "data"))


def path(key: str) -> Path:
    p = DATA / key
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def write_json(key: str, obj) -> str:
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump(mode="json", by_alias=True)
    path(key).write_text(json.dumps(obj, indent=2), encoding="utf-8")
    return key


def read_json(key: str):
    return json.loads((DATA / key).read_text(encoding="utf-8"))
