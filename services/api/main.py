"""Clearance API. Run: uvicorn services.api.main:app --port 8000 --ws-max-size 131072

Write routes need the operator token (CLEARANCE_OPERATOR_TOKEN; generated into <data>/.operator_token and
printed once when unset). Hosted deployments set CLEARANCE_DEPLOYED=1, which refuses to start without the
operator token and without CLEARANCE_PUBLIC_API_BASE or CLEARANCE_ALLOWED_HOSTS.
"""
from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse

from services.api.dispatch import DispatchUnavailable
from services.api.lazy import ComponentUnavailable
from services.api.limits import BodyLimitMiddleware
from services.api.routes import router
from services.api.service import ApiError, App
from services.api.settings import Settings

log = logging.getLogger("clearance.api")


def _mount_web(app: FastAPI, dist: Path) -> None:
    """Serve the built web console (apps/web/dist) with an index.html fallback for client-side routes."""
    root = dist.resolve()
    index = root / "index.html"

    @app.get("/", include_in_schema=False)
    def web_index():
        return FileResponse(index)

    # Browser navigations to console pages (/sites/<id>, /analyses/<id>, ...) share paths with API routes. A GET that
    # asks for HTML and is not a file or an /api, /upload or WebSocket path gets the web app; API clients
    # (Accept: application/json, the iOS app, fetch from the console via /api) keep the JSON routes.
    @app.middleware("http")
    async def web_navigation(request: Request, call_next):
        path = request.url.path
        accept = request.headers.get("accept", "")
        if (request.method == "GET" and "text/html" in accept and "application/json" not in accept
                and "upgrade" not in request.headers and not path.startswith(("/api/", "/upload/"))
                and "." not in path.rsplit("/", 1)[-1]):
            return FileResponse(index)
        return await call_next(request)

    @app.get("/{path:path}", include_in_schema=False)
    def web_files(path: str):
        target = (root / path).resolve()
        if (target == root or root in target.parents) and target.is_file():
            return FileResponse(target)
        if path.startswith(("api/", "assets/")) or "." in path.rsplit("/", 1)[-1]:
            raise HTTPException(404, "not found")
        return FileResponse(index)


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.check_deployment()

    @contextlib.asynccontextmanager
    async def lifespan(api: FastAPI):
        core = App(settings)
        core.start()
        api.state.clearance = core
        try:
            yield
        finally:
            await core.live.shutdown()
            core.stop()

    api = FastAPI(title="Clearance API", version="1.2.0", lifespan=lifespan,
                  description="Sites, captures, twins, robot catalog, Clearance checks, replays and live sessions.")
    api.add_middleware(BodyLimitMiddleware)
    api.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"], expose_headers=["ETag"])
    if settings.allowed_hosts:  # upload URLs fall back to the Host header without CLEARANCE_PUBLIC_API_BASE
        api.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)

    @api.exception_handler(ApiError)
    async def _api_error(_req: Request, exc: ApiError):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status)

    @api.exception_handler(ComponentUnavailable)
    async def _unavailable(_req: Request, exc: ComponentUnavailable):
        return JSONResponse({"detail": str(exc), "component": exc.module}, status_code=503)

    @api.exception_handler(DispatchUnavailable)
    async def _dispatch_unavailable(_req: Request, exc: DispatchUnavailable):
        return JSONResponse({"detail": str(exc)}, status_code=503)

    api.include_router(router)
    api.include_router(router, prefix="/api", include_in_schema=False)
    if (Path(settings.web_dist) / "index.html").is_file():
        _mount_web(api, Path(settings.web_dist))
    return api


app = create_app()
