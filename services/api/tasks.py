"""Job entry points run by the dispatcher (in-process, process pool, or a Nebius job container).

Each function takes JSON-serialisable kwargs and returns a JSON-serialisable dict. Other modules are imported
when the job runs. Container entrypoint: `python -m services.api.tasks` (or infra/nebius/entry.py task) with
the job spec {"job_id", "entrypoint", "kwargs"} in one of (first match wins):
    CLEARANCE_JOB_SPEC       raw JSON (local runs, docker compose)
    CLEARANCE_JOB_SPEC_B64   base64 JSON (Nebius: avoids commas/quotes in `--env KEY=VALUE`)
    CLEARANCE_JOB_SPEC_KEY   storage key under CLEARANCE_DATA (large specs; the bucket is mounted at /data)
"""
from __future__ import annotations

import base64
import hashlib
import importlib
import json
import os
import sys
from pathlib import Path
from typing import Mapping, Optional

RECON_VERSION = "recon-api-1"
ANALYSIS_VERSION = "analysis-api-1"


def hash_json(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def hash_tree(root: Path, *, extra: Optional[dict] = None, exclude: tuple[str, ...] = ()) -> str:
    """Content hash of a directory: sorted relative paths + per-file sha256 (+ extra parameters)."""
    root = Path(root)
    h = hashlib.sha256()
    for p in sorted(x for x in root.rglob("*") if x.is_file()):
        rel = p.relative_to(root).as_posix()
        if rel in exclude:
            continue
        fh = hashlib.sha256()
        with p.open("rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                fh.update(block)
        h.update(rel.encode() + b"\0" + fh.digest())
    if extra:
        h.update(hash_json(extra).encode())
    return h.hexdigest()


def _dump(obj) -> dict:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json", by_alias=True)
    return json.loads(json.dumps(obj, default=str))


def _use_data_root(data_root: Optional[str]) -> Path:
    """Worker processes / job containers: align clearance_core.storage with the API's data root."""
    from clearance_core import storage

    if data_root:
        storage.DATA = Path(data_root)
    return Path(storage.DATA)


def run_recon(capture_dir: str, site_id: str, out_prefix: str, tier: Optional[str] = None,
              data_root: Optional[str] = None) -> dict:
    """J1+J2: capture directory (absolute, or a storage key under the data root) -> Twin under out_prefix."""
    root = _use_data_root(data_root)
    cdir = Path(capture_dir)
    if not cdir.is_absolute():
        cdir = root / cdir
    pipeline = importlib.import_module("jobs.recon.pipeline")
    twin = pipeline.build_twin_from_capture(str(cdir), site_id, out_prefix, tier=tier)
    return _dump(twin)


def run_analysis_job(analysis_id: str, twin: dict, robots: list[dict], task: dict, business: dict,
                     budget: dict, data_root: Optional[str] = None) -> dict:
    from clearance_core.schemas import RobotSpec, Task, Twin

    _use_data_root(data_root)

    orch = importlib.import_module("agents.orchestrator.run")
    out = orch.run_analysis(analysis_id, Twin.model_validate(twin), [RobotSpec.model_validate(r) for r in robots],
                            Task.model_validate(task), business, budget=budget)
    return _dump(out) if out is not None else {}


class SpecError(ValueError):
    """The job spec is missing or malformed (message is shown in the job log)."""


def data_root(env: Mapping[str, str]) -> Path:
    return Path(env.get("CLEARANCE_DATA") or Path(__file__).resolve().parents[2] / "data")


def resolve_key(root: Path, key: str) -> Path:
    """Storage key -> path under root; rejects absolute keys and '..' segments."""
    k = key.replace("\\", "/").lstrip("/")
    if not k or any(part in ("..", "") for part in k.split("/")):
        raise SpecError(f"invalid storage key: {key!r}")
    p = (root / k).resolve()
    if root.resolve() not in p.parents and p != root.resolve():
        raise SpecError(f"storage key escapes the data root: {key!r}")
    return p


def load_spec(prefix: str = "CLEARANCE_JOB_SPEC", env: Optional[Mapping[str, str]] = None) -> dict:
    """Read <prefix>, <prefix>_B64 or <prefix>_KEY from env and return the decoded JSON object."""
    env = os.environ if env is None else env
    raw: Optional[str] = None
    if env.get(prefix):
        raw = env[prefix]
    elif env.get(prefix + "_B64"):
        try:
            raw = base64.b64decode(env[prefix + "_B64"], validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as e:
            raise SpecError(f"{prefix}_B64 is not valid base64 UTF-8: {e}") from e
    elif env.get(prefix + "_KEY"):
        p = resolve_key(data_root(env), env[prefix + "_KEY"])
        if not p.is_file():
            raise SpecError(f"{prefix}_KEY points to a missing file: {p}")
        raw = p.read_text(encoding="utf-8")
    if raw is None:
        raise SpecError(f"no job spec: set {prefix}, {prefix}_B64 or {prefix}_KEY")
    try:
        spec = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SpecError(f"{prefix} is not valid JSON: {e}") from e
    if not isinstance(spec, dict):
        raise SpecError(f"{prefix} must be a JSON object")
    return spec


def load_job_spec(env: Optional[Mapping[str, str]] = None) -> dict:
    spec = load_spec("CLEARANCE_JOB_SPEC", env)
    for field in ("job_id", "entrypoint"):
        if field not in spec:
            raise SpecError(f"job spec is missing '{field}'")
    return spec


def main(argv: Optional[list[str]] = None, env: Optional[Mapping[str, str]] = None) -> int:
    """Run one job from the job spec (see module docstring); write jobs/<id>/result.json."""
    from clearance_core import storage

    from services.api.dispatch import invoke

    try:
        spec = load_job_spec(env)
    except SpecError as e:
        print(json.dumps({"status": "failed", "error": str(e)}), file=sys.stderr)
        return 2
    result = invoke(spec["entrypoint"], spec.get("kwargs", {}))
    storage.write_json(f"jobs/{spec['job_id']}/result.json", _dump(result))
    print(json.dumps({"job_id": spec["job_id"], "status": "succeeded"}))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
