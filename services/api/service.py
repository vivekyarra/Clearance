"""Clearance API application services: sites, captures, twins, Clearance checks (analyses), batches."""
from __future__ import annotations

import json
import logging
import shutil
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, urlencode, urlsplit

from pydantic import ValidationError
from sqlalchemy import func, select

from services.api import tasks
from services.api.auth import Auth
from services.api.capture_io import (CaptureError, assemble, capture_root_key, chunk_key, file_key,
                                     find_capture_root)
from services.api.catalog import Catalog
from services.api.core import schemas, set_data_root
from services.api.db import AnalysisRow, CaptureRow, Database, JobRow, SiteRow, TwinRow, new_id, now_iso
from services.api.dispatch import Dispatcher, make_dispatcher
from services.api.episodes import EpisodeCache
from services.api.lazy import require
from services.api.live import KeyedRateLimiter, LiveManager
from services.api.security import BadKey, TokenSigner, safe_id, safe_key
from services.api.settings import Settings
from services.api.store import make_store

log = logging.getLogger("clearance.api")

SUCCESS_WORDS = {"done", "complete", "completed", "succeeded", "success"}
MAX_CAPTURE_JSON_BYTES = 1 << 20


class ApiError(Exception):
    status = 400

    def __init__(self, detail: Any):
        super().__init__(str(detail))
        self.detail = detail


class NotFound(ApiError):
    status = 404


class Conflict(ApiError):
    status = 409


class Invalid(ApiError):
    status = 422


class Forbidden(ApiError):
    status = 403


MAX_CLEAR_ZONES = 20
MAX_CLEAR_ZONE_M2 = 4.0


def _twin_dump(t: schemas.Twin) -> dict:
    return t.model_dump(mode="json", by_alias=True)


def rewrite_prefix(obj, old: str, new: str):
    if isinstance(obj, str):
        return new + obj[len(old):] if obj.startswith(old) else obj
    if isinstance(obj, list):
        return [rewrite_prefix(v, old, new) for v in obj]
    if isinstance(obj, dict):
        return {k: rewrite_prefix(v, old, new) for k, v in obj.items()}
    return obj


def default_task(twin: schemas.Twin) -> schemas.Task:
    """Deliver from pickup to every destination, starting and ending at the dock."""
    dock = next((w.id for w in twin.waypoints if w.kind == "dock"), None)
    pickup = next((w.id for w in twin.waypoints if w.kind == "pickup"), None)
    dests = [w.id for w in twin.waypoints if w.kind == "dest"]
    if pickup is None or not dests:
        raise Invalid("twin needs a pickup and at least one dest waypoint (place them in the twin editor) "
                      "or pass an explicit task.sequence")
    seq = [pickup, *dests]
    if dock:
        seq = [dock, *seq, dock]
    return schemas.Task(sequence=seq)


class App:
    def __init__(self, settings: Settings):
        self.settings = settings
        set_data_root(settings.data_dir)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(settings.database_url)
        self.signer = TokenSigner(settings.load_secret())
        self.auth = Auth(settings.load_operator_token(), self.signer, join_ttl_s=settings.join_ttl_s,
                         capture_ttl_s=settings.capture_token_ttl_s, live_ttl_s=settings.live_token_ttl_s)
        self.store = make_store(settings, self.signer)
        self.dispatcher: Dispatcher = make_dispatcher(settings, self.db)
        self.catalog = Catalog(settings.catalog_dir, self.db, writeback=settings.catalog_writeback)
        self.live = LiveManager(self.db, self.store, hz=settings.live_hz, max_sessions=settings.live_max_sessions,
                                idle_ttl_s=settings.live_idle_ttl_s, unclaimed_ttl_s=settings.live_unclaimed_ttl_s,
                                max_per_site=settings.live_max_per_site, max_clients=settings.live_max_clients,
                                msg_rate=settings.ws_msg_rate, msg_burst=settings.ws_msg_burst,
                                allowed_origins=self.ws_origins(), authorize=self.live_ws_allowed)
        self.live_create_limiter = KeyedRateLimiter(settings.live_create_per_min)
        self.episodes = EpisodeCache()
        self._capture_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
        self._batch_index: dict[str, str] = {}

    # ------------------------------------------------------------------ auth helpers
    def ws_origins(self) -> list[str]:
        out = list(self.settings.cors_origins)
        for base in (self.settings.public_web_base, self.settings.public_api_base):
            if base:
                u = urlsplit(base)
                if u.scheme and u.netloc:
                    out.append(f"{u.scheme}://{u.netloc}")
        return out

    def live_ws_allowed(self, token: Optional[str], st) -> bool:
        p = self.auth.resolve(token)
        if p is None:
            return False
        if p.is_operator:
            return True
        if p.kind == "join":
            return p.site_id == st.site_id
        if p.kind == "live":
            return p.session_id == st.id and p.site_id == st.site_id
        return False

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self.db.create_all()
        n = self.dispatcher.recover_interrupted()
        if n:
            log.info("marked %d interrupted jobs as failed", n)
        with self.db.session() as s:
            for r in s.query(AnalysisRow).filter(AnalysisRow.status.in_(["queued", "running"]),
                                                 AnalysisRow.readonly.is_(False)):
                r.status, r.error = "failed", "interrupted by API restart"
            for r in s.query(CaptureRow).filter(CaptureRow.status == "processing"):
                r.status, r.error = "failed", "interrupted by API restart; POST /captures/{id}/complete to retry"
        if self.settings.import_samples:
            from services.api.samples import import_sample_sites

            import_sample_sites(self)

    def stop(self) -> None:
        self.dispatcher.shutdown(wait=False)
        self.db.dispose()

    # ------------------------------------------------------------------ sites
    def site_row(self, site_id: str) -> SiteRow:
        try:
            safe_id(site_id, "site id")
        except BadKey as e:
            raise NotFound(str(e)) from e
        with self.db.session() as s:
            row = s.get(SiteRow, site_id)
        if row is None:
            raise NotFound(f"site {site_id} not found")
        return row

    def site_dict(self, row: SiteRow) -> dict:
        with self.db.session() as s:
            latest = s.scalar(select(func.max(TwinRow.version)).where(TwinRow.site_id == row.id,
                                                                      TwinRow.status == "ready"))
            n_an = s.scalar(select(func.count()).select_from(AnalysisRow).where(AnalysisRow.site_id == row.id))
        return {"site_id": row.id, "name": row.name, "type": row.type, "area_m2": row.area_m2,
                "marker": row.marker, "geometry_source": row.geometry_source, "sample": row.sample,
                "readonly": row.readonly, "created_at": row.created_at, "latest_twin_version": latest,
                "n_analyses": n_an or 0}

    def list_sites(self) -> list[dict]:
        with self.db.session() as s:
            rows = s.query(SiteRow).order_by(SiteRow.sample.desc(), SiteRow.created_at).all()
        return [self.site_dict(r) for r in rows]

    def create_site(self, name: str, type_: str, area_m2: Optional[float], marker_size_m: float) -> SiteRow:
        row = SiteRow(id=new_id("site"), name=name, type=type_, area_m2=area_m2,
                      marker={"family": "tag36h11", "id": 0, "size_m": marker_size_m}, geometry_source="scan")
        with self.db.session() as s:
            s.add(row)
        self.store.write_json(f"sites/{row.id}/site.json", self.site_dict(row))
        return row

    @staticmethod
    def join_url(row: SiteRow, api_base: str, web_base: str, token: Optional[str] = None) -> str:
        """The string encoded in the site QR code (docs/CONTRACTS.md "Site QR code"). `token` is the
        short-lived site join token; it is only issued to the operator."""
        items = {"api": api_base.rstrip("/"), "name": row.name}
        if token:
            items["token"] = token
        q = urlencode(items, quote_via=quote)
        return f"{web_base.rstrip('/')}/join/{quote(row.id, safe='')}?{q}"

    @classmethod
    def qr_payload(cls, row: SiteRow, api_base: str, web_base: str, token: Optional[str] = None,
                   token_expires_at: Optional[int] = None) -> dict:
        out = {"v": 1, "kind": "clearance-site", "site_id": row.id, "name": row.name,
               "api_base": api_base.rstrip("/"), "join_url": cls.join_url(row, api_base, web_base, token),
               "marker": row.marker}
        if token:
            out.update(token=token, token_expires_at=token_expires_at)
        return out

    def site_qr(self, row: SiteRow, api_base: str, web_base: str, *, with_token: bool) -> dict:
        """QR payload; `with_token` (operator requests only) adds a fresh join token for writable sites."""
        token, exp = self.auth.join_token(row.id) if with_token and not row.readonly else (None, None)
        return self.qr_payload(row, api_base, web_base, token, exp)

    # ------------------------------------------------------------------ twins
    def twin_row(self, site_id: str, version: Optional[int] = None) -> TwinRow:
        self.site_row(site_id)
        with self.db.session() as s:
            q = select(TwinRow).where(TwinRow.site_id == site_id, TwinRow.status == "ready")
            q = q.where(TwinRow.version == version) if version is not None else q.order_by(TwinRow.version.desc())
            row = s.scalars(q.limit(1)).first()
        if row is None:
            raise NotFound(f"site {site_id} has no twin" + (f" version {version}" if version is not None else " yet"))
        return row

    def get_twin(self, site_id: str, version: Optional[int] = None) -> dict:
        row = self.twin_row(site_id, version)
        return self.store.read_json(row.key)

    def twin_model(self, site_id: str, version: Optional[int] = None) -> schemas.Twin:
        return schemas.Twin.model_validate(self.get_twin(site_id, version))

    def list_twins(self, site_id: str) -> list[dict]:
        self.site_row(site_id)
        with self.db.session() as s:
            rows = s.scalars(select(TwinRow).where(TwinRow.site_id == site_id, TwinRow.status == "ready")
                             .order_by(TwinRow.version)).all()
        return [{"twin_id": r.twin_id, "version": r.version, "source": r.source, "parent_version": r.parent_version,
                 "capture_id": r.capture_id, "readonly": r.readonly, "created_at": r.created_at} for r in rows]

    def _mark_twin_ready(self, site_id: str, version: int, key: str) -> None:
        with self.db.session() as s:
            row = s.scalars(select(TwinRow).where(TwinRow.site_id == site_id, TwinRow.version == version)).one()
            row.status, row.key = "ready", key

    def _drop_pending(self, site_id: str, version: int) -> None:
        with self.db.session() as s:
            row = s.scalars(select(TwinRow).where(TwinRow.site_id == site_id, TwinRow.version == version)).first()
            if row is not None and row.status == "pending":
                s.delete(row)

    TWIN_ASSET_KEYS = ("splat", "mesh", "mesh_glb", "occluder_usdz", "pointcloud")

    def asset_url_allowed(self, value: str) -> bool:
        """Absolute twin asset URLs must be https on an allowlisted host (CLEARANCE_ASSET_HOSTS)."""
        try:
            u = urlsplit(value)
        except ValueError:
            return False
        host = (u.hostname or "").lower()
        return (u.scheme == "https" and bool(host) and not u.username and not u.password
                and host in {h.lower() for h in self.settings.asset_hosts})

    # Asset entries that record provenance rather than point at a file (written by the twin builder).
    ASSET_METADATA_KEYS = frozenset({"capture_id"})

    def check_assets(self, site_id: str, assets: dict, previous: Optional[dict] = None) -> None:
        previous = previous or {}
        for name, value in (assets or {}).items():
            if value is None:
                continue
            if previous.get(name) == value:
                continue  # a value the server itself stored on the base version
            if name in self.ASSET_METADATA_KEYS:  # provenance: must name a capture of this site
                try:
                    ok = self.capture_row(str(value)).site_id == site_id
                except NotFound:
                    ok = False
                if not ok:
                    raise Invalid(f"assets.{name} must name a capture of this site")
                continue
            v = str(value)
            if v.startswith(("http://", "https://")) or "://" in v or v.startswith("//"):
                if not self.asset_url_allowed(v):
                    raise Invalid(f"assets.{name} must be a storage key of this site or an https URL on an "
                                  f"allowed asset host")
                continue
            try:
                safe_key(v)
            except BadKey as e:
                raise Invalid(f"assets.{name} is not a valid storage key: {e}") from e
            if not v.startswith(f"sites/{site_id}/"):
                raise Invalid(f"assets.{name} must be stored with this site (sites/{site_id}/...)")

    def twin_asset(self, site_id: str, name: str, version: Optional[int] = None) -> tuple[str, Optional[Path]]:
        """Resolve GET /sites/{id}/twin/assets/{name}: (url, None) for absolute URLs, else (key, local path).

        `name` is an asset key ("splat", "occluder_usdz") or a file-style name ("splat.ply", "occluder.usdz");
        a given extension must match the stored file. Only keys under sites/<site_id>/ are served."""
        twin = self.get_twin(site_id, version)
        assets = twin.get("assets") or {}
        stem, dot, ext = name.partition(".")
        candidates = [name] if not dot else [stem, f"{stem}_{ext.replace('.', '_')}"]
        key = next((k for k in candidates if k in self.TWIN_ASSET_KEYS and assets.get(k)), None)
        if key is None:
            raise NotFound(f"twin v{twin.get('version')} has no asset {name!r}")
        value = str(assets[key])
        if value.startswith(("http://", "https://")):
            if not self.asset_url_allowed(value):  # never an open redirect
                raise NotFound(f"asset {key} points at a host that is not allowed")
            return value, None
        try:
            value = safe_key(value)
        except BadKey as e:
            raise NotFound(f"asset {key} is not a valid storage key") from e
        if not value.startswith(f"sites/{site_id}/"):
            raise NotFound(f"asset {key} is not stored with this site")
        if dot and not value.lower().endswith("." + ext.lower()):
            raise NotFound(f"asset {key} is not a .{ext} file")
        try:
            return value, self.store.ensure_local(value)
        except FileNotFoundError as e:
            raise NotFound(f"asset {key} file is missing") from e

    def occupancy_path(self, site_id: str, version: Optional[int] = None) -> Path:
        twin = self.get_twin(site_id, version)
        uri = (twin.get("occupancy") or {}).get("uri")
        try:
            return self.store.ensure_local(safe_key(uri))
        except (BadKey, FileNotFoundError, TypeError) as e:
            raise NotFound(f"occupancy image missing for twin v{twin.get('version')}") from e

    def save_twin(self, site_id: str, body: dict, *, force: bool = False, confirmed_by: str = "operator") -> dict:
        """PUT: validate the edited twin and store it as version N+1 (versions are immutable).
        Clear zones missing confirmed_by/confirmed_at are stamped with the saving operator and the save time."""
        self.site_row(site_id)
        try:
            twin = schemas.Twin.model_validate(body)
        except ValidationError as e:
            raise Invalid({"message": "twin does not match the Twin schema", "errors": e.errors()[:20]}) from e
        try:
            base = self.twin_row(site_id)
        except NotFound:
            base = None
        if base is not None and not force and twin.version != base.version:
            raise Conflict({"message": f"twin was saved as version {base.version} after the version you edited "
                                       f"({twin.version}); reload and re-apply, or pass ?force=true",
                            "latest_version": base.version})
        for label, items in (("primitive", twin.primitives), ("waypoint", twin.waypoints),
                             ("threshold", twin.thresholds), ("keep-out zone", twin.keepout_zones),
                             ("clear zone", twin.clear_zones)):
            ids = [i.id for i in items]
            dup = sorted({i for i in ids if ids.count(i) > 1})
            if dup:
                raise Invalid(f"duplicate {label} ids: {dup}")
        occ_uri = twin.occupancy.uri
        try:
            safe_key(occ_uri)
        except BadKey as e:
            raise Invalid(f"occupancy.uri is not a valid storage key: {e}") from e
        if not occ_uri.startswith(f"sites/{site_id}/") or not self.store.exists(occ_uri):
            raise Invalid("occupancy.uri must reference an existing occupancy image of this site")
        if twin.clear_zones:
            self._check_clear_zones(twin, confirmed_by)
        prev_assets = {}
        if base is not None:
            try:
                prev_assets = self.get_twin(site_id, base.version).get("assets") or {}
            except NotFound:
                prev_assets = {}
        self.check_assets(site_id, twin.assets, prev_assets)

        twin_id = new_id("twin")
        v = self.db.reserve_twin_version(site_id, twin_id=twin_id, source="edit",
                                         parent_version=base.version if base else None)
        prefix = f"sites/{site_id}/twin_v{v}/"
        try:
            occ_dst = prefix + Path(occ_uri).name
            shutil.copyfile(self.store.ensure_local(occ_uri), self.store.path(occ_dst))
            self.store.publish(occ_dst)
            data = _twin_dump(twin)
            data.update(twin_id=twin_id, site_id=site_id, version=v)
            data["occupancy"]["uri"] = occ_dst
            key = prefix + "twin.json"
            self.store.write_json(key, data)
            self._mark_twin_ready(site_id, v, key)
        except Exception:
            self._drop_pending(site_id, v)
            raise
        return data

    def _occupancy_extent(self, twin: schemas.Twin) -> tuple[float, float, float, float]:
        """(xmin, ymin, xmax, ymax) of the occupancy grid in metres."""
        occ = twin.occupancy
        w, h = int(occ.width or 0), int(occ.height or 0)
        if w <= 0 or h <= 0:
            import cv2
            img = cv2.imread(str(self.store.ensure_local(occ.uri)), cv2.IMREAD_GRAYSCALE)
            if img is None:
                raise Invalid("occupancy image could not be read")
            h, w = img.shape
        x0, y0 = float(occ.origin_xy[0]), float(occ.origin_xy[1])
        return x0, y0, x0 + w * occ.resolution_m, y0 + h * occ.resolution_m

    def _check_clear_zones(self, twin: schemas.Twin, confirmed_by: str) -> None:
        """Operator-confirmed clear floor: at most MAX_CLEAR_ZONES zones, each a simple polygon of at least three
        points inside the grid with an area of at most MAX_CLEAR_ZONE_M2."""
        import math

        from shapely.geometry import Polygon
        zones = twin.clear_zones
        if len(zones) > MAX_CLEAR_ZONES:
            raise Invalid(f"at most {MAX_CLEAR_ZONES} clear zones per twin (got {len(zones)})")
        xmin, ymin, xmax, ymax = self._occupancy_extent(twin)
        tol = twin.occupancy.resolution_m
        stamp = now_iso()
        for z in zones:
            if len(z.polygon) < 3:
                raise Invalid(f"clear zone {z.id}: polygon needs at least 3 points")
            if not all(math.isfinite(x) and math.isfinite(y) for x, y in z.polygon):
                raise Invalid(f"clear zone {z.id}: polygon has non-finite coordinates")
            if any(x < xmin - tol or x > xmax + tol or y < ymin - tol or y > ymax + tol for x, y in z.polygon):
                raise Invalid(f"clear zone {z.id}: polygon must lie inside the occupancy grid "
                              f"([{xmin:.2f}, {xmax:.2f}] x [{ymin:.2f}, {ymax:.2f}] m)")
            poly = Polygon(z.polygon)
            if poly.area <= 1e-6:
                raise Invalid(f"clear zone {z.id}: polygon has no area")
            if not poly.is_valid:
                raise Invalid(f"clear zone {z.id}: polygon edges must not cross")
            if poly.area > MAX_CLEAR_ZONE_M2 + 1e-9:
                raise Invalid(f"clear zone {z.id}: area {poly.area:.2f} m² exceeds {MAX_CLEAR_ZONE_M2:g} m²; "
                              f"confirm smaller patches or rescan the area")
            if not z.confirmed_by:
                z.confirmed_by = confirmed_by
            if not z.confirmed_at:
                z.confirmed_at = stamp

    # ------------------------------------------------------------------ captures
    def capture_row(self, capture_id: str) -> CaptureRow:
        try:
            safe_id(capture_id, "capture id")
        except BadKey as e:
            raise NotFound(str(e)) from e
        with self.db.session() as s:
            row = s.get(CaptureRow, capture_id)
        if row is None:
            raise NotFound(f"capture {capture_id} not found")
        return row

    @staticmethod
    def _expected_keys(row: CaptureRow) -> list[str]:
        return [chunk_key(row.id, i) for i in range(row.n_chunks)] + [file_key(row.id, f) for f in row.files]

    def _upload_urls(self, row: CaptureRow, api_base: str, *, parts=None, files=None) -> dict:
        ttl, mx = self.settings.upload_ttl_s, self.settings.max_upload_bytes
        parts = range(row.n_chunks) if parts is None else parts
        files = row.files if files is None else files
        return {
            "chunks": [{"part": i, **self.store.presign_put(chunk_key(row.id, i), api_base=api_base, ttl_s=ttl,
                                                            capture_id=row.id, max_bytes=mx)} for i in parts],
            "files": [{"name": f, **self.store.presign_put(file_key(row.id, f), api_base=api_base, ttl_s=ttl,
                                                           capture_id=row.id, max_bytes=mx)} for f in files],
        }

    @staticmethod
    def _check_names(names: list[str]) -> list[str]:
        out = []
        for n in names:
            try:
                out.append(safe_key(n, max_len=256))
            except BadKey as e:
                raise Invalid(f"invalid capture file name: {e}") from e
        if len(set(out)) != len(out):
            raise Invalid("duplicate capture file names")
        return out

    def register_capture(self, site_id: str, body: dict, api_base: str) -> dict:
        site = self.site_row(site_id)
        if site.readonly:
            raise Forbidden(f"site {site_id} is a read-only sample site; create a new site to upload captures")
        files = self._check_names(body.get("files") or [])
        meta = {k: body.get(k) for k in ("device", "marker", "started_at", "duration_s", "privacy", "capture_files")}
        meta["marker"] = meta["marker"] or site.marker
        row = CaptureRow(id=new_id("cap"), site_id=site_id, role=body["role"], tier=body["device"]["tier"],
                         n_chunks=int(body.get("n_chunks", 1)), chunking=body.get("chunking", "independent"),
                         files=files, meta=meta, status="registered")
        with self.db.session() as s:
            s.add(row)
        return {**self.capture_status(row.id), "upload": self._upload_urls(row, api_base),
                "complete_url": f"{api_base.rstrip('/')}/captures/{row.id}/complete",
                "device_token": self.auth.capture_token(row.id, site_id),
                "device_token_expires_in_s": int(self.settings.capture_token_ttl_s)}

    def more_upload_urls(self, capture_id: str, body: dict, api_base: str) -> dict:
        """Resumable uploads: refresh URLs (all, or the given parts/files) and optionally register more."""
        row = self.capture_row(capture_id)
        if row.status not in ("registered", "failed"):
            raise Conflict(f"capture {capture_id} is {row.status}; uploads are closed")
        add_chunks = int(body.get("add_chunks") or 0)
        new_files = self._check_names(body.get("add_files") or [])
        if add_chunks < 0 or row.n_chunks + add_chunks > 10000:
            raise Invalid("add_chunks out of range")
        with self.db.session() as s:
            r = s.get(CaptureRow, capture_id)
            r.n_chunks += add_chunks
            r.files = list(dict.fromkeys([*r.files, *new_files]))
            row = r
        parts, files = body.get("parts"), body.get("files")
        if parts is None and files is None:
            parts, files = list(range(row.n_chunks)), row.files
        parts, files = list(parts or []), self._check_names(files or [])
        if any(not isinstance(p, int) or not 0 <= p < row.n_chunks for p in parts):
            raise Invalid("parts must be existing chunk indexes")
        unknown = [f for f in files if f not in row.files]
        if unknown:
            raise Invalid(f"files not registered on this capture: {unknown}")
        return {"capture_id": row.id, "upload": self._upload_urls(row, api_base, parts=parts, files=files)}

    def capture_status(self, capture_id: str) -> dict:
        row = self.capture_row(capture_id)
        expected = self._expected_keys(row)
        missing = [k for k in expected if not self.store.exists(k)]
        job = self._poll_remote(row.job_id)
        if job and job["status"] in ("succeeded", "failed") and row.status == "processing":
            row = self.capture_row(capture_id)
        meta = row.meta or {}
        n_up = len(expected) - len(missing)
        if row.status in ("registered", "failed") and missing:
            upload_status = "uploading" if n_up else "waiting"
        else:
            upload_status = "uploaded"
        processing_status = "waiting" if row.status == "registered" else row.status
        return {"capture_id": row.id, "site_id": row.site_id, "role": row.role, "tier": row.tier,
                "status": row.status, "n_chunks": row.n_chunks, "chunking": row.chunking, "files": row.files,
                "missing": missing, "twin_version": row.twin_version, "error": row.error,
                "upload": {"status": upload_status, "n_expected": len(expected), "n_uploaded": n_up,
                           "progress": round(n_up / len(expected), 4) if expected else 0.0},
                "processing": {"status": processing_status, "twin_version": row.twin_version, "error": row.error},
                "device": meta.get("device"), "marker": meta.get("marker"), "started_at": meta.get("started_at"),
                "duration_s": meta.get("duration_s"), "privacy": meta.get("privacy"),
                "job": {k: job[k] for k in ("job_id", "status", "error", "duration_s")} if job else None,
                "created_at": row.created_at, "updated_at": row.updated_at}

    def list_captures(self, site_id: str) -> list[dict]:
        self.site_row(site_id)
        with self.db.session() as s:
            ids = s.scalars(select(CaptureRow.id).where(CaptureRow.site_id == site_id)
                            .order_by(CaptureRow.created_at)).all()
        return [self.capture_status(cid) for cid in ids]

    def upload_allowed(self, capture_id: str, key: Optional[str] = None) -> None:
        """Uploads are open while the capture is registered (or failed, for a retry). Each key is written
        once: a second PUT to an uploaded key is refused unless the capture failed."""
        row = self.capture_row(capture_id)
        if row.status not in ("registered", "failed"):
            raise Conflict(f"capture {capture_id} is {row.status}; uploads are closed")
        if key is not None and row.status != "failed" and self.store.exists(key):
            raise Conflict(f"{key} was already uploaded; upload tokens are single-use")

    def upload_lock(self, capture_id: str) -> threading.Lock:
        return self._capture_locks[capture_id]

    def _write_capture_json(self, row: CaptureRow, root: Path) -> dict:
        p = root / "capture.json"
        doc: dict = {}
        if p.is_file():
            if p.stat().st_size > MAX_CAPTURE_JSON_BYTES:
                raise Invalid(f"capture.json is larger than {MAX_CAPTURE_JSON_BYTES} bytes")
            try:
                doc = json.loads(p.read_text(encoding="utf-8"))
            except (ValueError, RecursionError) as e:
                raise Invalid("capture.json in the upload is not valid JSON") from e
            if not isinstance(doc, dict):
                raise Invalid("capture.json must hold a JSON object")
        meta = row.meta or {}
        doc.setdefault("device", meta.get("device"))
        if meta.get("marker"):
            doc.setdefault("marker", meta["marker"])
        if meta.get("capture_files"):
            doc.setdefault("files", meta["capture_files"])
        for k in ("started_at", "duration_s", "privacy"):
            if meta.get(k) is not None:
                doc.setdefault(k, meta[k])
        doc.update(capture_id=row.id, site_id=row.site_id, role=row.role)
        try:
            cap = schemas.Capture.model_validate(doc)
        except ValidationError as e:
            raise Invalid({"message": "capture metadata does not match the Capture schema",
                           "errors": e.errors()[:20]}) from e
        out = cap.model_dump(mode="json")
        p.write_text(json.dumps(out, indent=2), encoding="utf-8")
        return out

    def complete_capture(self, capture_id: str) -> dict:
        with self._capture_locks[capture_id]:
            row = self.capture_row(capture_id)
            if row.status in ("processing", "done", "stored"):
                return {**self.capture_status(capture_id), "cached": True}
            expected = self._expected_keys(row)
            missing = [k for k in expected if not self.store.exists(k)]
            if missing:
                raise Conflict({"message": "upload incomplete", "missing": missing})
            if not expected:
                raise Conflict("capture has no registered chunks or files")
            if row.role == "scanner":
                require("jobs.recon.pipeline", "build_twin_from_capture")
            for k in expected:
                self.store.ensure_local(k)
            base = self.store.dir(capture_root_key(row.id))
            try:
                assemble([self.store.path(chunk_key(row.id, i)) for i in range(row.n_chunks)], base,
                         chunking=row.chunking, max_bytes=self.settings.max_extract_bytes,
                         work_dir=self.store.dir(f"captures/{row.id}/work"))
            except CaptureError as e:
                self._set_capture(row.id, status="failed", error=str(e))
                raise Invalid(str(e)) from e
            root = find_capture_root(base)
            cap_doc = self._write_capture_json(row, root)

            if row.role == "observer":
                self._set_capture(row.id, status="stored", error=None)
                return {**self.capture_status(row.id), "cached": False,
                        "note": "observer capture stored for traffic modelling (J5)"}

            # capture.json is hashed without its capture_id so identical uploads share one twin build
            meta = {k: v for k, v in cap_doc.items() if k != "capture_id"}
            input_hash = tasks.hash_tree(root, exclude=("capture.json",),
                                         extra={"capture": meta, "tier": row.tier, "v": tasks.RECON_VERSION})
            jid = self.dispatcher.job_id_for("recon", input_hash)
            job = self.dispatcher.get(jid)
            if job and job["status"] == "succeeded":
                with self.db.session() as s:
                    tw = s.scalars(select(TwinRow).where(TwinRow.job_id == jid, TwinRow.site_id == row.site_id,
                                                         TwinRow.status == "ready")).first()
                if tw is not None:
                    self._set_capture(row.id, status="done", input_hash=input_hash, job_id=jid,
                                      twin_version=tw.version, error=None)
                    return {**self.capture_status(row.id), "cached": True}
            self._set_capture(row.id, status="processing", input_hash=input_hash, job_id=jid, error=None)
            root_key = root.resolve().relative_to(self.store.root.resolve()).as_posix()
            if self.store.backend != "local":  # remote jobs read the extracted capture from the bucket
                self.store.publish_many(f"{root_key}/{p.relative_to(root).as_posix()}"
                                        for p in root.rglob("*") if p.is_file())
            try:
                self.dispatcher.submit("recon", "services.api.tasks:run_recon",
                                       {"capture_dir": root_key, "site_id": row.site_id,
                                        "out_prefix": f"jobs/{jid}/twin/", "tier": row.tier,
                                        **self._job_data_root()},
                                       input_hash=input_hash, on_success=self._recon_done,
                                       on_error=self._recon_failed)
            except Exception:
                self._set_capture(row.id, status="failed", error="dispatch failed")
                raise
            return {**self.capture_status(row.id), "cached": False}

    def _job_data_root(self) -> dict:
        return {"data_root": str(self.store.root)} if self.dispatcher.backend == "local" else {}

    def _poll_remote(self, job_id: Optional[str]) -> Optional[dict]:
        """Remote (Nebius) jobs: poll status and finalize on completion. Local jobs: plain lookup."""
        job = self.dispatcher.get(job_id) if job_id else None
        refresh = getattr(self.dispatcher, "refresh", None)
        if not job or job["status"] != "submitted" or refresh is None:
            return job

        def load(jid):
            return self.store.read_json(f"jobs/{jid}/result.json")

        if job["kind"] == "recon":
            cbs = (self._recon_done, self._recon_failed)
        else:
            with self.db.session() as s:
                ar = s.scalars(select(AnalysisRow).where(AnalysisRow.job_id == job_id)).first()
            aid = ar.id if ar else None
            cbs = ((lambda j, r: self._analysis_done(aid, r)), (lambda j, e: self._analysis_failed(aid, e)))                 if aid else (None, None)
        try:
            return refresh(job_id, load, on_success=cbs[0], on_error=cbs[1])
        except Exception as e:  # noqa: BLE001 - polling problems surface as the last known state
            log.warning("polling job %s failed: %s", job_id, e)
            return job

    def _set_capture(self, capture_id: str, **fields) -> None:
        with self.db.session() as s:
            r = s.get(CaptureRow, capture_id)
            for k, v in fields.items():
                setattr(r, k, v)

    def _recon_done(self, job_id: str, twin_dict: dict) -> None:
        with self.db.session() as s:
            caps = s.scalars(select(CaptureRow).where(CaptureRow.job_id == job_id)
                             .order_by(CaptureRow.created_at)).all()
        site_id = caps[0].site_id if caps else twin_dict.get("site_id")
        twin_id = new_id("twin")
        v = self.db.reserve_twin_version(site_id, twin_id=twin_id, source="capture", job_id=job_id,
                                         capture_id=caps[0].id if caps else None)
        src, dst = f"jobs/{job_id}/twin/", f"sites/{site_id}/twin_v{v}/"
        try:
            moved = self.store.move_tree(src, dst)
            data = rewrite_prefix(dict(twin_dict), src, dst)
            data.update(twin_id=twin_id, site_id=site_id, version=v)
            data = _twin_dump(schemas.Twin.model_validate(data))
            key = dst + "twin.json"
            self.store.write_json(key, data)
            self.store.publish_many(k for k in moved if k != key)
            self._mark_twin_ready(site_id, v, key)
        except Exception:
            self._drop_pending(site_id, v)
            raise
        with self.db.session() as s:
            for c in s.scalars(select(CaptureRow).where(CaptureRow.job_id == job_id)):
                c.status, c.twin_version, c.error = "done", v, None

    def _recon_failed(self, job_id: str, exc: BaseException) -> None:
        with self.db.session() as s:
            for c in s.scalars(select(CaptureRow).where(CaptureRow.job_id == job_id)):
                c.status, c.error = "failed", f"{type(exc).__name__}: {exc}"

    # ------------------------------------------------------------------ analyses (Clearance checks)
    def analysis_key(self, analysis_id: str, name: str = "analysis.json") -> str:
        return f"analyses/{analysis_id}/{name}"

    def analysis_row(self, analysis_id: str) -> AnalysisRow:
        try:
            safe_id(analysis_id, "analysis id")
        except BadKey as e:
            raise NotFound(str(e)) from e
        with self.db.session() as s:
            row = s.get(AnalysisRow, analysis_id)
        if row is None:
            raise NotFound(f"analysis {analysis_id} not found")
        return row

    def create_analysis(self, site_id: str, body: dict) -> tuple[dict, bool]:
        self.site_row(site_id)
        twin = self.twin_model(site_id, body.get("twin_version"))
        if body.get("task"):
            try:
                task = schemas.Task.model_validate(body["task"])
            except ValidationError as e:
                raise Invalid({"message": "invalid task", "errors": e.errors()}) from e
        else:
            task = default_task(twin)
        wp = {w.id for w in twin.waypoints}
        unknown = [w for w in task.sequence if w not in wp]
        if unknown or len(task.sequence) < 2:
            raise Invalid(f"task sequence must name at least two waypoints of twin v{twin.version}; "
                          f"unknown: {unknown}")
        robot_ids = body.get("robot_ids")
        if robot_ids:
            robots = []
            for rid in robot_ids:
                spec = self.catalog.spec(rid)
                if spec is None:
                    raise Invalid(f"robot {rid} is not in the catalog")
                if not spec.verified_by_human:
                    raise Invalid(f"robot {rid} is awaiting review; only reviewed robots enter simulations")
                robots.append(spec)
        else:
            robots = [s for s in (self.catalog.spec(e["robot_id"]) for e in self.catalog.list()) if s]
        if not robots:
            raise Invalid("no reviewed robots in the catalog")
        budget = body.get("budget") or {}
        business = body.get("business") or {}
        require("agents.orchestrator.run", "run_analysis")

        robots_d = [r.model_dump(mode="json", by_alias=True) for r in robots]
        twin_d = _twin_dump(twin)
        input_hash = tasks.hash_json({"twin": twin_d, "robots": robots_d, "task": task.model_dump(mode="json"),
                                      "business": business, "budget": budget, "v": tasks.ANALYSIS_VERSION})
        if not body.get("force"):
            with self.db.session() as s:
                prev = s.scalars(select(AnalysisRow).where(AnalysisRow.input_hash == input_hash,
                                                           AnalysisRow.status != "failed",
                                                           AnalysisRow.site_id == site_id)
                                 .order_by(AnalysisRow.created_at.desc())).first()
            if prev is not None:
                return {**self.analysis_status(prev.id), "cached": True}, False

        aid = new_id("an")
        doc = {"analysis_id": aid, "site_id": site_id, "status": "queued", "stages": {},
               "inputs": {"twin_id": twin.twin_id, "twin_version": twin.version,
                          "robot_ids": [r.robot_id for r in robots], "task": task.model_dump(mode="json"),
                          "business": business, "budget": budget},
               "created_at": now_iso()}
        self.store.write_json(self.analysis_key(aid), doc)
        job_hash = tasks.hash_json({"analysis_id": aid, "inputs": input_hash})
        jid = self.dispatcher.job_id_for("analysis", job_hash)
        with self.db.session() as s:
            s.add(AnalysisRow(id=aid, site_id=site_id, twin_version=twin.version, status="queued",
                              input_hash=input_hash, job_id=jid, robot_ids=[r.robot_id for r in robots]))
        self.dispatcher.submit("analysis", "services.api.tasks:run_analysis_job",
                               {"analysis_id": aid, "twin": twin_d, "robots": robots_d,
                                "task": task.model_dump(mode="json"), "business": business, "budget": budget,
                                **self._job_data_root()},
                               input_hash=job_hash,
                               on_success=lambda j, r, aid=aid: self._analysis_done(aid, r),
                               on_error=lambda j, e, aid=aid: self._analysis_failed(aid, e))
        return {**self.analysis_status(aid), "cached": False}, True

    def _analysis_done(self, aid: str, result) -> None:
        with self.db.session() as s:
            r = s.get(AnalysisRow, aid)
            r.status, r.error = "done", None
        self._index_batches(aid)

    def _analysis_failed(self, aid: str, exc: BaseException) -> None:
        msg = f"{type(exc).__name__}: {exc}"
        with self.db.session() as s:
            r = s.get(AnalysisRow, aid)
            r.status, r.error = "failed", msg
        try:
            doc = self.store.read_json(self.analysis_key(aid))
        except (FileNotFoundError, ValueError):
            doc = {"analysis_id": aid}
        doc.update(status="failed", error=msg)
        self.store.write_json(self.analysis_key(aid), doc)

    def analysis_doc(self, aid: str) -> dict:
        try:
            return self.store.read_json(self.analysis_key(aid))
        except FileNotFoundError:
            return {}
        except ValueError:
            return {}

    def analysis_status(self, aid: str) -> dict:
        row = self.analysis_row(aid)
        job = self._poll_remote(row.job_id)
        if job and job["status"] in ("succeeded", "failed") and row.status in ("queued", "running"):
            row = self.analysis_row(aid)
        doc = self.analysis_doc(aid)
        doc_status = str(doc.get("status") or "").lower()
        if row.status == "failed" or doc_status == "failed":
            status = "failed"
        elif row.status == "done":
            status = "done"
        elif job and job["status"] == "running":
            status = "running" if doc_status in ("", "queued") else doc_status
        else:
            status = doc_status or row.status
        stages = doc.get("stages") or {}
        progs = [float(v.get("progress", 1.0 if v.get("state") in SUCCESS_WORDS else 0.0))
                 for v in stages.values() if isinstance(v, dict)]
        progress = 1.0 if status == "done" else (round(sum(progs) / len(progs), 4) if progs else 0.0)
        return {**doc, "analysis_id": aid, "site_id": row.site_id, "twin_version": row.twin_version,
                "status": status, "progress": progress, "error": row.error or doc.get("error"),
                "verdicts": doc.get("verdicts") or [], "readonly": row.readonly, "job_id": row.job_id,
                "created_at": row.created_at,
                "links": {"report": f"/analyses/{aid}/report", "facts": f"/analyses/{aid}/facts",
                          "investigation": f"/analyses/{aid}/investigation"}}

    def list_analyses(self, site_id: Optional[str] = None) -> list[dict]:
        with self.db.session() as s:
            q = select(AnalysisRow).order_by(AnalysisRow.created_at.desc())
            if site_id:
                q = q.where(AnalysisRow.site_id == site_id)
            rows = s.scalars(q).all()
        return [{"analysis_id": r.id, "site_id": r.site_id, "twin_version": r.twin_version, "status": r.status,
                 "robot_ids": r.robot_ids, "readonly": r.readonly, "created_at": r.created_at} for r in rows]

    def analysis_artifact(self, aid: str, name: str):
        self.analysis_row(aid)
        key = self.analysis_key(aid, name)
        try:
            if name.endswith(".json"):
                return self.store.read_json(key)
            return self.store.read_bytes(key).decode("utf-8")
        except FileNotFoundError as e:
            raise NotFound({"message": f"{name} is not ready for analysis {aid}",
                            "status": self.analysis_status(aid)["status"]}) from e

    def report(self, aid: str) -> dict:
        import re

        md = self.analysis_artifact(aid, "report.md")
        try:
            facts = self.analysis_artifact(aid, "facts.json")
        except NotFound:
            facts = []
        if isinstance(facts, dict):
            facts = facts.get("facts", [])
        try:
            citations = self.analysis_artifact(aid, "citations.json")
        except NotFound:
            citations = []
        by_id = {f.get("fact_id"): f for f in facts if isinstance(f, dict)}
        row = self.analysis_row(aid)
        chips, seen = [], set()
        for fid in re.findall(r"\[(f_[A-Za-z0-9_\-]+)\]", md):
            if fid in seen:
                continue
            seen.add(fid)
            f = by_id.get(fid)
            origin = (f or {}).get("origin") or {}
            chips.append({"fact_id": fid, "known": f is not None, "label": (f or {}).get("label"),
                          "value": (f or {}).get("value"), "unit": (f or {}).get("unit"), "origin": origin,
                          "link": self._chip_link(aid, row, origin)})
        doc = self.analysis_doc(aid)
        return {"analysis_id": aid, "status": self.analysis_status(aid)["status"], "markdown": md, "facts": facts,
                "citations": citations, "chips": chips, "verdicts": doc.get("verdicts") or []}

    @staticmethod
    def _chip_link(aid: str, row: AnalysisRow, origin: dict) -> Optional[str]:
        t, ref = origin.get("type"), str(origin.get("ref") or "")
        if t == "sim_batch" and ref:
            return f"/sim/batches/{ref}/episodes/0"
        if t == "source_url" and ref.startswith(("http://", "https://")):
            return ref
        if t == "measurement":
            return f"/sites/{row.site_id}/twin?version={row.twin_version}"
        if t == "investigation":
            return f"/analyses/{aid}/investigation"
        return None

    # ------------------------------------------------------------------ batches / episodes
    def _index_batches(self, aid: str) -> None:
        for key in self.store.list_keys(f"analyses/{aid}/batches"):
            name = key.rsplit("/", 1)[-1]
            if name.endswith(".json") and not name.endswith(".episodes.json"):
                self._batch_index[name[:-5]] = key

    def batch_key(self, batch_id: str) -> str:
        try:
            safe_id(batch_id, "batch id")
        except BadKey as e:
            raise NotFound(str(e)) from e
        key = self._batch_index.get(batch_id)
        if key and self.store.exists(key):
            return key
        root = self.store.root.resolve()
        for p in (root / "analyses").glob(f"*/batches/{batch_id}.json"):
            key = p.relative_to(root).as_posix()
            self._batch_index[batch_id] = key
            return key
        raise NotFound(f"batch {batch_id} not found")

    def get_batch(self, batch_id: str) -> dict:
        return self.store.read_json(self.batch_key(batch_id))

    def bottleneck_path(self, batch_id: str) -> Path:
        """The batch's Pinch Map grid (.npy float32, same shape as the twin occupancy grid)."""
        key = self.batch_key(batch_id)
        doc = self.store.read_json(key)
        uri = doc.get("bottleneck_uri") if isinstance(doc, dict) else None
        try:
            uri = safe_key(uri) if uri else None
        except BadKey:
            uri = None
        if not uri or not uri.startswith("analyses/") or not uri.endswith(".npy"):
            uri = key[:-5] + ".bottleneck.npy"
        try:
            return self.store.ensure_local(uri)
        except FileNotFoundError as e:
            raise NotFound(f"batch {batch_id} has no Pinch Map grid") from e

    def episode(self, batch_id: str, n: int) -> dict:
        key = self.batch_key(batch_id)
        doc = self.store.read_json(key)
        ep_key = doc.get("episodes_uri") if isinstance(doc, dict) else None
        try:
            ep_key = safe_key(ep_key) if ep_key else None
        except BadKey:
            ep_key = None
        if not ep_key or not self.store.exists(ep_key):
            ep_key = key[:-5] + ".episodes.jsonl"
        try:
            path = self.store.ensure_local(ep_key)
        except FileNotFoundError as e:
            raise NotFound(f"batch {batch_id} has no recorded episodes") from e
        idx = self.episodes.get(path)
        try:
            ep = idx.episode(n)
        except IndexError as e:
            raise NotFound(f"episode {n} out of range (batch has {len(idx)})") from e
        return {"batch_id": batch_id, "robot_id": doc.get("robot_id"), "density": doc.get("density"),
                "geometry_bound": doc.get("geometry_bound"), **ep}

    # ------------------------------------------------------------------ usage
    def job_usage(self) -> dict:
        with self.db.session() as s:
            rows = s.execute(select(JobRow.kind, JobRow.status, func.count(), func.sum(JobRow.duration_s))
                             .group_by(JobRow.kind, JobRow.status)).all()
        out: dict = {}
        for kind, status, n, dur in rows:
            k = out.setdefault(kind, {"count": 0, "total_duration_s": 0.0, "by_status": {}})
            k["count"] += n
            k["total_duration_s"] = round(k["total_duration_s"] + float(dur or 0.0), 3)
            k["by_status"][status] = n
        return out
