"""Import bundled sample sites (sample_sites/<name>/site.json + twin_v*/ + optional analyses/) at startup.

Sample files are copied into storage under the normal key layout and registered read-only: their imported
twin versions and analyses are never modified. Editing a sample twin creates a new version in storage.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import select

from services.api.core import schemas
from services.api.db import AnalysisRow, SiteRow, TwinRow
from services.api.security import BadKey, safe_id

log = logging.getLogger("clearance.api.samples")

_TWIN_DIR = re.compile(r"^twin_v(\d+)$")
SITE_TYPES = {"cafe", "restaurant", "storeroom", "warehouse", "corridor", "other"}


def _remap(uri, twin_dir: Path, dst_prefix: str):
    """Point a sample twin's file reference at its copied location."""
    if not isinstance(uri, str) or not uri or uri.startswith(("http://", "https://", "s3://")):
        return uri
    if (twin_dir / uri).is_file():
        return dst_prefix + Path(uri).as_posix()
    m = re.search(r"twin_v\d+/(.+)$", uri)
    if m and (twin_dir / m.group(1)).is_file():
        return dst_prefix + m.group(1)
    name = uri.rsplit("/", 1)[-1]
    if (twin_dir / name).is_file():
        return dst_prefix + name
    return uri


def _copy_tree(app, src: Path, dst_prefix: str) -> int:
    n = 0
    for p in sorted(src.rglob("*")):
        if p.is_file() and not any(part.startswith(".") for part in p.relative_to(src).parts):
            rel = p.relative_to(src).as_posix()
            try:
                n += app.store.copy_file_in(p, dst_prefix + rel)
            except BadKey:
                log.warning("skipping sample file with unsupported name: %s", p)
    return n


def import_sample_site(app, d: Path) -> str | None:
    meta = json.loads((d / "site.json").read_text(encoding="utf-8"))
    site_id = meta.get("site_id") or d.name
    safe_id(site_id, "sample site id")
    twin_dirs = sorted(((int(m.group(1)), p) for p in d.iterdir() if p.is_dir() and (m := _TWIN_DIR.match(p.name))
                        and (p / "twin.json").is_file()), key=lambda t: t[0])
    first_twin = json.loads((twin_dirs[0][1] / "twin.json").read_text(encoding="utf-8")) if twin_dirs else {}
    geometry_source = meta.get("geometry_source") or first_twin.get("geometry_source") or "scan"
    with app.db.session() as s:
        row = s.get(SiteRow, site_id)
        if row is not None and not row.sample:
            log.warning("sample site %s collides with a user site; skipped", site_id)
            return None
        if row is None:
            row = SiteRow(id=site_id)
            s.add(row)
        row.name = str(meta.get("name") or site_id)[:200]
        row.type = meta.get("type") if meta.get("type") in SITE_TYPES else "other"
        row.area_m2 = meta.get("area_m2")
        row.marker = meta.get("marker") or {"family": "tag36h11", "id": 0, "size_m": 0.160}
        row.geometry_source = geometry_source if geometry_source in ("scan", "reference") else "scan"
        row.sample, row.readonly = True, True
        row.meta = {k: v for k, v in meta.items() if k not in ("site_id",)}
    app.store.write_json(f"sites/{site_id}/site.json", {**meta, "site_id": site_id, "sample": True})

    for n, tdir in twin_dirs:
        dst = f"sites/{site_id}/twin_v{n}/"
        _copy_tree(app, tdir, dst)
        data = json.loads((tdir / "twin.json").read_text(encoding="utf-8"))
        data["occupancy"] = dict(data.get("occupancy") or {})
        data["occupancy"]["uri"] = _remap(data["occupancy"].get("uri"), tdir, dst)
        data["assets"] = {k: _remap(v, tdir, dst) for k, v in (data.get("assets") or {}).items()}
        if isinstance(data.get("traffic"), dict) and data["traffic"].get("heatmap_uri"):
            data["traffic"]["heatmap_uri"] = _remap(data["traffic"]["heatmap_uri"], tdir, dst)
        data.update(site_id=site_id, version=n)
        data.setdefault("twin_id", f"twin_{site_id}_v{n}")
        try:
            twin = schemas.Twin.model_validate(data)
        except ValidationError as e:
            log.warning("sample twin %s v%d is invalid, skipped: %s", site_id, n, e.errors()[:3])
            continue
        key = dst + "twin.json"
        app.store.write_json(key, twin.model_dump(mode="json", by_alias=True))
        with app.db.session() as s:
            tr = s.scalars(select(TwinRow).where(TwinRow.site_id == site_id, TwinRow.version == n)).first()
            if tr is not None and not tr.readonly:
                log.warning("sample twin %s v%d collides with an edited version; kept the edit", site_id, n)
                continue
            if tr is None:
                tr = TwinRow(site_id=site_id, version=n)
                s.add(tr)
            tr.twin_id, tr.status, tr.key, tr.source, tr.readonly = twin.twin_id, "ready", key, "sample", True

    adir = d / "analyses"
    if adir.is_dir():
        for a in sorted(p for p in adir.iterdir() if p.is_dir()):
            try:
                aid = safe_id(a.name, "sample analysis id")
            except BadKey:
                continue
            _copy_tree(app, a, f"analyses/{aid}/")
            doc = {}
            if (a / "analysis.json").is_file():
                try:
                    doc = json.loads((a / "analysis.json").read_text(encoding="utf-8"))
                except ValueError:
                    doc = {}
            inputs = doc.get("inputs") or {}
            with app.db.session() as s:
                ar = s.get(AnalysisRow, aid)
                if ar is not None and not ar.readonly:
                    continue
                if ar is None:
                    ar = AnalysisRow(id=aid)
                    s.add(ar)
                ar.site_id, ar.readonly = site_id, True
                ar.status = "done" if str(doc.get("status", "done")).lower() in (
                    "done", "complete", "completed", "succeeded") else str(doc.get("status"))[:16]
                ar.twin_version = inputs.get("twin_version") or (twin_dirs[-1][0] if twin_dirs else None)
                ar.robot_ids = list(inputs.get("robot_ids") or [])
            app._index_batches(aid)
    return site_id


def import_sample_sites(app) -> list[str]:
    root = Path(app.settings.sample_dir)
    out = []
    if not root.is_dir():
        return out
    for d in sorted(p for p in root.iterdir() if p.is_dir() and (p / "site.json").is_file()):
        try:
            sid = import_sample_site(app, d)
        except Exception as e:  # noqa: BLE001 - one bad sample must not stop the API
            log.warning("sample site %s could not be imported: %s", d.name, e)
            continue
        if sid:
            out.append(sid)
    if out:
        log.info("imported sample sites: %s", ", ".join(out))
    return out
