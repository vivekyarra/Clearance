"""infra/nebius helpers: job submission command, status mapping, container entry point, shard fan-out."""
from __future__ import annotations

import base64
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
NEBIUS = REPO / "infra" / "nebius"
JOBS = REPO / "infra" / "jobs"


def _load(name: str):
    if str(NEBIUS) not in sys.path:
        sys.path.insert(0, str(NEBIUS))
    spec = importlib.util.spec_from_file_location(f"infra_nebius_{name}", NEBIUS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


submit_job = _load("submit_job")
job_status = _load("job_status")
entry = _load("entry")
shards = _load("submit_sim_shards")

ENV = {
    "CLEARANCE_IMAGE_JOBS_GPU": "cr.eu-north1.nebius.cloud/abc/clearance-jobs-gpu:0.1.0",
    "CLEARANCE_IMAGE_JOBS_CPU": "cr.eu-north1.nebius.cloud/abc/clearance-jobs-cpu:0.1.0",
    "CLEARANCE_S3_BUCKET": "clearance-data",
    "NEBIUS_SUBNET_ID": "vpcsubnet-e00test",
}


def flag_values(cmd: list[str], flag: str) -> list[str]:
    return [cmd[i + 1] for i, a in enumerate(cmd[:-1]) if a == flag]


class Proc:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


# ---------------------------------------------------------------- names and expansion
@pytest.mark.parametrize("raw,expected", [
    ("job_recon_0123456789abcdef01234567", "job-recon-0123456789abcdef01234567"),
    ("Job__Analysis--X", "job-analysis-x"),
    ("123abc", "j-123abc"),
    ("sim_b1_0", "sim-b1-0"),
])
def test_nebius_name(raw, expected):
    assert submit_job.nebius_name(raw) == expected


def test_nebius_name_length_and_charset():
    name = submit_job.nebius_name("job_" + "A" * 200 + "_tail")
    assert len(name) <= submit_job.NAME_MAX
    assert set(name) <= set("abcdefghijklmnopqrstuvwxyz0123456789-")
    assert not name.endswith("-")


def test_nebius_name_rejects_empty():
    with pytest.raises(submit_job.SubmitError):
        submit_job.nebius_name("___")


def test_expand_nested_and_missing():
    out = submit_job.expand({"a": ["s3://${B}:/data", {"x": "${B}-y"}], "n": 3}, {"B": "bkt"})
    assert out == {"a": ["s3://bkt:/data", {"x": "bkt-y"}], "n": 3}
    with pytest.raises(submit_job.SubmitError, match="MISSING"):
        submit_job.expand("${MISSING}", {})
    with pytest.raises(submit_job.SubmitError):
        submit_job.expand("${EMPTY}", {"EMPTY": ""})


# ---------------------------------------------------------------- job definitions
@pytest.mark.parametrize("kind", ["recon", "analysis", "sim_shard"])
def test_job_definitions_render(kind):
    d = submit_job.load_definition(kind)
    fields = {"job_id": "job_x_1", "image": d["image"], "spec_json": '{"a": {"b": [1, 2]}}', "kind": kind}
    rendered = [str(a).format(**fields) for a in d["submit_command"]]  # same as NebiusDispatcher
    assert rendered[:2] == ["python", "infra/nebius/submit_job.py"]
    assert '{"a": {"b": [1, 2]}}' in rendered
    status = [str(a).format(job_id="job_x_1") for a in d["status_command"]]
    assert status[-1] == "job_x_1"
    res = d["resources"]
    assert res["platform"] and res["preset"] and res["timeout"]
    hours = int(str(res["timeout"]).rstrip("h"))
    assert 1 <= hours <= 168  # docs: min 1h, max 168h
    gpu = res["platform"].startswith("gpu-")
    disk = int(str(res["disk_size"]).rstrip("Gi"))
    assert disk >= (50 if gpu else 20)  # docs minimums


def test_build_command_recon():
    d = submit_job.load_definition("recon")
    cmd = submit_job.build_command(d, name="job-recon-1", image="${CLEARANCE_IMAGE_JOBS_GPU}",
                                   extra_env={"CLEARANCE_JOB_SPEC_B64": "e30="}, env=ENV)
    assert cmd[:4] == ["nebius", "ai", "job", "create"]
    assert flag_values(cmd, "--image") == [ENV["CLEARANCE_IMAGE_JOBS_GPU"]]
    assert flag_values(cmd, "--platform") == ["gpu-l40s-a"]
    assert flag_values(cmd, "--preset") == ["1gpu-8vcpu-32gb"]
    assert flag_values(cmd, "--volume") == ["s3://clearance-data:/data:rw"]
    assert flag_values(cmd, "--subnet-id") == ["vpcsubnet-e00test"]
    envs = flag_values(cmd, "--env")
    assert "CLEARANCE_DATA=/data" in envs and "CLEARANCE_JOB_SPEC_B64=e30=" in envs
    assert "CLEARANCE_JOB_NAME=job-recon-1" in envs
    assert flag_values(cmd, "--args") == ["/app/infra/nebius/entry.py task"]
    assert "--dry-run" not in cmd and "--async" in cmd


def test_build_command_shard_preemptible_and_dry_run():
    d = dict(submit_job.load_definition("sim_shard"), preemptible=True)
    cmd = submit_job.build_command(d, name="sim-b-0", image="", extra_env={}, mode="shard",
                                   dry_run=True, env=ENV)
    assert flag_values(cmd, "--image") == [ENV["CLEARANCE_IMAGE_JOBS_CPU"]]  # falls back to definition
    assert "--preemptible" in cmd and "--follows-spot-price" in cmd
    assert flag_values(cmd, "--restart-policy") == ["on-failure"]
    assert flag_values(cmd, "--args") == ["/app/infra/nebius/entry.py shard"]
    assert cmd[-1] == "--dry-run"


def test_build_command_errors():
    d = submit_job.load_definition("analysis")
    with pytest.raises(submit_job.SubmitError, match="CLEARANCE_S3_BUCKET"):
        submit_job.build_command(d, name="n", image="img", extra_env={}, env={"CLEARANCE_IMAGE_JOBS_CPU": "x"})
    with pytest.raises(submit_job.SubmitError, match="comma"):
        submit_job.build_command(d, name="n", image="img", extra_env={"BAD": "a,b"}, env=ENV)
    with pytest.raises(submit_job.SubmitError, match="image"):
        submit_job.build_command({"resources": {}}, name="n", image="", extra_env={}, env=ENV)
    with pytest.raises(submit_job.SubmitError):
        submit_job.load_definition("no_such_kind")


# ---------------------------------------------------------------- spec delivery
def test_spec_env_inline_roundtrip_through_entry():
    spec = {"job_id": "job_recon_1", "entrypoint": "m:f",
            "kwargs": {"note": 'commas, "quotes", = signs, unicode ✓', "n": [1, 2, 3]}}
    env = submit_job.spec_env(json.dumps(spec), "job-recon-1", "CLEARANCE_JOB_SPEC")
    assert list(env) == ["CLEARANCE_JOB_SPEC_B64"]
    assert "," not in env["CLEARANCE_JOB_SPEC_B64"]
    assert entry.load_spec("CLEARANCE_JOB_SPEC", env) == spec


def test_spec_env_large_goes_to_bucket(tmp_path):
    uploaded = {}
    big = json.dumps({"job_id": "j", "entrypoint": "m:f", "kwargs": {"blob": "x" * 200_000}})
    env = submit_job.spec_env(big, "job-big", "CLEARANCE_JOB_SPEC",
                              uploader=lambda k, b: uploaded.__setitem__(k, b))
    assert env == {"CLEARANCE_JOB_SPEC_KEY": "jobs/job-big/spec.json"}
    # Inside the job the bucket is mounted at CLEARANCE_DATA: emulate it and read the spec back.
    (tmp_path / "jobs" / "job-big").mkdir(parents=True)
    (tmp_path / "jobs" / "job-big" / "spec.json").write_bytes(uploaded["jobs/job-big/spec.json"])
    got = entry.load_spec("CLEARANCE_JOB_SPEC", {**env, "CLEARANCE_DATA": str(tmp_path)})
    assert len(got["kwargs"]["blob"]) == 200_000


def test_spec_env_rejects_invalid_json():
    with pytest.raises(ValueError):
        submit_job.spec_env("{not json", "n", "CLEARANCE_JOB_SPEC")


def test_submit_main_runs_cli_and_reports(capsys):
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        return Proc(0, json.dumps({"id": "op-1", "resource_id": "aijob-e00abc"}))

    spec = json.dumps({"job_id": "job_recon_9", "entrypoint": "services.api.tasks:run_recon", "kwargs": {}})
    import os
    old = dict(os.environ)
    os.environ.update(ENV)
    try:
        rc = submit_job.main(["--kind", "recon", "--name", "job_recon_9", "--image", "${CLEARANCE_IMAGE_JOBS_GPU}",
                              "--spec", spec], runner=runner)
    finally:
        os.environ.clear()
        os.environ.update(old)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"submitted": True, "name": "job-recon-9", "dry_run": False, "resource_id": "aijob-e00abc"}
    assert flag_values(calls[0], "--name") == ["job-recon-9"]


def test_submit_main_failure_paths(capsys):
    assert submit_job.main(["--kind", "nope", "--name", "x", "--spec", "{}"]) == 2
    import os
    old = dict(os.environ)
    os.environ.update(ENV)
    try:
        rc = submit_job.main(["--kind", "analysis", "--name", "x", "--spec", "{}"],
                             runner=lambda cmd, **kw: Proc(1, "", "QuotaExceeded"))
    finally:
        os.environ.clear()
        os.environ.update(old)
    assert rc == 1
    assert "QuotaExceeded" in capsys.readouterr().err


def test_dispatcher_rendered_command_is_accepted(tmp_path):
    """NebiusDispatcher.build_submission -> submit_job.main parses exactly what the dispatcher renders."""
    dispatch = pytest.importorskip("services.api.dispatch")
    disp = dispatch.NebiusDispatcher(None, REPO / "infra", submit_enabled=True)
    sub = disp.build_submission("job_analysis_abc", "analysis", "services.api.tasks:run_analysis_job",
                                {"analysis_id": "a1", "business": {"note": "a, b"}})
    cmd = sub["command"]
    assert cmd[:2] == ["python", "infra/nebius/submit_job.py"]
    seen = []
    import os
    old = dict(os.environ)
    os.environ.update(ENV)
    try:
        rc = submit_job.main(cmd[2:], runner=lambda c, **kw: (seen.append(c), Proc(0, "{}"))[1])
    finally:
        os.environ.clear()
        os.environ.update(old)
    assert rc == 0
    b64 = [v for v in flag_values(seen[0], "--env") if v.startswith("CLEARANCE_JOB_SPEC_B64=")][0]
    spec = json.loads(base64.b64decode(b64.split("=", 1)[1]))
    assert spec["job_id"] == "job_analysis_abc" and spec["kwargs"]["business"]["note"] == "a, b"


# ---------------------------------------------------------------- status
@pytest.mark.parametrize("state,status", [
    ("COMPLETED", "completed"), ("FAILED", "failed"), ("ERROR", "error"), ("CANCELLED", "cancelled"),
    ("RUNNING", "running"), ("IMAGE_PULLING", "running"), ("PROVISIONING", "running"), ("", "pending"),
    ("SOMETHING_NEW", "something_new"),
])
def test_status_mapping(state, status):
    assert job_status.to_status(state) == status


def test_parse_state_shapes():
    assert job_status.parse_state(json.dumps({"status": {"state": "COMPLETED"}})) == "COMPLETED"
    assert job_status.parse_state(json.dumps({"status": {"state": "STATE_RUNNING"}})) == "RUNNING"
    assert job_status.parse_state(json.dumps({"status": {"state": "STATE_UNSPECIFIED"}})) == "STATE_UNSPECIFIED"
    assert job_status.parse_state("not json") == ""
    assert job_status.parse_state("[]") == ""
    assert job_status.parse_state(json.dumps({"status": "weird"})) == ""


def test_status_main(capsys):
    seen = []

    def runner(cmd, **kw):
        seen.append(cmd)
        return Proc(0, json.dumps({"metadata": {"id": "x"}, "status": {"state": "COMPLETED"}}))

    assert job_status.main(["--name", "job_recon_1"], runner=runner) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"name": "job-recon-1", "state": "COMPLETED", "status": "completed"}
    assert seen[0] == ["nebius", "ai", "job", "get-by-name", "--name", "job-recon-1", "--format", "json"]
    # CLI failure (e.g. not yet visible) keeps the job pending instead of failing it
    assert job_status.main(["--name", "j"], runner=lambda c, **kw: Proc(1, "", "not found")) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "pending"


# ---------------------------------------------------------------- entry point
def test_load_spec_precedence_and_errors(tmp_path):
    raw = {"job_id": "a", "entrypoint": "m:f"}
    b64 = base64.b64encode(json.dumps({"job_id": "b", "entrypoint": "m:f"}).encode()).decode()
    assert entry.load_spec("P", {"P": json.dumps(raw), "P_B64": b64})["job_id"] == "a"
    assert entry.load_spec("P", {"P_B64": b64})["job_id"] == "b"
    with pytest.raises(entry.SpecError, match="no job spec"):
        entry.load_spec("P", {})
    with pytest.raises(entry.SpecError, match="base64"):
        entry.load_spec("P", {"P_B64": "@@not-base64@@"})
    with pytest.raises(entry.SpecError, match="JSON"):
        entry.load_spec("P", {"P": "{bad"})
    with pytest.raises(entry.SpecError, match="object"):
        entry.load_spec("P", {"P": "[1, 2]"})
    with pytest.raises(entry.SpecError, match="missing file"):
        entry.load_spec("P", {"P_KEY": "jobs/x/spec.json", "CLEARANCE_DATA": str(tmp_path)})


@pytest.mark.parametrize("key", ["../etc/passwd", "/abs/../../x", "a/../../b", "a//b", ""])
def test_resolve_key_rejects_escapes(tmp_path, key):
    with pytest.raises(entry.SpecError):
        entry.resolve_key(tmp_path, key)


def test_run_task_sets_env_and_calls_tasks(monkeypatch):
    monkeypatch.delenv("CLEARANCE_JOB_SPEC", raising=False)
    spec = {"job_id": "j1", "entrypoint": "m:f", "kwargs": {"x": 1}}
    seen = {}

    def tasks_main():
        import os
        seen["spec"] = json.loads(os.environ["CLEARANCE_JOB_SPEC"])
        return 0

    b64 = base64.b64encode(json.dumps(spec).encode()).decode()
    assert entry.run_task({"CLEARANCE_JOB_SPEC_B64": b64}, tasks_main=tasks_main) == 0
    assert seen["spec"] == spec
    with pytest.raises(entry.SpecError, match="entrypoint"):
        entry.run_task({"CLEARANCE_JOB_SPEC": json.dumps({"job_id": "j"})}, tasks_main=tasks_main)


def test_run_shard_writes_result(tmp_path, capsys):
    spec = {"shard_index": 3, "n_shards": 4, "out_key": "analyses/a1/shards/b1_3.json"}
    env = {"CLEARANCE_SHARD_SPEC": json.dumps(spec), "CLEARANCE_DATA": str(tmp_path)}
    assert entry.run_shard(env, shard_fn=lambda s: {"episodes": 25, "idx": s["shard_index"]}) == 0
    out = json.loads((tmp_path / "analyses/a1/shards/b1_3.json").read_text())
    assert out == {"episodes": 25, "idx": 3}
    assert json.loads(capsys.readouterr().out)["out_key"] == spec["out_key"]
    # default key uses the job name
    env2 = {"CLEARANCE_SHARD_SPEC": "{}", "CLEARANCE_DATA": str(tmp_path), "CLEARANCE_JOB_NAME": "sim-b-1"}
    entry.run_shard(env2, shard_fn=lambda s: {})
    assert (tmp_path / "shards" / "sim-b-1.json").is_file()


def test_entry_main_modes(capsys):
    assert entry.main(["task"], env={}) == 2
    assert "no job spec" in capsys.readouterr().err
    assert entry.main(["bogus"], env={}) == 2


def test_entry_cli_process(tmp_path):
    """End to end as the container runs it: python infra/nebius/entry.py task, with a real entrypoint."""
    spec = {"job_id": "job_t_1", "entrypoint": "json:dumps", "kwargs": {"obj": {"ok": True}}}
    env = {"CLEARANCE_JOB_SPEC_B64": base64.b64encode(json.dumps(spec).encode()).decode(),
           "CLEARANCE_DATA": str(tmp_path), "PYTHONPATH": str(REPO), "SYSTEMROOT": "C:\\Windows",
           "PATH": ""}
    tasks = importlib.util.find_spec("services.api.tasks")
    if tasks is None:
        pytest.skip("services.api.tasks not present")
    proc = subprocess.run([sys.executable, str(NEBIUS / "entry.py"), "task"], env=env,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "jobs" / "job_t_1" / "result.json").is_file()


# ---------------------------------------------------------------- shard fan-out
def test_shard_specs():
    batch = {"analysis_id": "a1", "batch_id": "b7", "n_episodes": 100}
    specs = shards.shard_specs(batch, 4)
    assert [s["shard_index"] for s in specs] == [0, 1, 2, 3]
    assert {s["n_shards"] for s in specs} == {4}
    assert len({s["out_key"] for s in specs}) == 4
    assert specs[2]["out_key"] == "analyses/a1/shards/b7_2.json"
    custom = shards.shard_specs(dict(batch, out_key="runs/x.json"), 2)
    assert [s["out_key"] for s in custom] == ["runs/x_0.json", "runs/x_1.json"]
    with pytest.raises(ValueError):
        shards.shard_specs(batch, 0)


def test_submit_sim_shards_main(tmp_path):
    p = tmp_path / "batch.json"
    p.write_text(json.dumps({"analysis_id": "a", "batch_id": "b", "n_episodes": 50}))
    calls = []
    rc = shards.main([str(p), "3", "--dry-run"], submit=lambda args: calls.append(args) or 0)
    assert rc == 0 and len(calls) == 3
    assert all("--dry-run" in c and c[c.index("--mode") + 1] == "shard" for c in calls)
    assert [c[c.index("--name") + 1] for c in calls] == ["sim_b_0", "sim_b_1", "sim_b_2"]


def test_endpoint_definitions_parse():
    for name in ("api", "cosmos_reason2"):
        values = {}
        for line in (NEBIUS / "endpoints" / f"{name}.env").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                k, _, v = line.partition("=")
                values[k] = v
        assert values["ENDPOINT_NAME"].startswith("clearance-")
        assert values["ENDPOINT_PORT"] == "8000"
    assert values["VISION_MODEL_ID"] == "nvidia/Cosmos-Reason2-8B"
    assert values["ENDPOINT_PLATFORM"].startswith("gpu-")


def test_yaml_files_load():
    for p in JOBS.glob("*.yaml"):
        assert isinstance(yaml.safe_load(p.read_text(encoding="utf-8")), dict), p
