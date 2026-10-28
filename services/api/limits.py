"""Request body size limits per route (pure ASGI, so oversized bodies are refused while streaming)."""
from __future__ import annotations

import json
import re
from typing import Callable, Optional

from fastapi import HTTPException

KB, MB = 1 << 10, 1 << 20
DEFAULT_LIMIT = 1 * MB
ROUTE_LIMITS: list[tuple[str, re.Pattern, int]] = [
    ("PUT", re.compile(r"^/sites/[^/]+/twin/?$"), 8 * MB),
    ("POST", re.compile(r"^/sites/[^/]+/captures/?$"), 4 * MB),
    ("POST", re.compile(r"^/captures/[^/]+/upload-urls/?$"), 4 * MB),
    ("POST", re.compile(r"^/captures/[^/]+/complete/?$"), 1 * MB),
]
UNLIMITED = re.compile(r"^/upload/[^/]+$")  # signed upload PUTs enforce their own per-token max_bytes


def body_limit(method: str, path: str) -> Optional[int]:
    """Max request body bytes for (method, path); None = left to the route."""
    if path.startswith("/api/"):
        path = path[4:]
    if method == "PUT" and UNLIMITED.match(path):
        return None
    for m, pat, limit in ROUTE_LIMITS:
        if m == method and pat.match(path):
            return limit
    return DEFAULT_LIMIT


class BodyTooLarge(HTTPException):
    def __init__(self, limit: int):
        super().__init__(413, f"request body exceeds {limit} bytes")


class BodyLimitMiddleware:
    def __init__(self, app, limit_for: Callable[[str, str], Optional[int]] = body_limit):
        self.app = app
        self.limit_for = limit_for

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit = self.limit_for(scope.get("method", "GET"), scope.get("path", ""))
        if limit is None:
            return await self.app(scope, receive, send)
        for k, v in scope.get("headers") or []:
            if k == b"content-length":
                try:
                    n = int(v)
                except ValueError:
                    return await _reply(send, 400, "invalid Content-Length")
                if n > limit:
                    return await _reply(send, 413, f"request body exceeds {limit} bytes")
        seen = 0
        started = False

        async def limited_receive():
            nonlocal seen
            msg = await receive()
            if msg.get("type") == "http.request":
                seen += len(msg.get("body") or b"")
                if seen > limit:
                    raise BodyTooLarge(limit)
            return msg

        async def tracking_send(msg):
            nonlocal started
            if msg.get("type") == "http.response.start":
                started = True
            await send(msg)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except BodyTooLarge:
            if started:
                raise
            await _reply(send, 413, f"request body exceeds {limit} bytes")


async def _reply(send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})
