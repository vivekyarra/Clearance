"""HTTP + WebSocket routes (docs/CONTRACTS.md). Mounted at / and at /api.

Authorization (services/api/auth.py): GET routes are public. Writes need `Authorization: Bearer <token>`:
the operator token for everything, or the site join token (from the QR) / per-capture device token for the
phone flows (register capture, upload URLs, complete, coach session). Upload PUTs carry their own signed token.
"""
from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import uuid
from typing import Literal, Optional
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, WebSocket
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from services.api.auth import Principal, bearer_from_headers
from services.api.core import schemas
from services.api.db import LiveSessionRow, new_id
from services.api.lazy import require
from services.api.live import LiveState
from services.api.marker import marker_pdf, qr_png
from services.api.security import BadKey, TokenError, safe_id, safe_key
from services.api.service import App, Invalid, NotFound, default_task

router = APIRouter()

SiteType = Literal["cafe", "restaurant", "storeroom", "warehouse", "corridor", "other"]


def ctx(request: Request) -> App:
    return request.app.state.clearance


def api_base(request: Request) -> str:
    return ctx(request).settings.public_api_base or str(request.base_url).rstrip("/")


def web_base(request: Request) -> str:
    return ctx(request).settings.public_web_base or api_base(request)


# ---------------------------------------------------------------- authorization
def principal(request: Request) -> Optional[Principal]:
    return ctx(request).auth.resolve(bearer_from_headers(request.headers))


def _need_token(detail: str) -> HTTPException:
    return HTTPException(401, detail, headers={"WWW-Authenticate": "Bearer"})


def require_operator(request: Request) -> Principal:
    p = principal(request)
    if p is None:
        raise _need_token("operator token required (Authorization: Bearer <CLEARANCE_OPERATOR_TOKEN>)")
    if not p.is_operator:
        raise HTTPException(403, "this route needs the operator token")
    return p


def require_site_access(site_id: str, request: Request) -> Principal:
    """Operator, or the join token of this site (from its QR code)."""
    p = principal(request)
    if p is None:
        raise _need_token("operator token or this site's join token required")
    if p.is_operator or (p.kind == "join" and p.site_id == site_id):
        return p
    raise HTTPException(403, f"token is not valid for site {site_id}")


def require_capture_access(capture_id: str, request: Request) -> Principal:
    """Operator, the capture's device token, or a join token of the capture's site."""
    p = principal(request)
    if p is None:
        raise _need_token("operator token, device token or join token required")
    if p.is_operator or (p.kind == "capture" and p.capture_id == capture_id):
        return p
    if p.kind == "join":
        row = ctx(request).capture_row(capture_id)
        if row.site_id == p.site_id:
            return p
    raise HTTPException(403, f"token is not valid for capture {capture_id}")


def _live_rate_limit(request: Request, p: Principal) -> None:
    """Per client IP budget for opening live sessions (the operator is exempt)."""
    if p.is_operator:
        return
    ip = request.client.host if request.client else "unknown"
    if not ctx(request).live_create_limiter.allow(ip):
        raise HTTPException(429, "too many live session requests from this address; retry in a minute",
                            headers={"Retry-After": "60"})


# ---------------------------------------------------------------- request bodies
class SiteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    type: SiteType = "cafe"
    area_m2: Optional[float] = Field(None, gt=0, le=100_000)
    marker_size_m: float = Field(0.160, ge=0.04, le=0.17)


class CaptureCreate(BaseModel):
    device: schemas.Device
    role: Literal["scanner", "observer"] = "scanner"
    marker: Optional[schemas.Marker] = None
    n_chunks: int = Field(1, ge=0, le=10_000)
    chunking: Literal["independent", "split"] = "independent"
    files: list[str] = Field(default_factory=list, max_length=100_000)
    started_at: str = ""
    duration_s: float = Field(0.0, ge=0)
    privacy: Optional[dict] = None
    capture_files: Optional[schemas.CaptureFiles] = None


class UploadUrlsRequest(BaseModel):
    add_chunks: int = Field(0, ge=0, le=10_000)
    add_files: list[str] = Field(default_factory=list)
    parts: Optional[list[int]] = None
    files: Optional[list[str]] = None


class AnalysisCreate(BaseModel):
    twin_version: Optional[int] = Field(None, ge=1)
    robot_ids: Optional[list[str]] = None
    task: Optional[dict] = None
    business: dict = Field(default_factory=dict)
    budget: dict = Field(default_factory=dict)
    force: bool = False


class VerifyBody(BaseModel):
    verified_by: str = Field(min_length=1, max_length=100)
    edits: dict = Field(default_factory=dict)
    notes: str = Field("", max_length=2000)
    verified: bool = True


class LiveCreate(BaseModel):
    site_id: str
    mode: Literal["chaos", "coach"] = "chaos"
    robot_id: Optional[str] = None
    twin_version: Optional[int] = Field(None, ge=1)
    task: Optional[dict] = None
    density: Literal["none", "quiet", "normal", "peak"] = "normal"
    seed: int = 0


# ---------------------------------------------------------------- health
@router.get("/health")
def health(request: Request):
    app = ctx(request)
    return {"ok": True, "service": "Clearance API", "storage": app.store.backend,
            "dispatcher": app.dispatcher.backend, "database": app.db.engine.dialect.name}


# ---------------------------------------------------------------- sites
def _site_payload(request: Request, row, *, operator: Optional[bool] = None) -> dict:
    """The join token rides in the QR payload only for the operator; public reads get a token-free join URL."""
    app = ctx(request)
    base = api_base(request)
    if operator is None:
        p = principal(request)
        operator = bool(p and p.is_operator)
    qr = app.site_qr(row, base, web_base(request), with_token=operator)
    return {**app.site_dict(row), "qr": qr, "join_url": qr["join_url"],
            "qr_png_url": f"{base}/sites/{row.id}/qr.png", "marker_pdf_url": f"{base}/sites/{row.id}/marker.pdf"}


@router.get("/sites")
def list_sites(request: Request):
    return ctx(request).list_sites()


@router.post("/sites", status_code=201)
def create_site(body: SiteCreate, request: Request, _p: Principal = Depends(require_operator)):
    row = ctx(request).create_site(body.name.strip(), body.type, body.area_m2, body.marker_size_m)
    return _site_payload(request, row, operator=True)


@router.get("/sites/{site_id}")
def get_site(site_id: str, request: Request):
    return _site_payload(request, ctx(request).site_row(site_id))


@router.get("/sites/{site_id}/qr.png")
def site_qr(site_id: str, request: Request):
    """The QR code encodes the join URL (docs/CONTRACTS.md "Site QR code")."""
    app = ctx(request)
    row = app.site_row(site_id)
    p = principal(request)
    payload = app.site_qr(row, api_base(request), web_base(request),
                          with_token=bool(p and p.is_operator))["join_url"]
    try:
        png = qr_png(payload)
    except ImportError as e:
        raise HTTPException(503, f"qrcode package: component unavailable in this deployment ({e})") from e
    return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/sites/{site_id}/marker.pdf")
def site_marker(site_id: str, request: Request):
    row = ctx(request).site_row(site_id)
    m = row.marker or {}
    pdf = marker_pdf(row.id, row.name, size_m=float(m.get("size_m", 0.160)), tag_id=int(m.get("id", 0)))
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="clearance-marker-{row.id}.pdf"'})


# ---------------------------------------------------------------- captures + uploads
@router.post("/sites/{site_id}/captures", status_code=201)
def register_capture(site_id: str, body: CaptureCreate, request: Request,
                     _p: Principal = Depends(require_site_access)):
    data = body.model_dump(mode="json")
    return ctx(request).register_capture(site_id, data, api_base(request))


@router.get("/sites/{site_id}/captures")
def site_captures(site_id: str, request: Request):
    return ctx(request).list_captures(site_id)


@router.get("/captures/{capture_id}")
def capture_status(capture_id: str, request: Request):
    return ctx(request).capture_status(capture_id)


@router.post("/captures/{capture_id}/upload-urls")
def capture_upload_urls(capture_id: str, body: UploadUrlsRequest, request: Request,
                        _p: Principal = Depends(require_capture_access)):
    return ctx(request).more_upload_urls(capture_id, body.model_dump(), api_base(request))


@router.post("/captures/{capture_id}/complete", status_code=202)
def complete_capture(capture_id: str, request: Request, _p: Principal = Depends(require_capture_access)):
    return ctx(request).complete_capture(capture_id)


@router.put("/upload/{token}")
async def upload(token: str, request: Request):
    app = ctx(request)
    try:
        payload = app.signer.verify(token)
        key, cid, max_bytes = payload.get("k"), payload.get("c"), int(payload.get("m", 0))
        safe_key(key)
        safe_id(cid, "capture id")
    except (TokenError, BadKey, TypeError, ValueError) as e:
        raise HTTPException(403, f"upload token rejected: {e}") from e
    if payload.get("t", "up") != "up":
        raise HTTPException(403, "upload token rejected: not an upload token")
    if not key.startswith(f"captures/{cid}/"):
        raise HTTPException(403, "upload token rejected: key outside its capture")
    await run_in_threadpool(app.upload_allowed, cid, key)
    cl = request.headers.get("content-length")
    if cl is not None and cl.isdigit() and int(cl) > max_bytes:
        raise HTTPException(413, f"upload exceeds {max_bytes} bytes")
    dest = app.store.path(key)
    tmp = dest.with_name(f".tmp_{uuid.uuid4().hex}.part")
    sha, md5, size = hashlib.sha256(), hashlib.md5(usedforsecurity=False), 0
    try:
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(413, f"upload exceeds {max_bytes} bytes")
                sha.update(chunk)
                md5.update(chunk)
                f.write(chunk)

        def finalize():
            with app.upload_lock(cid):  # two PUTs racing on one key: only the first one lands
                app.upload_allowed(cid, key)
                os.replace(tmp, dest)

        await run_in_threadpool(finalize)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    return JSONResponse({"key": key, "size": size, "sha256": sha.hexdigest()},
                        headers={"ETag": f'"{md5.hexdigest()}"'})


# ---------------------------------------------------------------- twins
@router.get("/sites/{site_id}/twin")
def get_twin(site_id: str, request: Request, version: Optional[int] = Query(None, ge=1)):
    return ctx(request).get_twin(site_id, version)


@router.put("/sites/{site_id}/twin", status_code=201)
def put_twin(site_id: str, request: Request, body: dict = Body(...), force: bool = False,
             _p: Principal = Depends(require_operator)):
    return ctx(request).save_twin(site_id, body, force=force, confirmed_by=_p.kind)


@router.get("/sites/{site_id}/twins")
def list_twins(site_id: str, request: Request):
    return ctx(request).list_twins(site_id)


@router.get("/sites/{site_id}/twin/occupancy.png")
def twin_occupancy(site_id: str, request: Request, version: Optional[int] = Query(None, ge=1)):
    return FileResponse(ctx(request).occupancy_path(site_id, version), media_type="image/png")


ASSET_NAME = re.compile(r"^[A-Za-z0-9_]{1,40}(\.[A-Za-z0-9]{1,8})?$")
ASSET_TYPES = {".glb": "model/gltf-binary", ".usdz": "model/vnd.usdz+zip", ".ply": "application/octet-stream",
               ".splat": "application/octet-stream", ".ksplat": "application/octet-stream",
               ".pcd": "application/octet-stream"}


@router.get("/sites/{site_id}/twin/assets/{name}")
def twin_asset(site_id: str, name: str, request: Request, version: Optional[int] = Query(None, ge=1)):
    if not ASSET_NAME.match(name):
        raise NotFound(f"invalid asset name {name!r}")
    target, path = ctx(request).twin_asset(site_id, name, version)
    if path is None:
        return RedirectResponse(target, status_code=307)
    ext = path.suffix.lower()
    media = ASSET_TYPES.get(ext) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media, filename=path.name, content_disposition_type="inline")


# ---------------------------------------------------------------- catalog
@router.get("/catalog/robots")
def catalog_list(request: Request, all: str = "0"):  # noqa: A002 - contract uses ?all=1
    return ctx(request).catalog.list(include_all=all.lower() in ("1", "true", "yes"))


@router.get("/catalog/robots/{robot_id}")
def catalog_get(robot_id: str, request: Request):
    e = ctx(request).catalog.get(robot_id)
    if e is None:
        raise NotFound(f"robot {robot_id} not found")
    return e


@router.post("/catalog/robots/{robot_id}/verify")
def catalog_verify(robot_id: str, body: VerifyBody, request: Request, _p: Principal = Depends(require_operator)):
    from services.api.catalog import CatalogError

    try:
        return ctx(request).catalog.verify(robot_id, verified_by=body.verified_by.strip(), edits=body.edits,
                                           notes=body.notes, verified=body.verified)
    except KeyError as e:
        raise NotFound(f"robot {robot_id} not found") from e
    except CatalogError as e:
        raise Invalid(str(e)) from e


# ---------------------------------------------------------------- analyses (Clearance checks)
def _create_analysis(site_id: str, body: AnalysisCreate, request: Request):
    out, created = ctx(request).create_analysis(site_id, body.model_dump())
    return JSONResponse(out, status_code=202 if created else 200)


@router.post("/sites/{site_id}/analyses", status_code=202)
def create_analysis(site_id: str, body: AnalysisCreate, request: Request, _p: Principal = Depends(require_operator)):
    return _create_analysis(site_id, body, request)


@router.post("/sites/{site_id}/clearance-checks", status_code=202)
def create_clearance_check(site_id: str, body: AnalysisCreate, request: Request,
                           _p: Principal = Depends(require_operator)):
    return _create_analysis(site_id, body, request)


@router.get("/sites/{site_id}/analyses")
def site_analyses(site_id: str, request: Request):
    ctx(request).site_row(site_id)
    return ctx(request).list_analyses(site_id)


@router.get("/analyses")
def list_analyses(request: Request, site_id: Optional[str] = None):
    return ctx(request).list_analyses(site_id)


@router.get("/analyses/{analysis_id}")
def get_analysis(analysis_id: str, request: Request):
    return ctx(request).analysis_status(analysis_id)


@router.get("/analyses/{analysis_id}/report")
def get_report(analysis_id: str, request: Request):
    return ctx(request).report(analysis_id)


@router.get("/analyses/{analysis_id}/investigation")
def get_investigation(analysis_id: str, request: Request):
    return ctx(request).analysis_artifact(analysis_id, "investigation.json")


@router.get("/analyses/{analysis_id}/facts")
def get_facts(analysis_id: str, request: Request):
    return ctx(request).analysis_artifact(analysis_id, "facts.json")


# ---------------------------------------------------------------- sim batches
@router.get("/sim/batches/{batch_id}")
def get_batch(batch_id: str, request: Request):
    return ctx(request).get_batch(batch_id)


@router.get("/sim/batches/{batch_id}/bottleneck.npy")
def get_bottleneck(batch_id: str, request: Request):
    return FileResponse(ctx(request).bottleneck_path(batch_id), media_type="application/octet-stream",
                        filename=f"{batch_id}.bottleneck.npy")


@router.get("/sim/batches/{batch_id}/episodes/{n}")
def get_episode(batch_id: str, n: int, request: Request):
    if n < 0:
        raise NotFound("episode index must be >= 0")
    return ctx(request).episode(batch_id, n)


# ---------------------------------------------------------------- usage
@router.get("/usage/summary")
def usage_summary(request: Request):
    summary = require("agents.llm.usage", "summary")()
    jobs = ctx(request).job_usage()
    if isinstance(summary, dict):
        return {**summary, "api_jobs": jobs}
    return {"llm": summary, "api_jobs": jobs}


# ---------------------------------------------------------------- live sessions
@router.post("/live", status_code=201)
def create_live(body: LiveCreate, request: Request):
    """Chaos sessions need the operator token; coach sessions also accept the site's join token."""
    app = ctx(request)
    p = require_operator(request) if body.mode == "chaos" else require_site_access(body.site_id, request)
    site = app.site_row(body.site_id)
    web = web_base(request).rstrip("/")
    if body.mode == "coach":
        # one coach session per site: the scanning phone and every coach screen meet in the same session
        existing = next((s for s in app.live.sessions.values()
                         if s.site_id == site.id and s.mode == "coach" and s.status == "open"), None)
        if existing is not None:
            return JSONResponse(_live_payload(app, existing, None, None, web), status_code=200)
    _live_rate_limit(request, p)
    sid = new_id("live")
    sim, robot_id, version = None, None, None
    if body.mode == "chaos":
        twin = app.twin_model(site.id, body.twin_version)
        version = twin.version
        robot = None
        if body.robot_id:
            robot = app.catalog.spec(body.robot_id)
            if robot is None:
                raise Invalid(f"robot {body.robot_id} is not in the catalog")
            if not robot.verified_by_human:
                raise Invalid(f"robot {body.robot_id} is awaiting review")
        else:
            reviewed = app.catalog.list()
            robot = app.catalog.spec(reviewed[0]["robot_id"]) if reviewed else None
        if robot is None:
            raise Invalid("no reviewed robot available for the live session")
        robot_id = robot.robot_id
        task = schemas.Task.model_validate(body.task) if body.task else default_task(twin)
        live_cls = require("jobs.sim.api", "LiveSession")
        sim = live_cls(twin, robot, task, body.density, body.seed)
    st = LiveState(id=sid, site_id=site.id, mode=body.mode, hz=app.settings.live_hz, sim=sim)
    row = LiveSessionRow(id=sid, site_id=site.id, mode=body.mode, robot_id=robot_id, twin_version=version,
                         params=body.model_dump(mode="json"))
    try:
        app.live.add(st, row)
    except OverflowError as e:
        raise HTTPException(429, str(e)) from e
    return _live_payload(app, st, robot_id, version, web)


def _live_payload(app: App, st: LiveState, robot_id, version, web: str) -> dict:
    """`token` opens the session WebSocket (?token=...); the joystick and coach links carry it."""
    tok = app.auth.live_token(st.id, st.site_id)
    q = quote(tok, safe="")
    return {"session_id": st.id, "site_id": st.site_id, "mode": st.mode, "robot_id": robot_id,
            "twin_version": version, "hz": st.hz, "ws_path": f"/live/{st.id}", "token": tok,
            "joystick_url": f"{web}/joystick/{st.id}?token={q}",
            "coach_url": f"{web}/coach/{st.site_id}?session={st.id}&token={q}"}


@router.get("/live/{session_id}")
def get_live(session_id: str, request: Request):
    st = ctx(request).live.get(session_id)
    if st is None:
        raise NotFound(f"live session {session_id} not found")
    return st.info()


@router.delete("/live/{session_id}")
def close_live(session_id: str, request: Request, _p: Principal = Depends(require_operator)):
    if not ctx(request).live.close(session_id):
        raise NotFound(f"live session {session_id} not found")
    return {"closed": session_id}


@router.get("/sites/{site_id}/live")
def site_live(site_id: str, request: Request):
    app = ctx(request)
    app.site_row(site_id)
    return [s.info() for s in app.live.sessions.values() if s.site_id == site_id]


@router.websocket("/live/{session_id}")
async def live_ws(websocket: WebSocket, session_id: str):
    """Browsers cannot set headers on WebSockets, so the token may also come as ?token=..."""
    token = websocket.query_params.get("token") or bearer_from_headers(websocket.headers)
    await websocket.app.state.clearance.live.handle(websocket, session_id, token)
