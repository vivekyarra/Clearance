"""Container entry point for Clearance jobs on Nebius Serverless Jobs (and locally with docker run).

    python infra/nebius/entry.py task    # one dispatcher job: services.api.tasks.main()
    python infra/nebius/entry.py shard   # one simulation shard: jobs.sim.api.run_shard(spec)

Job specs arrive as CLEARANCE_JOB_SPEC, CLEARANCE_JOB_SPEC_B64 or CLEARANCE_JOB_SPEC_KEY; services.api.tasks
reads all three (see its docstring). Shards read CLEARANCE_SHARD_SPEC / _B64 / _KEY the same way and write
their result JSON to spec["out_key"] (default "shards/<CLEARANCE_JOB_NAME or shard index>.json") under
CLEARANCE_DATA.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Callable, Mapping, Optional

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from services.api.tasks import SpecError, data_root, load_job_spec, load_spec, resolve_key  # noqa: E402

__all__ = ["SpecError", "data_root", "load_spec", "resolve_key", "run_task", "run_shard", "main"]


def run_task(env: Mapping[str, str], tasks_main: Optional[Callable[[], int]] = None) -> int:
    spec = load_job_spec(env)
    os.environ["CLEARANCE_JOB_SPEC"] = json.dumps(spec, separators=(",", ":"))
    if tasks_main is None:
        from services.api.tasks import main as tasks_main  # noqa: PLC0415
    return int(tasks_main() or 0)


def run_shard(env: Mapping[str, str], shard_fn: Optional[Callable[[dict], dict]] = None) -> int:
    spec = load_spec("CLEARANCE_SHARD_SPEC", env)
    if shard_fn is None:
        from jobs.sim.api import run_shard as shard_fn  # noqa: PLC0415
    result = shard_fn(spec)
    name = env.get("CLEARANCE_JOB_NAME") or f"shard_{spec.get('shard_index', 0)}"
    out_key = spec.get("out_key") or f"shards/{name}.json"
    out = resolve_key(data_root(env), out_key)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, separators=(",", ":"), default=str), encoding="utf-8")
    print(json.dumps({"status": "succeeded", "out_key": out_key}))
    return 0


def main(argv: Optional[list[str]] = None, env: Optional[Mapping[str, str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    env = os.environ if env is None else env
    mode = argv[0] if argv else "task"
    try:
        if mode == "task":
            return run_task(env)
        if mode == "shard":
            return run_shard(env)
    except SpecError as e:
        print(json.dumps({"status": "failed", "error": str(e)}), file=sys.stderr)
        return 2
    print(f"unknown mode {mode!r}; use 'task' or 'shard'", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
