"""Submit one Clearance job to Nebius Serverless Jobs with the Nebius AI Cloud CLI.

Used by services/api NebiusDispatcher through the `submit_command` in infra/jobs/<kind>.yaml, and by
infra/nebius/submit_sim_shards.sh. CLI flags follow https://docs.nebius.com/cli/reference/ai/job/create.md
(checked 2026-10-27, see docs/VERIFIED.md Section 2).

    python infra/nebius/submit_job.py --kind recon --name job_recon_ab12 --image <ref> --spec '<json>'
        [--spec-env CLEARANCE_JOB_SPEC] [--mode task|shard] [--dry-run] [--print-only]

The definition file infra/jobs/<kind>.yaml supplies platform, preset, timeout, disk, env and volumes.
`${VAR}` in definition values is expanded from the environment (CLEARANCE_S3_BUCKET, NEBIUS_SUBNET_ID, ...).
Specs up to INLINE_LIMIT bytes travel base64-encoded in an env var; larger specs are uploaded to the job
bucket (boto3) and passed as a storage key.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Mapping, Optional

REPO = Path(__file__).resolve().parents[2]
JOBS_DIR = REPO / "infra" / "jobs"
INLINE_LIMIT = 48 * 1024  # bytes of base64 text kept in --env; VERIFY the platform's env size ceiling
NAME_MAX = 63              # VERIFY: Nebius resource name rules are not stated on the job create page
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class SubmitError(RuntimeError):
    pass


def nebius_name(job_id: str) -> str:
    """Dispatcher ids (job_recon_<hex>) -> a conservative DNS-label style name: [a-z0-9-], <= 63 chars."""
    s = re.sub(r"[^a-z0-9-]+", "-", job_id.lower()).strip("-")
    s = re.sub(r"-{2,}", "-", s)[:NAME_MAX].rstrip("-")
    if not s:
        raise SubmitError(f"job id {job_id!r} has no usable characters")
    return s if s[0].isalpha() else f"j-{s}"[:NAME_MAX]


def expand(value, env: Mapping[str, str]):
    """Expand ${VAR} recursively in strings, lists and dicts; a missing variable is an error."""
    if isinstance(value, str):
        def sub(m):
            if m.group(1) not in env or env[m.group(1)] == "":
                raise SubmitError(f"environment variable {m.group(1)} is required by the job definition")
            return env[m.group(1)]
        return _VAR.sub(sub, value)
    if isinstance(value, list):
        return [expand(v, env) for v in value]
    if isinstance(value, dict):
        return {k: expand(v, env) for k, v in value.items()}
    return value


def load_definition(kind: str, jobs_dir: Path = JOBS_DIR) -> dict:
    import yaml

    for ext in (".yaml", ".yml", ".json"):
        p = jobs_dir / f"{kind}{ext}"
        if p.is_file():
            text = p.read_text(encoding="utf-8")
            return (json.loads(text) if ext == ".json" else yaml.safe_load(text)) or {}
    raise SubmitError(f"no job definition for kind {kind!r} in {jobs_dir}")


def spec_env(spec_json: str, name: str, prefix: str, *,
             uploader: Optional[Callable[[str, bytes], None]] = None) -> dict[str, str]:
    """Env vars that deliver the spec to infra/nebius/entry.py inside the container."""
    json.loads(spec_json)  # validate early
    b64 = base64.b64encode(spec_json.encode("utf-8")).decode("ascii")
    if len(b64) <= INLINE_LIMIT:
        return {f"{prefix}_B64": b64}
    key = f"jobs/{name}/spec.json"
    (uploader or s3_uploader())(key, spec_json.encode("utf-8"))
    return {f"{prefix}_KEY": key}


def s3_uploader() -> Callable[[str, bytes], None]:
    try:
        import boto3  # type: ignore
    except ImportError as e:
        raise SubmitError("spec is too large for an env var and boto3 is not installed") from e
    bucket = os.environ.get("CLEARANCE_S3_BUCKET")
    if not bucket:
        raise SubmitError("spec is too large for an env var and CLEARANCE_S3_BUCKET is not set")
    prefix = os.environ.get("CLEARANCE_S3_PREFIX", "")
    client = boto3.client(
        "s3",
        endpoint_url=os.environ.get("CLEARANCE_S3_ENDPOINT"),
        region_name=os.environ.get("CLEARANCE_S3_REGION"),
        aws_access_key_id=os.environ.get("CLEARANCE_S3_ACCESS_KEY_ID") or os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("CLEARANCE_S3_SECRET_ACCESS_KEY")
        or os.environ.get("AWS_SECRET_ACCESS_KEY"),
    )

    def upload(key: str, body: bytes) -> None:
        client.put_object(Bucket=bucket, Key=prefix + key, Body=body, ContentType="application/json")

    return upload


def build_command(definition: dict, *, name: str, image: str, extra_env: Mapping[str, str],
                  mode: str = "task", dry_run: bool = False, env: Mapping[str, str] = os.environ) -> list[str]:
    d = expand(definition, env)
    image = expand(image, env) if image else d.get("image")
    if not image:
        raise SubmitError("no container image: pass --image or set `image` in the job definition")
    res = d.get("resources") or {}
    cmd = ["nebius", "ai", "job", "create", "--name", name, "--image", str(image)]
    for flag, key in (("--platform", "platform"), ("--preset", "preset"), ("--timeout", "timeout"),
                      ("--disk-size", "disk_size"), ("--shm-size", "shm_size")):
        if res.get(key):
            cmd += [flag, str(res[key])]
    cmd += ["--restart-policy", str(d.get("restart_policy", "never"))]
    if d.get("preemptible"):
        cmd += ["--preemptible", "--follows-spot-price"]
    subnet = d.get("subnet_id") or env.get("NEBIUS_SUBNET_ID")
    if subnet:
        cmd += ["--subnet-id", str(subnet)]
    if d.get("registry_secret"):
        cmd += ["--registry-secret", str(d["registry_secret"])]
    for vol in d.get("volumes") or []:
        cmd += ["--volume", str(vol)]
    merged = {**{k: str(v) for k, v in (d.get("env") or {}).items()}, "CLEARANCE_JOB_NAME": name, **extra_env}
    for k, v in merged.items():
        if "," in v:
            raise SubmitError(f"env {k} contains a comma; pass it as a secret or inside the spec")
        cmd += ["--env", f"{k}={v}"]
    for k, sel in (d.get("env_secrets") or {}).items():
        cmd += ["--env-secret", f"{k}={sel}"]
    cmd += ["--working-dir", str(d.get("working_dir", "/app")),
            "--container-command", "python",
            "--args", f"{d.get('entry', '/app/infra/nebius/entry.py')} {mode}",
            "--async", "--format", "json"]
    if dry_run:
        cmd.append("--dry-run")
    return cmd


def main(argv: Optional[list[str]] = None,
         runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--kind", required=True)
    ap.add_argument("--name", required=True, help="dispatcher job id (sanitised to a Nebius name)")
    ap.add_argument("--image", default="")
    ap.add_argument("--spec", required=True, help="job spec JSON, or @path/to/spec.json")
    ap.add_argument("--spec-env", default=None, help="env prefix (default by mode)")
    ap.add_argument("--mode", choices=("task", "shard"), default=None)
    ap.add_argument("--jobs-dir", default=str(JOBS_DIR))
    ap.add_argument("--dry-run", action="store_true", help="pass --dry-run to the Nebius CLI")
    ap.add_argument("--print-only", action="store_true", help="print the command and exit")
    a = ap.parse_args(argv)
    try:
        definition = load_definition(a.kind, Path(a.jobs_dir))
        mode = a.mode or definition.get("mode", "task")
        prefix = a.spec_env or ("CLEARANCE_SHARD_SPEC" if mode == "shard" else "CLEARANCE_JOB_SPEC")
        spec_json = Path(a.spec[1:]).read_text(encoding="utf-8") if a.spec.startswith("@") else a.spec
        name = nebius_name(a.name)
        extra = spec_env(spec_json, name, prefix) if not a.print_only else {f"{prefix}_B64": "<spec>"}
        cmd = build_command(definition, name=name, image=a.image, extra_env=extra, mode=mode,
                            dry_run=a.dry_run)
    except (SubmitError, ValueError, OSError) as e:
        print(json.dumps({"submitted": False, "error": str(e)}), file=sys.stderr)
        return 2
    if a.print_only:
        print(json.dumps(cmd))
        return 0
    proc = runner(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr or proc.stdout or "nebius ai job create failed\n")
        return proc.returncode
    try:
        op = json.loads(proc.stdout or "{}")
    except ValueError:
        op = {"raw": (proc.stdout or "")[-2000:]}
    print(json.dumps({"submitted": True, "name": name, "dry_run": a.dry_run,
                      "resource_id": op.get("resource_id") if isinstance(op, dict) else None}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
