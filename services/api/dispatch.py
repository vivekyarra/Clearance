"""Job dispatch (J1/J2 reconstruction, analyses).

Jobs are content-addressed: job id = kind + sha256 of the inputs, so re-submitting identical inputs returns the
existing (queued / running / succeeded) job instead of running it again. A failed job re-runs on re-submit.

LocalDispatcher  - thread pool (default) or process pool in this API process.
NebiusDispatcher - prepares Nebius Serverless Job submissions from infra job definitions
                   (infra/jobs/<kind>.yaml|.yml|.json). The CLI syntax and status API are VERIFY items: the
                   definition file carries the exact submit/status command lines, so no CLI syntax is
                   hard-coded here. Live submission runs only with CLEARANCE_NEBIUS_SUBMIT=1.
"""
from __future__ import annotations

import abc
import importlib
import json
import logging
import os
import shutil
import subprocess
import threading
import time
import traceback
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Optional

from sqlalchemy.exc import IntegrityError

from services.api.db import Database, JobRow, now_iso

log = logging.getLogger("clearance.api.dispatch")

OnSuccess = Callable[[str, Any], None]
OnError = Callable[[str, BaseException], None]


class DispatchUnavailable(RuntimeError):
    """The selected backend cannot run this job right now (message is user-facing)."""


def resolve_callable(spec: str) -> Callable:
    mod, _, attr = spec.partition(":")
    if not mod or not attr:
        raise ValueError(f"callable spec must be 'module:attr', got {spec!r}")
    return getattr(importlib.import_module(mod), attr)


def invoke(spec: str, kwargs: dict):
    return resolve_callable(spec)(**kwargs)


def _json_safe(obj):
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump(mode="json", by_alias=True)
    return json.loads(json.dumps(obj, default=str))


def _log_job_usage(kind: str, job_id: str, duration_s: float, status: str, backend: str = "local") -> None:
    """Best effort: jobs appear in the usage table next to model calls."""
    try:
        usage = importlib.import_module("agents.llm.usage")
        usage.log_usage(f"job:{kind}", f"compute:{backend}", 0, 0, duration_s, 0.0, {"job_id": job_id, "status": status})
    except Exception:  # noqa: BLE001 - usage logging must never fail a job
        pass


class Dispatcher(abc.ABC):
    backend = "base"

    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def job_id_for(kind: str, input_hash: str) -> str:
        return f"job_{kind}_{input_hash[:24]}"

    def claim(self, kind: str, input_hash: str) -> tuple[str, bool]:
        """Return (job_id, should_run). Existing live/succeeded jobs are reused; failed ones are reset."""
        jid = self.job_id_for(kind, input_hash)
        try:
            with self.db.session() as s:
                row = s.get(JobRow, jid)
                if row is not None:
                    if row.status in ("queued", "running", "succeeded", "submitted"):
                        return jid, False
                    row.status, row.error, row.result = "queued", None, None
                    row.started_at = row.finished_at = None
                    row.backend = self.backend
                    return jid, True
                s.add(JobRow(id=jid, kind=kind, input_hash=input_hash, status="queued", backend=self.backend))
            return jid, True
        except IntegrityError:
            return jid, False

    def get(self, job_id: str) -> Optional[dict]:
        with self.db.session() as s:
            r = s.get(JobRow, job_id)
            if r is None:
                return None
            return {"job_id": r.id, "kind": r.kind, "input_hash": r.input_hash, "status": r.status,
                    "backend": r.backend, "result": r.result, "error": r.error, "attempts": r.attempts,
                    "created_at": r.created_at, "started_at": r.started_at, "finished_at": r.finished_at,
                    "duration_s": r.duration_s}

    def _mark_running(self, job_id: str) -> float:
        with self.db.session() as s:
            r = s.get(JobRow, job_id)
            r.status, r.started_at = "running", now_iso()
            r.attempts = (r.attempts or 0) + 1
        return time.monotonic()

    def _mark(self, job_id: str, status: str, t0: float, *, result=None, error: Optional[str] = None) -> None:
        dur = round(time.monotonic() - t0, 4)
        with self.db.session() as s:
            r = s.get(JobRow, job_id)
            r.status, r.finished_at, r.duration_s = status, now_iso(), dur
            r.result, r.error = _json_safe(result), error
            kind = r.kind
        _log_job_usage(kind, job_id, dur, status, self.backend)

    def recover_interrupted(self) -> int:
        """Jobs left queued/running by a previous process are marked failed so a re-submit re-runs them."""
        n = 0
        with self.db.session() as s:
            for r in s.query(JobRow).filter(JobRow.status.in_(["queued", "running"]), JobRow.backend == "local"):
                r.status, r.error, r.finished_at = "failed", "interrupted by API restart", now_iso()
                n += 1
        return n

    @abc.abstractmethod
    def submit(self, kind: str, fn: str, kwargs: dict, *, input_hash: str,
               on_success: Optional[OnSuccess] = None, on_error: Optional[OnError] = None) -> tuple[str, bool]:
        """Run `fn` ('module:attr') with kwargs. Returns (job_id, newly_started)."""

    def wait(self, job_id: str, timeout: Optional[float] = None) -> Optional[dict]:
        return self.get(job_id)

    def shutdown(self, wait: bool = False) -> None:
        return None


class LocalDispatcher(Dispatcher):
    backend = "local"

    def __init__(self, db: Database, max_workers: int = 4, mode: str = "thread"):
        super().__init__(db)
        if mode not in ("thread", "process"):
            raise ValueError("mode must be 'thread' or 'process'")
        self.mode = mode
        self.max_workers = max(1, int(max_workers))
        self._exec = (ThreadPoolExecutor(self.max_workers, thread_name_prefix="clearance-job") if mode == "thread"
                      else ProcessPoolExecutor(self.max_workers))
        self._futures: dict[str, Future] = {}
        self._lock = threading.Lock()

    def submit(self, kind, fn, kwargs, *, input_hash, on_success=None, on_error=None):
        jid, run = self.claim(kind, input_hash)
        if not run:
            return jid, False
        if self.mode == "thread":
            fut = self._exec.submit(self._run_inline, jid, fn, kwargs, on_success, on_error)
        else:
            t0 = self._mark_running(jid)
            fut = self._exec.submit(invoke, fn, kwargs)
            fut.add_done_callback(lambda f: self._finish_future(jid, f, t0, on_success, on_error))
        with self._lock:
            self._futures[jid] = fut
        return jid, True

    def _run_inline(self, jid, fn, kwargs, on_success, on_error):
        t0 = self._mark_running(jid)
        try:
            result = invoke(fn, kwargs)
        except BaseException as e:  # noqa: BLE001
            self._fail(jid, t0, e, on_error)
            return
        self._succeed(jid, t0, result, on_success, on_error)

    def _finish_future(self, jid, fut: Future, t0, on_success, on_error):
        exc = fut.exception()
        if exc is not None:
            self._fail(jid, t0, exc, on_error)
        else:
            self._succeed(jid, t0, fut.result(), on_success, on_error)

    def _succeed(self, jid, t0, result, on_success, on_error):
        try:
            if on_success:
                on_success(jid, result)
        except BaseException as e:  # noqa: BLE001 - finalization failure fails the job
            self._fail(jid, t0, e, on_error)
            return
        self._mark(jid, "succeeded", t0, result=result)

    def _fail(self, jid, t0, exc: BaseException, on_error):
        msg = f"{type(exc).__name__}: {exc}"
        log.warning("job %s failed: %s\n%s", jid, msg, "".join(traceback.format_exception(exc)))
        try:
            self._mark(jid, "failed", t0, error=msg)
        finally:
            if on_error:
                try:
                    on_error(jid, exc)
                except Exception:  # noqa: BLE001
                    log.exception("on_error callback failed for %s", jid)

    def wait(self, job_id, timeout=None):
        with self._lock:
            fut = self._futures.get(job_id)
        if fut is not None:
            fut.result(timeout=timeout)
            if self.mode == "process":  # done-callback may still be running
                deadline = time.monotonic() + (timeout or 30)
                while time.monotonic() < deadline and (self.get(job_id) or {}).get("status") == "running":
                    time.sleep(0.01)
        return self.get(job_id)

    def shutdown(self, wait: bool = False):
        self._exec.shutdown(wait=wait, cancel_futures=not wait)


class NebiusDispatcher(Dispatcher):
    """Nebius Serverless Jobs backend.

    Definition file (infra/jobs/<kind>.yaml), all command lines are VERIFY against current Nebius docs:
        image: <registry>/clearance-jobs:<tag>
        resources: {platform: ..., preset: ...}
        env: {KEY: value}
        submit_command: ["nebius", "...", "--name", "{job_id}", "--image", "{image}", "--env", "CLEARANCE_JOB_SPEC={spec_json}"]
        status_command: ["nebius", "...", "{job_id}"]   # optional; must print JSON with a "status" field
    The container entrypoint is `python -m services.api.tasks`, which reads CLEARANCE_JOB_SPEC.
    """

    backend = "nebius"

    def __init__(self, db: Database, infra_dir: Path, *, submit_enabled: Optional[bool] = None,
                 runner: Callable[..., subprocess.CompletedProcess] = subprocess.run):
        super().__init__(db)
        self.infra_dir = Path(infra_dir)
        self.submit_enabled = (os.environ.get("CLEARANCE_NEBIUS_SUBMIT") == "1") if submit_enabled is None else submit_enabled
        self.runner = runner
        self._callbacks: dict[str, tuple[Optional[OnSuccess], Optional[OnError]]] = {}

    def definition(self, kind: str) -> dict:
        base = self.infra_dir / "jobs"
        for ext in (".yaml", ".yml", ".json"):
            p = base / f"{kind}{ext}"
            if p.is_file():
                text = p.read_text(encoding="utf-8")
                if ext == ".json":
                    return json.loads(text)
                import yaml

                return yaml.safe_load(text) or {}
        raise DispatchUnavailable(f"no Nebius job definition for '{kind}' in {base} (expected {kind}.yaml)")

    def build_submission(self, job_id: str, kind: str, fn: str, kwargs: dict) -> dict:
        d = self.definition(kind)
        spec = {"job_id": job_id, "kind": kind, "entrypoint": fn, "kwargs": _json_safe(kwargs)}
        spec_json = json.dumps(spec, separators=(",", ":"))
        fields = {"job_id": job_id, "image": d.get("image", ""), "spec_json": spec_json, "kind": kind}
        cmd = d.get("submit_command")
        rendered = [str(a).format(**fields) for a in cmd] if cmd else None
        return {"definition": d, "spec": spec, "env": {**d.get("env", {}), "CLEARANCE_JOB_SPEC": spec_json},
                "command": rendered}

    def submit(self, kind, fn, kwargs, *, input_hash, on_success=None, on_error=None):
        jid = self.job_id_for(kind, input_hash)
        sub = self.build_submission(jid, kind, fn, kwargs)
        if not sub["command"]:
            raise DispatchUnavailable(f"job definition for '{kind}' has no submit_command (VERIFY Nebius CLI syntax)")
        if not self.submit_enabled:
            raise DispatchUnavailable("Nebius submission is switched off; set CLEARANCE_NEBIUS_SUBMIT=1 to enable")
        if shutil.which(sub["command"][0]) is None and self.runner is subprocess.run:
            raise DispatchUnavailable(f"'{sub['command'][0]}' CLI is not on PATH")
        jid, run = self.claim(kind, input_hash)
        if not run:
            return jid, False
        t0 = self._mark_running(jid)
        proc = self.runner(sub["command"], capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            self._mark(jid, "failed", t0, error=(proc.stderr or proc.stdout or "submit failed")[-2000:])
            raise DispatchUnavailable(f"Nebius job submission failed: {(proc.stderr or '').strip()[-300:]}")
        with self.db.session() as s:
            s.get(JobRow, jid).status = "submitted"
        self._callbacks[jid] = (on_success, on_error)
        return jid, True

    def refresh(self, job_id: str, load_result: Callable[[str], Any], *, on_success: Optional[OnSuccess] = None,
                on_error: Optional[OnError] = None) -> Optional[dict]:
        """Poll the job via the definition's status_command; on success load the result and run callbacks
        (the ones given at submit, else the ones passed here, e.g. after an API restart)."""
        job = self.get(job_id)
        if not job or job["status"] != "submitted":
            return job
        d = self.definition(job["kind"])
        cmd = d.get("status_command")
        if not cmd:
            return job
        proc = self.runner([str(a).format(job_id=job_id) for a in cmd], capture_output=True, text=True, timeout=60)
        try:
            status = str(json.loads(proc.stdout).get("status", "")).lower()
        except (ValueError, AttributeError):
            return job
        ok_cb, err_cb = self._callbacks.pop(job_id, (on_success, on_error))
        if status in ("succeeded", "completed", "success"):
            result = load_result(job_id)
            if ok_cb:
                ok_cb(job_id, result)
            self._mark(job_id, "succeeded", time.monotonic(), result=result)
        elif status in ("failed", "error", "cancelled"):
            self._mark(job_id, "failed", time.monotonic(), error=f"remote status {status}")
            if err_cb:
                err_cb(job_id, RuntimeError(f"remote status {status}"))
        else:
            self._callbacks[job_id] = (ok_cb, err_cb)
        return self.get(job_id)


def make_dispatcher(settings, db: Database) -> Dispatcher:
    if settings.dispatcher == "nebius":
        return NebiusDispatcher(db, settings.infra_dir)
    return LocalDispatcher(db, settings.job_workers, settings.job_mode)
