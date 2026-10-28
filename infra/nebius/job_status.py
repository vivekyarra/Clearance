"""Print the status of a Clearance job on Nebius Serverless Jobs as one JSON line.

    python infra/nebius/job_status.py --name job_recon_ab12
    -> {"name": "job-recon-ab12", "state": "COMPLETED", "status": "completed"}

Used as the `status_command` in infra/jobs/<kind>.yaml (services/api NebiusDispatcher reads the "status"
field: completed -> succeeded; failed / error / cancelled -> failed; anything else -> still running).
CLI: `nebius ai job get-by-name --name <name> --format json`, field status.state
(https://docs.nebius.com/serverless/quickstart/jobs.md, https://docs.nebius.com/serverless/lifecycle.md).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from submit_job import nebius_name  # noqa: E402

# Nebius job states (docs/VERIFIED.md 2.10) -> dispatcher vocabulary.
STATE_MAP = {
    "COMPLETED": "completed",
    "FAILED": "failed",
    "ERROR": "error",
    "CANCELLED": "cancelled",
    "CANCELLING": "cancelled",
    "DELETING": "cancelled",
    "PROVISIONING": "running",
    "STARTING": "running",
    "IMAGE_PULLING": "running",
    "RUNNING": "running",
    "STATE_UNSPECIFIED": "pending",
}


def parse_state(payload: str) -> str:
    """Return status.state from `nebius ai job get(-by-name) --format json` output ('' if absent)."""
    try:
        data = json.loads(payload)
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    status = data.get("status") or {}
    s = str(status.get("state", "") if isinstance(status, dict) else "").upper()
    if s.startswith("STATE_") and s != "STATE_UNSPECIFIED":  # tolerate enum-style prefixes
        s = s[len("STATE_"):]
    return s


def to_status(state: str) -> str:
    return STATE_MAP.get(state, "pending" if not state else state.lower())


def main(argv: Optional[list[str]] = None,
         runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> int:
    ap = argparse.ArgumentParser(description="Print a Nebius job status as JSON")
    ap.add_argument("--name", required=True, help="dispatcher job id or Nebius job name")
    a = ap.parse_args(argv)
    name = nebius_name(a.name)
    proc = runner(["nebius", "ai", "job", "get-by-name", "--name", name, "--format", "json"],
                  capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        print(json.dumps({"name": name, "state": "", "status": "pending",
                          "detail": (proc.stderr or "").strip()[-300:]}))
        return 0
    state = parse_state(proc.stdout)
    print(json.dumps({"name": name, "state": state, "status": to_status(state)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
