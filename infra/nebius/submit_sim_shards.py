"""Fan one simulation batch out to N CPU Serverless Jobs (one jobs.sim.api.run_shard call per job).

    python infra/nebius/submit_sim_shards.py <batch_spec.json> <n_shards> [--dry-run] [--print-only]

batch_spec.json holds the run_batch arguments plus analysis_id and batch_id. Shard i gets
shard_index=i, n_shards=N and out_key=analyses/<analysis_id>/shards/<batch_id>_<i>.json; collect the
results from the bucket and combine them with jobs.sim.api.merge_shards. Parallelism is bounded by the
project's Compute quotas (VMs, vCPUs): docs/VERIFIED.md 2.7.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import submit_job  # noqa: E402


def shard_specs(batch: dict, n_shards: int) -> list[dict]:
    if n_shards < 1:
        raise ValueError("n_shards must be >= 1")
    aid = batch.get("analysis_id", "adhoc")
    bid = batch.get("batch_id", "batch")
    out = []
    for i in range(n_shards):
        s = dict(batch, shard_index=i, n_shards=n_shards)
        s.setdefault("out_key", f"analyses/{aid}/shards/{bid}_{i}.json")
        if "out_key" in batch:  # a shared out_key would overwrite; make it per shard
            s["out_key"] = f"{Path(batch['out_key']).with_suffix('').as_posix()}_{i}.json"
        out.append(s)
    return out


def main(argv: Optional[list[str]] = None, submit: Callable[[list[str]], int] = submit_job.main) -> int:
    ap = argparse.ArgumentParser(description="Submit one Nebius job per simulation shard")
    ap.add_argument("batch_spec")
    ap.add_argument("n_shards", type=int)
    ap.add_argument("--image", default="${CLEARANCE_IMAGE_JOBS_CPU}")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--print-only", action="store_true")
    a = ap.parse_args(argv)
    batch = json.loads(Path(a.batch_spec).read_text(encoding="utf-8"))
    rc = 0
    for s in shard_specs(batch, a.n_shards):
        args = ["--kind", "sim_shard", "--mode", "shard",
                "--name", f"sim_{batch.get('batch_id', 'batch')}_{s['shard_index']}",
                "--image", a.image, "--spec", json.dumps(s, separators=(",", ":"))]
        args += ["--dry-run"] if a.dry_run else []
        args += ["--print-only"] if a.print_only else []
        rc = max(rc, submit(args))
    return rc


if __name__ == "__main__":
    sys.exit(main())
