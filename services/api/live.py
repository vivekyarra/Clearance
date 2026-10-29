"""Live sessions: chaos mode (jobs.sim.api.LiveSession stepped at 20 Hz) and the capture-coach relay.

WS protocol (/live/{session_id}?role=console|joystick|scanner|coach|viewer):
  server -> client  {"type":"hello",...} once, then {"type":"state", t, robot, peds, events} at `hz`
  client -> server  {"type":"joystick","vx":..,"vy":..}   (speed clamped to 1.4 m/s, PLAN 7.10)
                    {"type":"coach","site_id","coverage":{...}|null,"prompt":str|null,"t":float,"status"?:{...}}
                        validated by normalize_coach, relayed to every other client; the latest one is
                        replayed to clients that join later (docs/CONTRACTS.md "Coach message")
                    {"type":"ping"}                         -> {"type":"pong"}
Stepping runs only while at least one client is connected.

Connection guard (in order, before any message is parsed): Origin must be absent (native apps) or on the
allowlist (CORS origins + this API's own origin); a bearer token (`?token=` or Authorization header) must name
the operator, a join token of the session's site, or the session's own live token; at most `max_clients`
clients per session. Each client gets a token-bucket message budget; messages over MAX_MSG_BYTES are refused
before JSON parsing (and uvicorn runs with --ws-max-size 131072 so bigger frames never reach the app).
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

from fastapi import WebSocket

from services.api.db import Database, LiveSessionRow, now_iso

log = logging.getLogger("clearance.api.live")

MAX_PED_SPEED = 1.4
MAX_MSG_BYTES = 128 * 1024
MAX_COVERAGE_CELLS = 40_000
MAX_DROPPED_MSGS = 200          # a client that keeps flooding past its budget is disconnected
STATUS_KEYS = {"device": str, "frames": int, "tracking": str, "keyframes": int, "covered_frac": float}


def _json_default(o):
    try:
        import numpy as np

        if isinstance(o, np.generic):
            return o.item()
        if isinstance(o, np.ndarray):
            return o.tolist()
    except ImportError:  # pragma: no cover
        pass
    if hasattr(o, "model_dump"):
        return o.model_dump(mode="json")
    return str(o)


def dumps(obj) -> str:
    return json.dumps(obj, default=_json_default, separators=(",", ":"))


@dataclass
class Client:
    ws: WebSocket
    role: str
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send(self, text: str) -> bool:
        try:
            async with self.lock:
                await self.ws.send_text(text)
            return True
        except Exception:  # noqa: BLE001 - disconnected
            return False


@dataclass
class LiveState:
    id: str
    site_id: str
    mode: str
    hz: float
    sim: Any = None
    clients: list = field(default_factory=list)
    task: Optional[asyncio.Task] = None
    sim_lock: threading.Lock = field(default_factory=threading.Lock)
    n_frames: int = 0
    n_events: int = 0
    last_seen: float = field(default_factory=time.monotonic)
    status: str = "open"
    error: Optional[str] = None
    events_path: Any = None
    last_coach: Optional[str] = None
    created: float = field(default_factory=time.monotonic)
    ever_connected: bool = False

    def step(self) -> dict:
        with self.sim_lock:
            return self.sim.step()

    def set_joystick(self, vx: float, vy: float) -> None:
        with self.sim_lock:
            self.sim.set_joystick(vx, vy)

    def info(self) -> dict:
        return {"session_id": self.id, "site_id": self.site_id, "mode": self.mode, "hz": self.hz,
                "status": self.status, "clients": [c.role for c in self.clients], "n_frames": self.n_frames,
                "n_events": self.n_events, "error": self.error}


def clamp_joystick(vx, vy) -> tuple[float, float]:
    vx, vy = _finite(vx, "joystick vx"), _finite(vy, "joystick vy")
    sp = math.hypot(vx, vy)
    if not math.isfinite(sp):  # both near float max: scale down first
        vx, vy = vx / 4.0, vy / 4.0
        sp = math.hypot(vx, vy)
    if sp > MAX_PED_SPEED:
        k = MAX_PED_SPEED / sp  # scale factor first: vx * 1.4 alone can overflow near float max
        vx, vy = vx * k, vy * k
    return vx, vy


def _finite(v, what: str) -> float:
    """A JSON number as a finite float; huge integers (OverflowError), NaN and inf are refused."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"{what} must be a finite number")
    try:
        f = float(v)
    except (OverflowError, ValueError) as e:
        raise ValueError(f"{what} must be a finite number") from e
    if not math.isfinite(f):
        raise ValueError(f"{what} must be a finite number")
    return f


class TokenBucket:
    """`rate` tokens per second up to `burst`; take() spends one."""

    def __init__(self, rate: float, burst: float, now: Optional[float] = None):
        self.rate, self.burst = float(rate), float(burst)
        self.tokens = float(burst)
        self.t = time.monotonic() if now is None else now

    def take(self, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        self.tokens = min(self.burst, self.tokens + max(0.0, now - self.t) * self.rate)
        self.t = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class KeyedRateLimiter:
    """One TokenBucket per key (client IP), bounded in memory (least recently used keys are dropped)."""

    def __init__(self, per_minute: float, burst: Optional[float] = None, max_keys: int = 10_000):
        self.rate = per_minute / 60.0
        self.burst = float(burst if burst is not None else max(1.0, per_minute))
        self.max_keys = max_keys
        self._b: OrderedDict[str, TokenBucket] = OrderedDict()
        self._lock = threading.Lock()

    def allow(self, key: str, now: Optional[float] = None) -> bool:
        with self._lock:
            b = self._b.get(key)
            if b is None:
                b = self._b[key] = TokenBucket(self.rate, self.burst, now)
                while len(self._b) > self.max_keys:
                    self._b.popitem(last=False)
            else:
                self._b.move_to_end(key)
            return b.take(now)


def origin_allowed(origin: Optional[str], host: Optional[str], allowed: list[str]) -> bool:
    """Browsers always send Origin on WebSocket upgrades; native clients (the iOS app) send none."""
    if origin is None:
        return True
    o = origin.strip().rstrip("/")
    if not o or o == "null":
        return False
    if o in {a.rstrip("/") for a in allowed}:
        return True
    try:
        parts = urlsplit(o)
    except ValueError:
        return False
    return bool(host) and parts.scheme in ("http", "https") and parts.netloc.lower() == host.strip().lower()


def normalize_coach(msg: dict, site_id: str) -> dict:
    """Validate a coach message and return it in the fixed wire form."""
    sid = msg.get("site_id", site_id)
    if sid != site_id:
        raise ValueError(f"coach message is for site {sid!r}, this session is for {site_id!r}")
    cov = msg.get("coverage")
    out_cov = None
    if cov is not None:
        if not isinstance(cov, dict):
            raise ValueError("coverage must be an object")
        cell_m = _finite(cov.get("cell_m"), "coverage.cell_m")
        if cell_m <= 0:
            raise ValueError("coverage.cell_m must be > 0")
        origin = cov.get("origin")
        if not isinstance(origin, (list, tuple)) or len(origin) != 2:
            raise ValueError("coverage.origin must be [x, y]")
        w, h = cov.get("width"), cov.get("height")
        if not all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in (w, h)):
            raise ValueError("coverage.width and coverage.height must be positive integers")
        if w * h > MAX_COVERAGE_CELLS:
            raise ValueError("coverage grid is too large")
        cells = cov.get("cells")
        if not isinstance(cells, list) or len(cells) != w * h:
            raise ValueError("coverage.cells must hold width*height values")
        if not all(isinstance(c, int) and not isinstance(c, bool) and 0 <= c <= 255 for c in cells):
            raise ValueError("coverage.cells values must be integers 0..255")
        out_cov = {"cell_m": cell_m, "origin": [_finite(origin[0], "origin x"), _finite(origin[1], "origin y")],
                   "width": w, "height": h, "cells": cells}
    prompt = msg.get("prompt")
    if prompt is not None and not isinstance(prompt, str):
        raise ValueError("prompt must be a string or null")
    t = msg.get("t")
    out = {"type": "coach", "site_id": site_id, "coverage": out_cov,
           "prompt": prompt[:500] if prompt is not None else None,
           "t": _finite(t, "t") if t is not None else time.time()}
    status = msg.get("status")
    if isinstance(status, dict):
        st = {}
        for k, typ in STATUS_KEYS.items():
            v = status.get(k)
            if v is None:
                continue
            if typ is str and isinstance(v, str):
                st[k] = v[:80]
            elif typ in (int, float):
                try:
                    f = _finite(v, k)  # huge ints (OverflowError), NaN and inf are dropped
                except ValueError:
                    continue
                st[k] = int(f) if typ is int else f
        out["status"] = st
    return out


class LiveManager:
    def __init__(self, db: Database, store, *, hz: float = 20.0, max_sessions: int = 16, idle_ttl_s: float = 600,
                 unclaimed_ttl_s: float = 120, max_per_site: int = 4, max_clients: int = 8,
                 msg_rate: float = 30.0, msg_burst: float = 60.0, allowed_origins: Optional[list[str]] = None,
                 authorize: Optional[Callable[[Optional[str], "LiveState"], bool]] = None):
        self.db = db
        self.store = store
        self.hz = hz
        self.max_sessions = max_sessions
        self.idle_ttl_s = idle_ttl_s
        self.unclaimed_ttl_s = unclaimed_ttl_s
        self.max_per_site = max_per_site
        self.max_clients = max_clients
        self.msg_rate, self.msg_burst = msg_rate, msg_burst
        self.allowed_origins = list(allowed_origins or [])
        self.authorize = authorize
        self.sessions: dict[str, LiveState] = {}
        self._lock = threading.Lock()

    def _stale(self, s: LiveState, now: float) -> bool:
        if s.clients:
            return False
        if not s.ever_connected and now - s.created > self.unclaimed_ttl_s:
            return True
        return now - s.last_seen > self.idle_ttl_s

    def prune(self) -> None:
        now = time.monotonic()
        with self._lock:
            stale = [s for s in self.sessions.values() if self._stale(s, now)]
        for s in stale:
            self.close(s.id)

    def add(self, state: LiveState, row: LiveSessionRow) -> LiveState:
        self.prune()
        with self._lock:
            if len(self.sessions) >= self.max_sessions:
                raise OverflowError("too many live sessions; close one first")
            if sum(1 for s in self.sessions.values() if s.site_id == state.site_id) >= self.max_per_site:
                raise OverflowError(f"site {state.site_id} already has {self.max_per_site} live sessions; "
                                    "close one first")
            self.sessions[state.id] = state
        state.events_path = self.store.path(f"live/{state.id}/events.jsonl")
        with self.db.session() as s:
            s.add(row)
        return state

    def get(self, sid: str) -> Optional[LiveState]:
        return self.sessions.get(sid)

    def close(self, sid: str) -> bool:
        with self._lock:
            st = self.sessions.pop(sid, None)
        if st is None:
            return False
        if st.task is not None:
            st.task.cancel()
        self._persist(st, closed=True)
        return True

    def _persist(self, st: LiveState, closed: bool = False) -> None:
        try:
            with self.db.session() as s:
                row = s.get(LiveSessionRow, st.id)
                if row is not None:
                    row.n_frames, row.n_events = st.n_frames, st.n_events
                    row.status = "closed" if closed and st.status == "open" else st.status
                    if closed:
                        row.closed_at = now_iso()
        except Exception:  # noqa: BLE001
            log.exception("could not persist live session %s", st.id)

    async def broadcast(self, st: LiveState, text: str, *, exclude: Optional[Client] = None) -> None:
        dead = []
        for c in list(st.clients):
            if c is exclude:
                continue
            if not await c.send(text):
                dead.append(c)
        for c in dead:
            if c in st.clients:
                st.clients.remove(c)

    def _record_events(self, st: LiveState, frame: dict) -> None:
        evs = frame.get("events") or []
        if not evs:
            return
        st.n_events += len(evs)
        with open(st.events_path, "a", encoding="utf-8") as f:
            for ev in evs:
                f.write(dumps({"t": frame.get("t"), "event": ev, "label": "manual stress test"}) + "\n")

    async def run_loop(self, st: LiveState) -> None:
        period = 1.0 / st.hz
        loop = asyncio.get_running_loop()
        next_t = loop.time()
        try:
            while st.clients and st.sim is not None:
                try:
                    frame = await asyncio.to_thread(st.step)
                except Exception as e:  # noqa: BLE001
                    st.status, st.error = "error", f"{type(e).__name__}: {e}"
                    await self.broadcast(st, dumps({"type": "error", "detail": st.error}))
                    break
                frame = dict(frame or {})
                st.n_frames += 1
                self._record_events(st, frame)
                await self.broadcast(st, dumps({"type": "state", "seq": st.n_frames, **frame}))
                next_t += period
                delay = next_t - loop.time()
                if delay < -period:  # fell behind: resynchronise rather than burst
                    next_t = loop.time()
                    delay = 0
                await asyncio.sleep(max(0.0, delay))
        finally:
            st.task = None
            self._persist(st)

    def ensure_loop(self, st: LiveState) -> None:
        if st.sim is not None and st.task is None and st.status == "open":
            st.task = asyncio.get_running_loop().create_task(self.run_loop(st))

    async def _refuse(self, ws: WebSocket, code: int, detail: str) -> None:
        await ws.accept()
        await ws.send_text(dumps({"type": "error", "detail": detail}))
        await ws.close(code=code)

    async def handle(self, ws: WebSocket, sid: str, token: Optional[str] = None) -> None:
        if not origin_allowed(ws.headers.get("origin"), ws.headers.get("host"), self.allowed_origins):
            await ws.close(code=4403)  # before accept: the handshake fails with HTTP 403
            return
        st = self.get(sid)
        if st is None:
            await self._refuse(ws, 4404, f"unknown live session {sid}")
            return
        if self.authorize is not None and not self.authorize(token, st):
            await self._refuse(ws, 4401, "a valid token for this site or session is required")
            return
        if len(st.clients) >= self.max_clients:
            await self._refuse(ws, 4429, f"live session {sid} already has {self.max_clients} clients")
            return
        await ws.accept()
        role = (ws.query_params.get("role") or "viewer")[:32]
        client = Client(ws, role)
        st.clients.append(client)
        st.ever_connected = True
        st.last_seen = time.monotonic()
        bucket = TokenBucket(self.msg_rate, self.msg_burst)
        dropped = 0
        await client.send(dumps({"type": "hello", **st.info()}))
        if st.last_coach is not None:
            await client.send(st.last_coach)
        self.ensure_loop(st)
        try:
            while True:
                text = await ws.receive_text()
                st.last_seen = time.monotonic()
                if not bucket.take():
                    dropped += 1
                    if dropped >= MAX_DROPPED_MSGS:
                        await client.send(dumps({"type": "error", "detail": "message rate limit exceeded"}))
                        await ws.close(code=4408)
                        break
                    if dropped == 1 or dropped % 50 == 0:
                        await client.send(dumps({"type": "error", "detail": "message rate limit exceeded"}))
                    continue
                if len(text) > MAX_MSG_BYTES or len(text.encode("utf-8")) > MAX_MSG_BYTES:
                    await client.send(dumps({"type": "error", "detail": "message too large"}))
                    continue
                try:
                    msg = json.loads(text)
                    if not isinstance(msg, dict):
                        raise ValueError
                except (ValueError, RecursionError):
                    await client.send(dumps({"type": "error", "detail": "messages must be JSON objects"}))
                    continue
                kind = msg.get("type")
                if kind == "joystick":
                    if st.sim is None:
                        await client.send(dumps({"type": "error", "detail": "this session has no simulation"}))
                        continue
                    try:
                        vx, vy = clamp_joystick(msg.get("vx", 0.0), msg.get("vy", 0.0))
                    except (TypeError, ValueError, OverflowError) as e:
                        await client.send(dumps({"type": "error", "detail": str(e)}))
                        continue
                    await asyncio.to_thread(st.set_joystick, vx, vy)
                elif kind == "coach":
                    try:
                        text_out = dumps(normalize_coach(msg, st.site_id))
                    except (TypeError, ValueError, OverflowError) as e:
                        await client.send(dumps({"type": "error", "detail": str(e)}))
                        continue
                    st.last_coach = text_out
                    await self.broadcast(st, text_out, exclude=client)
                elif kind == "ping":
                    await client.send(dumps({"type": "pong", "t": time.time()}))
                else:
                    await client.send(dumps({"type": "error", "detail": f"unknown message type {kind!r}"}))
        except Exception:  # noqa: BLE001 - WebSocketDisconnect or transport error
            pass
        finally:
            if client in st.clients:
                st.clients.remove(client)
            st.last_seen = time.monotonic()
            if not st.clients and st.sim is not None:
                # The server may cancel this task as the socket closes; shield the release so the robot always stops.
                release = asyncio.ensure_future(asyncio.to_thread(st.set_joystick, 0.0, 0.0))
                try:
                    await asyncio.shield(release)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    pass

    async def shutdown(self) -> None:
        for sid in list(self.sessions):
            self.close(sid)
