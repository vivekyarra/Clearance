"""Test doubles, fixtures and flow helpers for Clearance API integration tests.

Other workstreams' modules (recon pipeline, orchestrator, sim live session, usage) are replaced by small
test doubles registered in sys.modules, so these tests exercise the API's lazy-import path unchanged.
"""
from __future__ import annotations

import io
import json
import sys
import threading
import time
import types
import zipfile

import numpy as np
import pytest
from fastapi.testclient import TestClient

from clearance_core import grid, schemas, storage

OPERATOR_TOKEN = "integration-operator-token-0123456789"
AUTH = {"Authorization": f"Bearer {OPERATOR_TOKEN}"}

ROBOT_REVIEWED = {
    "robot_id": "acme_tray_t1", "class": "A_service", "vendor": "Acme", "model": "Tray T1",
    "footprint_polygon_m": [[0.25, 0.22], [-0.25, 0.22], [-0.25, -0.22], [0.25, -0.22]],
    "height_m": 1.2, "max_speed_mps": 1.2, "nominal_speed_mps": 0.7, "max_threshold_m": 0.02,
    "verified_by_human": True,
}
ROBOT_AWAITING = {
    "robot_id": "acme_cart_c2", "class": "B_amr", "vendor": "Acme", "model": "Cart C2",
    "footprint_polygon_m": [[0.4, 0.3], [-0.4, 0.3], [-0.4, -0.3], [0.4, -0.3]],
    "max_speed_mps": None, "verified_by_human": False,
}


def wait_for(fn, timeout=10.0, interval=0.02):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"condition not met in {timeout}s; last={last!r}")


def zip_bytes(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def capture_files(n_frames: int = 3, seed: int = 0) -> dict[str, bytes]:
    poses = []
    for i in range(n_frames):
        T = [1, 0, 0, 0.1 * i + seed, 0, 1, 0, 0, 0, 0, 1, 1.2, 0, 0, 0, 1]
        poses.append(json.dumps({"t": 0.1 * i, "frame": f"{i:06d}.jpg", "T_world_cam": T,
                                 "K": [1000, 1000, 640, 360], "w": 1280, "h": 720}))
    files = {"poses.jsonl": ("\n".join(poses) + "\n").encode()}
    for i in range(n_frames):
        files[f"frames/{i:06d}.jpg"] = bytes([i % 256]) * 64
    return files


# ---------------------------------------------------------------- test doubles
def _recon_module(calls: list):
    mod = types.ModuleType("jobs.recon.pipeline")

    def build_twin_from_capture(capture_dir, site_id, out_prefix, *, tier=None):
        from pathlib import Path

        n_poses = len((Path(capture_dir) / "poses.jsonl").read_text().strip().splitlines())
        calls.append({"capture_dir": capture_dir, "site_id": site_id, "out_prefix": out_prefix, "tier": tier,
                      "n_poses": n_poses})
        data = np.full((80, 120), grid.FREE, np.uint8)
        data[0, :] = data[-1, :] = data[:, 0] = data[:, -1] = grid.OCC
        occ = grid.save(grid.Grid(data, 0.05, (0.0, 0.0)), out_prefix + "occ.png")
        storage.path(out_prefix + "mesh.glb").write_bytes(b"glTF")
        return schemas.Twin(
            twin_id="pipeline_twin", site_id=site_id, occupancy=occ,
            primitives=[schemas.Primitive(id="T1", type="box", label="table",
                                          pose=schemas.Pose2(xyz=(3.0, 2.0, 0.375)), size=(0.8, 0.8, 0.75),
                                          movable=True)],
            waypoints=[schemas.Waypoint(id="DOCK", xy=(0.5, 0.5), kind="dock"),
                       schemas.Waypoint(id="PICKUP", xy=(1.0, 3.0), kind="pickup"),
                       schemas.Waypoint(id="T1", xy=(4.0, 2.0), kind="dest")],
            assets={"mesh_glb": out_prefix + "mesh.glb"},
            accuracy=schemas.Accuracy(method="estimated", p90_abs_err_m=0.03),
        )

    mod.build_twin_from_capture = build_twin_from_capture
    return mod


class OrchestratorControl:
    def __init__(self):
        self.calls: list = []
        self.delay_s = 0.0
        self.barrier: threading.Barrier | None = None
        self.fail_with: str | None = None


def _orchestrator_module(ctl: OrchestratorControl):
    mod = types.ModuleType("agents.orchestrator.run")

    def run_analysis(analysis_id, twin, robots, task, business, *, budget):
        assert isinstance(twin, schemas.Twin) and all(isinstance(r, schemas.RobotSpec) for r in robots)
        ctl.calls.append({"analysis_id": analysis_id, "twin_version": twin.version,
                          "robots": [r.robot_id for r in robots], "task": task.sequence, "business": business})
        base = f"analyses/{analysis_id}/"
        doc = storage.read_json(base + "analysis.json")
        doc.update(status="running", stages={"feasibility": {"state": "done", "progress": 1.0},
                                             "simulation": {"state": "running", "progress": 0.5}})
        storage.write_json(base + "analysis.json", doc)
        if ctl.barrier is not None:
            ctl.barrier.wait(timeout=10)
        if ctl.delay_s:
            time.sleep(ctl.delay_s)
        if ctl.fail_with:
            raise RuntimeError(ctl.fail_with)
        bid = f"sb_{analysis_id[-8:]}"
        frames = []
        for ep in range(2):
            for k in range(5):
                ev = [{"type": "near_miss", "xy": [1.0, 1.0]}] if (ep == 1 and k == 2) else []
                frames.append({"episode": ep, "t": 0.1 * k, "robot": [0.5 + 0.1 * k, 0.5, 0.0, 0.5],
                               "peds": [[2.0, 2.0]], "events": ev})
        storage.path(base + f"batches/{bid}.episodes.jsonl").write_text(
            "\n".join(json.dumps(f) for f in frames) + "\n", encoding="utf-8")
        batch = schemas.SimBatch(batch_id=bid, twin_id=twin.twin_id, twin_version=twin.version,
                                 robot_id=robots[0].robot_id, task=task, density="normal",
                                 geometry_bound="nominal", n_episodes=2, seed_base=0,
                                 results=schemas.BatchResults(success_rate=0.5, ci95=(0.1, 0.9), n_episodes=2,
                                                              n_success=1),
                                 episodes_uri=base + f"batches/{bid}.episodes.jsonl")
        storage.write_json(base + f"batches/{bid}.json", batch)
        facts = [
            schemas.Fact(fact_id="f_001", value=0.5, unit="ratio", label="Success rate, normal traffic",
                         origin=schemas.FactOrigin(type="sim_batch", ref=bid)),
            schemas.Fact(fact_id="f_002", value=0.03, unit="m", label="Scan p90 error",
                         origin=schemas.FactOrigin(type="measurement", ref="accuracy.p90_abs_err_m")),
        ]
        storage.write_json(base + "facts.json", [f.model_dump(mode="json") for f in facts])
        storage.path(base + "report.md").write_text(
            "# Clearance Report\n\nSimulated success is 50% [f_001] with scan error 0.03 m [f_002].\n",
            encoding="utf-8")
        storage.write_json(base + "citations.json", [])
        inv = schemas.Investigation(investigation_id=f"inv_{analysis_id}", analysis_id=analysis_id,
                                    model_id="deterministic-policy", stop_reason="finished")
        storage.write_json(base + "investigation.json", inv)
        verdicts = [{"robot_id": robots[0].robot_id, "label": "Not cleared",
                     "main_reason": "success 50% at normal traffic", "as_is": []}]
        doc.update(status="done", batches=[bid], verdicts=verdicts, investigation_id=inv.investigation_id,
                   stages={"feasibility": {"state": "done", "progress": 1.0},
                           "simulation": {"state": "done", "progress": 1.0}})
        storage.write_json(base + "analysis.json", doc)
        return {"analysis_id": analysis_id, "status": "done"}

    mod.run_analysis = run_analysis
    return mod


class LiveSessionDouble:
    instances: list = []

    def __init__(self, twin, robot, task, density, seed):
        self.twin, self.robot, self.task, self.density, self.seed = twin, robot, task, density, seed
        self.joystick = []
        self.t = 0.0
        LiveSessionDouble.instances.append(self)

    def set_joystick(self, vx, vy):
        self.joystick.append((vx, vy))

    def step(self):
        self.t = round(self.t + 0.05, 3)
        events = [{"type": "near_miss", "xy": [1.0, 1.0]}] if abs(self.t - 0.25) < 1e-9 else []
        return {"t": self.t, "robot": {"x": self.t, "y": 0.0, "yaw": 0.0, "v": np.float32(0.5), "state": "moving"},
                "peds": [[1.0, 1.0]], "events": events}


# ---------------------------------------------------------------- fixtures
@pytest.fixture
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    catalog = tmp_path / "catalog"
    samples = tmp_path / "samples"
    catalog.mkdir()
    samples.mkdir()
    for r in (ROBOT_REVIEWED, ROBOT_AWAITING):
        (catalog / f"{r['robot_id']}.json").write_text(json.dumps(r), encoding="utf-8")
    monkeypatch.setattr(storage, "DATA", data)
    monkeypatch.setenv("CLEARANCE_USAGE_DB", str(tmp_path / "usage.db"))
    return {"tmp": tmp_path, "data": data, "catalog": catalog, "samples": samples}


@pytest.fixture
def make_client(env):
    from services.api.main import create_app
    from services.api.settings import Settings

    clients = []

    def _make(*, anonymous: bool = False, **overrides):
        """A client for the API. By default it acts as the operator (sends the operator bearer token);
        anonymous=True sends no token, like a visitor browsing the hosted demo."""
        kw = dict(data_dir=env["data"], sample_dir=env["samples"], catalog_dir=env["catalog"],
                  web_dist=env["tmp"] / "no_web_dist", infra_dir=env["tmp"] / "infra", secret="integration-secret",
                  job_workers=4, operator_token=OPERATOR_TOKEN)
        kw.update(overrides)
        c = TestClient(create_app(Settings(**kw)))
        if not anonymous:
            c.headers.update(AUTH)
        c.__enter__()
        clients.append(c)
        return c

    yield _make
    for c in clients:
        try:
            c.__exit__(None, None, None)
        except Exception:  # noqa: BLE001 - a test may have closed it already
            pass


@pytest.fixture
def client(make_client):
    return make_client()


@pytest.fixture
def recon(monkeypatch):
    calls: list = []
    monkeypatch.setitem(sys.modules, "jobs.recon.pipeline", _recon_module(calls))
    return calls


@pytest.fixture
def orchestrator(monkeypatch):
    ctl = OrchestratorControl()
    monkeypatch.setitem(sys.modules, "agents.orchestrator.run", _orchestrator_module(ctl))
    return ctl


@pytest.fixture
def usage(monkeypatch):
    mod = types.ModuleType("agents.llm.usage")
    mod.summary = lambda: {"calls_by_model": {"nano": 3}, "total_cost_usd": 0.0}
    mod.log_usage = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "agents.llm.usage", mod)
    return mod


@pytest.fixture
def live_sim(monkeypatch):
    mod = types.ModuleType("jobs.sim.api")
    mod.LiveSession = LiveSessionDouble
    LiveSessionDouble.instances = []
    monkeypatch.setitem(sys.modules, "jobs.sim.api", mod)
    return LiveSessionDouble


@pytest.fixture
def no_components(monkeypatch):
    for name in ("jobs.recon.pipeline", "agents.orchestrator.run", "jobs.sim.api", "agents.llm.usage"):
        monkeypatch.setitem(sys.modules, name, None)


# ---------------------------------------------------------------- flow helpers
def create_site(c, name="Corner Cafe"):
    r = c.post("/sites", json={"name": name, "type": "cafe", "area_m2": 60})
    assert r.status_code == 201, r.text
    return r.json()


def upload_capture(c, site_id, *, files=None, n_parts=2, seed=0):
    files = files or capture_files(seed=seed)
    names = sorted(files)
    parts = [dict((n, files[n]) for n in names[i::n_parts]) for i in range(n_parts)]
    r = c.post(f"/sites/{site_id}/captures",
               json={"device": {"model": "iPhone14,5", "os": "iOS 18", "tier": "T2_VIO_MONO"},
                     "role": "scanner", "n_chunks": n_parts})
    assert r.status_code == 201, r.text
    cap = r.json()
    for part, info in zip(parts, cap["upload"]["chunks"]):
        u = c.put(info["url"], content=zip_bytes(part))
        assert u.status_code == 200, u.text
    return cap


def build_twin(c, site_id, **kw):
    cap = upload_capture(c, site_id, **kw)
    r = c.post(f"/captures/{cap['capture_id']}/complete")
    assert r.status_code == 202, r.text
    st = wait_for(lambda: (lambda s: s if s["status"] in ("done", "failed") else None)(
        c.get(f"/captures/{cap['capture_id']}").json()))
    assert st["status"] == "done", st
    return cap, st


def wait_analysis(c, aid, timeout=15):
    return wait_for(lambda: (lambda s: s if s["status"] in ("done", "failed") else None)(
        c.get(f"/analyses/{aid}").json()), timeout=timeout)
