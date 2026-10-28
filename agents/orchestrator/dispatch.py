"""Batch dispatchers. A dispatcher takes batch specs (JSON-serializable kwargs for jobs.sim.api.run_batch) and
returns SimBatch objects in the same order:

    dispatcher(specs: list[dict], on_result: Callable[[int, SimBatch], None] | None = None) -> list[SimBatch]

Local default: a ProcessPool running run_batch per spec. services/api can pass its own dispatcher that shards
each spec over Nebius Serverless Jobs (jobs.sim.api.run_shard / merge_shards)."""
from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Callable

from clearance_core import storage
from clearance_core.schemas import RobotSpec, SimBatch, Task, Twin


def make_spec(twin: Twin, robot: RobotSpec, task: Task, density: str, bound: str, n_episodes: int, seed_base: int,
              *, overrides: dict | None = None, record_episodes: bool = True, out_prefix: str | None = None) -> dict:
    return {"twin": twin.model_dump(mode="json", by_alias=True), "robot": robot.model_dump(mode="json", by_alias=True),
            "task": task.model_dump(mode="json"), "density": density, "bound": bound, "n_episodes": int(n_episodes),
            "seed_base": int(seed_base), "overrides": overrides, "record_episodes": record_episodes,
            "out_prefix": out_prefix}


def _call(run_batch: Callable, spec: dict) -> SimBatch:
    b = run_batch(Twin.model_validate(spec["twin"]), RobotSpec.model_validate(spec["robot"]),
                  Task.model_validate(spec["task"]), spec["density"], spec["bound"], spec["n_episodes"],
                  spec["seed_base"], overrides=spec.get("overrides"), record_episodes=spec.get("record_episodes", True),
                  out_prefix=spec.get("out_prefix"))
    return b if isinstance(b, SimBatch) else SimBatch.model_validate(b)


def _worker(spec: dict, data_root: str) -> dict:
    storage.DATA = type(storage.DATA)(data_root)  # same storage root as the parent process
    from jobs.sim import api  # type: ignore
    return _call(api.run_batch, spec).model_dump(mode="json")


def inline_dispatcher(run_batch: Callable) -> Callable:
    """Runs specs sequentially in this process (tests, injected sims, debugging)."""
    def dispatch(specs: list[dict], on_result=None) -> list[SimBatch]:
        out = []
        for i, s in enumerate(specs):
            b = _call(run_batch, s)
            out.append(b)
            if on_result:
                on_result(i, b)
        return out
    return dispatch


def process_pool_dispatcher(max_workers: int | None = None) -> Callable:
    workers = max_workers or max(1, min(8, (os.cpu_count() or 2) - 1))

    def dispatch(specs: list[dict], on_result=None) -> list[SimBatch]:
        if not specs:
            return []
        out: list[SimBatch | None] = [None] * len(specs)
        root = str(storage.DATA)
        with ProcessPoolExecutor(max_workers=min(workers, len(specs))) as ex:
            futs = {ex.submit(_worker, s, root): i for i, s in enumerate(specs)}
            for f in as_completed(futs):
                i = futs[f]
                out[i] = SimBatch.model_validate(f.result())
                if on_result:
                    on_result(i, out[i])
        return out  # type: ignore[return-value]
    return dispatch
