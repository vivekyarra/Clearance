"""Robot catalog: catalog/robots/*.json (RobotSpec) plus human review records.

Reviews are stored in the DB (source of truth: who/when/edits) and, unless CLEARANCE_CATALOG_WRITEBACK=0,
written back into the spec file (`verified_by_human`, edited values, and a `review` block).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from services.api.core import schemas
from services.api.db import Database, RobotReviewRow, now_iso

log = logging.getLogger("clearance.api.catalog")

EDITABLE_EXCLUDE = {"robot_id", "fields_sources", "verified_by_human"}


class CatalogError(ValueError):
    pass


class Catalog:
    def __init__(self, directory: Path, db: Database, *, writeback: bool = True):
        self.dir = Path(directory)
        self.db = db
        self.writeback = writeback
        self._lock = threading.Lock()

    def _files(self) -> dict[str, tuple[Path, dict]]:
        out: dict[str, tuple[Path, dict]] = {}
        if not self.dir.is_dir():
            return out
        for p in sorted(self.dir.glob("*.json")):
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                rid = raw.get("robot_id") if isinstance(raw, dict) else None
                if not rid:
                    raise CatalogError("missing robot_id")
            except (ValueError, OSError) as e:
                log.warning("skipping catalog file %s: %s", p.name, e)
                continue
            out[rid] = (p, raw)
        return out

    def _reviews(self) -> dict[str, RobotReviewRow]:
        with self.db.session() as s:
            return {r.robot_id: r for r in s.query(RobotReviewRow).all()}

    @staticmethod
    def _merge(raw: dict, review: Optional[RobotReviewRow]) -> dict:
        d = dict(raw)
        if review is not None:
            d.update(review.edits or {})
            d["verified_by_human"] = bool(review.verified)
            d["review"] = {"verified": review.verified, "verified_by": review.verified_by,
                           "verified_at": review.verified_at, "edited_fields": sorted((review.edits or {}).keys()),
                           "notes": review.notes}
        return d

    def _entry(self, raw: dict, review) -> Optional[dict]:
        d = self._merge(raw, review)
        try:
            spec = schemas.RobotSpec.model_validate(d)
        except ValidationError as e:
            log.warning("catalog entry %s fails RobotSpec validation: %s", raw.get("robot_id"), e.errors()[:3])
            return None
        out = spec.model_dump(mode="json", by_alias=True)
        if "review" in d:
            out["review"] = d["review"]
        return out

    def list(self, include_all: bool = False) -> list[dict]:
        reviews = self._reviews()
        items = []
        for rid, (_, raw) in self._files().items():
            e = self._entry(raw, reviews.get(rid))
            if e is not None and (include_all or e.get("verified_by_human")):
                items.append(e)
        return items

    def get(self, robot_id: str) -> Optional[dict]:
        f = self._files().get(robot_id)
        if f is None:
            return None
        return self._entry(f[1], self._reviews().get(robot_id))

    def spec(self, robot_id: str) -> Optional[schemas.RobotSpec]:
        e = self.get(robot_id)
        if e is None:
            return None
        e = {k: v for k, v in e.items() if k != "review"}
        return schemas.RobotSpec.model_validate(e)

    def verify(self, robot_id: str, *, verified_by: str, edits: Optional[dict] = None, notes: str = "",
               verified: bool = True) -> dict:
        edits = dict(edits or {})
        bad = sorted(set(edits) & EDITABLE_EXCLUDE)
        if bad:
            raise CatalogError(f"fields cannot be edited in review: {bad}")
        allowed = set(schemas.RobotSpec.model_fields) | {"class"}
        unknown = sorted(k for k in edits if k not in allowed)
        if unknown:
            raise CatalogError(f"unknown RobotSpec fields: {unknown}")
        with self._lock:
            f = self._files().get(robot_id)
            if f is None:
                raise KeyError(robot_id)
            path, raw = f
            with self.db.session() as s:
                prev = s.get(RobotReviewRow, robot_id)
                merged_edits = {**(prev.edits if prev else {}), **edits}
                candidate = {**raw, **merged_edits, "verified_by_human": verified}
                try:
                    schemas.RobotSpec.model_validate(candidate)
                except ValidationError as e:
                    raise CatalogError(f"edited spec is invalid: {e.errors()[:3]}") from e
                at = now_iso()
                entry = {"verified": verified, "verified_by": verified_by, "verified_at": at,
                         "edits": edits, "notes": notes}
                if prev is None:
                    s.add(RobotReviewRow(robot_id=robot_id, verified=verified, verified_by=verified_by,
                                         verified_at=at, edits=merged_edits, notes=notes, history=[entry]))
                else:
                    prev.verified, prev.verified_by, prev.verified_at = verified, verified_by, at
                    prev.edits, prev.notes = merged_edits, notes
                    prev.history = [*(prev.history or []), entry]
            if self.writeback:
                self._write_back(path, raw, merged_edits, verified, verified_by, at, notes)
        return self.get(robot_id)

    @staticmethod
    def _write_back(path: Path, raw: dict, edits: dict, verified: bool, who: str, at: str, notes: str) -> None:
        d = {**raw, **edits, "verified_by_human": verified,
             "review": {"verified_by": who, "verified_at": at, "edited_fields": sorted(edits), "notes": notes}}
        try:
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp_")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(d, fh, indent=2)
            os.replace(tmp, path)
        except OSError as e:
            log.warning("catalog write-back to %s failed (review kept in DB): %s", path, e)
