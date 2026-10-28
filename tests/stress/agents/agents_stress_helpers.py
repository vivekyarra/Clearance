"""Shared helpers for the agents stress tests: a scripted httpx transport (real request/response handling, scripted
server answers), completion builders and a process-pool worker for the usage DB."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
INV_DIR = HERE.parents[1] / "unit" / "investigate"
if str(INV_DIR) not in sys.path:
    sys.path.insert(0, str(INV_DIR))


class ScriptedTransport(httpx.BaseTransport):
    """httpx transport answering from a script. Items: httpx.Response, an exception (raised as the network
    failure), or a callable(request) -> Response. When the script runs out, `default` (if set) answers."""

    def __init__(self, script=(), default=None):
        self.script = list(script)
        self.default = default
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.script.pop(0) if self.script else self.default
        if item is None:
            raise AssertionError("scripted transport exhausted")
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            item = item(request)
        return item

    def payloads(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests]


def completion(content="", tool_calls=None, *, model="m", pt=10, ct=5, usage=True) -> dict:
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    out = {"id": "cmpl", "model": model,
           "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if tool_calls else "stop"}]}
    if usage:
        out["usage"] = {"prompt_tokens": pt, "completion_tokens": ct}
    return out


def tool_call(name, arguments, i=0) -> dict:
    """One OpenAI-format tool call; `arguments` dict is JSON-encoded, a str is sent verbatim."""
    return {"id": f"call_{name}_{i}", "type": "function",
            "function": {"name": name, "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments)}}


def ok(body: dict | bytes | str, status=200, headers=None) -> httpx.Response:
    if isinstance(body, dict):
        return httpx.Response(status, json=body, headers=headers)
    return httpx.Response(status, content=body if isinstance(body, bytes) else body.encode(), headers=headers)


def offline_run(data_dir: str, policy: str = "offline") -> str:
    """One offline investigation in a fresh storage dir (run in a subprocess); returns the investigation JSON."""
    import numpy as np

    os.environ["CLEARANCE_USAGE_DB"] = str(Path(data_dir) / "usage.db")
    from clearance_core import storage
    storage.DATA = Path(data_dir)
    from clearance_core.schemas import BatchResults, SimBatch, Task
    from inv_doubles import SimDouble, bottleneck_map, make_robot, make_twin, wilson

    from agents.investigate.loop import run_investigation
    from agents.investigate.tools import SimAPI
    from agents.llm.client import LLM

    twin, robot, task = make_twin(), make_robot(), Task(sequence=["DOCK", "T7", "DOCK"])
    key = "analyses/a1/batches/sb_nn.bottleneck.npy"
    np.save(storage.path(key), bottleneck_map())
    batches = [SimBatch(batch_id="sb_nn", twin_id="tw_test", twin_version=1, robot_id=robot.robot_id, task=task,
                        density="normal", geometry_bound="nominal", n_episodes=100, seed_base=1000,
                        results=BatchResults(success_rate=0.5, ci95=wilson(50, 100), n_success=50, n_episodes=100,
                                             fail_reasons={"blocked_timeout": 50}), bottleneck_uri=key)]
    sim = SimDouble()
    kw = {"llm": LLM("offline"), "policy": "llm"} if policy == "llm" else {}
    run_investigation("a1", twin, robot, task, batches, sim=SimAPI(sim.rasterize_twin, sim.run_batch, None),
                      other_robots=[make_robot("bot_b")], **kw)
    return (Path(data_dir) / "analyses/a1/investigation.json").read_text(encoding="utf-8")


def pool_log_rows(args) -> int:
    """Worker: log n usage rows into the DB named by CLEARANCE_USAGE_DB (inherited env)."""
    worker, n = args
    from agents.llm import usage
    for i in range(n):
        usage.log_usage("stress", f"model_{worker % 3}", 10, 3, 0.01, 0.001,
                        {"analysis_id": f"a{worker % 2}", "worker": worker, "i": i})
        if i % 25 == 0:
            usage.summary()
    return os.getpid()
