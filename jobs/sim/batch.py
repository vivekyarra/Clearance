"""Monte Carlo batches: JSON shard specs (one Serverless Job or one process each), merging, results."""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import subprocess
import time
import zlib
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache
from pathlib import Path

import numpy as np

from clearance_core import storage
from clearance_core.schemas import BatchResults, RobotSpec, SimBatch, Task, Twin

from . import params as P
from .episode import make_context, nominal_geom, run_episode
from .routing import waypoint_check

SPEC_VERSION = 1
BOTTLENECK_SCALE = 1000  # Pinch Map units per event / per blocked second


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for k successes in n trials; (0, 1) when there are no trials."""
    if n <= 0:
        return (0.0, 1.0)
    if not 0 <= k <= n:
        raise ValueError(f"wilson: need 0 <= k <= n, got k={k}, n={n}")
    if k == 0:
        return (0.0, (z * z / n) / (1 + z * z / n))
    if k == n:
        return (1.0 / (1 + z * z / n), 1.0)
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, c - h), min(1.0, c + h))


@lru_cache(maxsize=1)
def code_version() -> str:
    """Git commit (when available) plus a hash of the simulator sources. The source hash is always included: with
    uncommitted edits the commit alone would give batches from different simulator code the same id."""
    root = Path(__file__).resolve().parents[2]
    h = hashlib.sha1()
    for f in sorted(Path(__file__).parent.glob("*.py")):
        h.update(f.name.encode())
        h.update(f.read_bytes().replace(b"\r\n", b"\n"))
    src = "sim-" + h.hexdigest()[:10]
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True,
                             text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip() + "+" + src
    except (OSError, subprocess.SubprocessError):
        pass
    return src


def _jsonable_overrides(overrides: dict | None) -> dict | None:
    if not overrides:
        return None
    return json.loads(json.dumps(overrides, default=lambda o: o.model_dump(mode="json")))


def batch_id_for(twin: Twin, robot: RobotSpec, task: Task, density: str, bound: str, n: int, seed_base: int,
                 overrides: dict | None, sim_config: dict | None) -> str:
    blob = json.dumps([twin.twin_id, twin.version, robot.robot_id, task.model_dump(mode="json"), density, bound,
                       n, seed_base, _jsonable_overrides(overrides), sim_config or {}, code_version()],
                      sort_keys=True, default=str)
    return "sb_" + hashlib.sha1(blob.encode()).hexdigest()[:12]


def make_shard_specs(twin: Twin, robot: RobotSpec, task: Task, density: str, bound: str, n_episodes: int,
                     seed_base: int, *, overrides: dict | None = None, record_episodes: bool = True,
                     out_prefix: str | None = None, sim_config: dict | None = None,
                     shard_size: int = P.SHARD_SIZE, twin_key: str | None = None,
                     batch_id: str | None = None, inline_occupancy: bool = False) -> list[dict]:
    """JSON-serializable shard specs. The twin travels inline unless `twin_key` (a storage key) is given;
    inline_occupancy=True also embeds the occupancy PNG so a worker needs no shared storage."""
    if n_episodes <= 0:
        raise ValueError("n_episodes must be positive")
    bid = batch_id or batch_id_for(twin, robot, task, density, bound, n_episodes, seed_base, overrides, sim_config)
    n_shards = int(math.ceil(n_episodes / shard_size))
    common = {
        "spec_version": SPEC_VERSION,
        "batch_id": bid,
        "twin": None if twin_key else twin.model_dump(mode="json"),
        "twin_key": twin_key,
        "robot": robot.model_dump(mode="json", by_alias=True),
        "task": task.model_dump(mode="json"),
        "density": density,
        "bound": bound,
        "n_episodes": n_episodes,
        "seed_base": seed_base,
        "overrides": _jsonable_overrides(overrides),
        "record_episodes": record_episodes,
        "out_prefix": out_prefix,
        "sim_config": sim_config or {},
        "shard_size": shard_size,
        "n_shards": n_shards,
        "code_version": code_version(),
    }
    if inline_occupancy:
        common["occ_png_b64"] = base64.b64encode((storage.DATA / twin.occupancy.uri).read_bytes()).decode("ascii")
    return [{**common, "shard_index": i} for i in range(n_shards)]


def _pack(b: bytes) -> str:
    return base64.b64encode(zlib.compress(b, 6)).decode("ascii")


def _unpack(s: str) -> bytes:
    return zlib.decompress(base64.b64decode(s))


def run_shard(spec: dict) -> dict:
    """Run one shard. JSON in, JSON out (episode summaries, compressed bottleneck grid + episode JSONL)."""
    t0 = time.perf_counter()
    twin = Twin.model_validate(spec["twin"] if spec.get("twin") else storage.read_json(spec["twin_key"]))
    robot = RobotSpec.model_validate(spec["robot"])
    task = Task.model_validate(spec["task"])
    ctx = make_context(twin, robot, task, spec["density"], spec["bound"], spec.get("overrides"),
                       spec.get("sim_config"), spec.get("p90_err_m"), spec.get("occ_png_b64"))
    i = int(spec["shard_index"])
    size = int(spec.get("shard_size", P.SHARD_SIZE))
    a, b = i * size, min((i + 1) * size, int(spec["n_episodes"]))
    if a >= b:
        raise ValueError(f"shard {i} is empty for n_episodes={spec['n_episodes']}")
    # integer milli-units (events x 1000, blocked seconds x 1000): sums are exact, so the merged Pinch Map does not
    # depend on how episodes were split into shards
    bott = np.zeros(ctx.layers.shape, np.int64)
    record = bool(spec.get("record_episodes", True))
    eps = []
    z = zlib.compressobj(6)
    parts, n_lines = [], 0
    for ep in range(a, b):
        summ, ln = run_episode(ctx, ep, int(spec["seed_base"]) + ep, bott, record)
        eps.append(summ)
        if record and ln:
            # compressed episode by episode: a shard never holds all of its uncompressed frames at once
            parts.append(z.compress(("\n".join(ln) + "\n").encode()))
            n_lines += len(ln)
    jsonl = base64.b64encode(b"".join(parts) + z.flush()).decode("ascii") if record and n_lines else None
    out = {
        "spec_version": SPEC_VERSION,
        "batch_id": spec["batch_id"],
        "shard_index": i,
        "n_shards": spec.get("n_shards"),
        "episode_range": [a, b],
        "episodes": eps,
        "bottleneck": {"shape": list(bott.shape), "dtype": "int64", "scale": BOTTLENECK_SCALE,
                       "zlib_b64": _pack(bott.tobytes())},
        "episodes_jsonl_zlib_b64": jsonl,
        "waypoint_issues": waypoint_issues(ctx),
        "wall_time_s": round(time.perf_counter() - t0, 3),
        "density": ctx.density,
        "meta": {k: spec[k] for k in ("twin_key", "density", "bound", "n_episodes", "seed_base", "out_prefix",
                                       "record_episodes", "code_version") if k in spec},
        "twin_id": twin.twin_id, "twin_version": twin.version, "robot_id": robot.robot_id,
        "task": task.model_dump(mode="json"),
    }
    return out


def waypoint_issues(ctx) -> list[dict]:
    """Task waypoints with no collision-free robot pose on the nominal layout (see routing.waypoint_check)."""
    geom = nominal_geom(ctx)
    wp = {w.id: w for w in ctx.twin.waypoints}
    out = []
    for i in dict.fromkeys(ctx.task.sequence):
        d = waypoint_check(geom, i, wp[i].xy[0], wp[i].xy[1], wp[i].yaw)
        if not d["ok"]:
            out.append(d)
    return out


def summarize(episodes: list[dict]) -> BatchResults:
    n = len(episodes)
    succ = [e for e in episodes if e["outcome"] == "success"]
    k = len(succ)
    fails = {r: 0 for r in P.FAIL_REASONS}
    for e in episodes:
        if e["outcome"] != "success":
            fails[e["outcome"]] = fails.get(e["outcome"], 0) + 1
    times = [e["mission_time_s"] for e in succ if e.get("mission_time_s") is not None]
    gaps = [e["min_gap_m"] for e in episodes if e.get("min_gap_m") is not None]
    return BatchResults(
        success_rate=k / n if n else 0.0,
        ci95=wilson(k, n),
        min_gap_m=float(np.median(gaps)) if gaps else None,
        median_mission_s=float(np.median(times)) if times else None,
        near_miss_per_mission=float(np.mean([e["near_misses"] for e in episodes])) if n else 0.0,
        stops_per_mission=float(np.mean([e["stops"] for e in episodes])) if n else 0.0,
        fail_reasons=fails, n_success=k, n_episodes=n)


def merge_shards(shards: list[dict]) -> SimBatch:
    """Combine shard outputs (any order) into a SimBatch; writes batch files when out_prefix was set."""
    return merge_shards_full(shards)[0]


def merge_shards_full(shards: list[dict]) -> tuple[SimBatch, list[dict], np.ndarray]:
    """merge_shards plus the per-episode summaries and the summed bottleneck grid."""
    if not shards:
        raise ValueError("no shards to merge")
    shards = sorted(shards, key=lambda s: s["shard_index"])
    bid = shards[0]["batch_id"]
    if any(s["batch_id"] != bid for s in shards):
        raise ValueError("shards belong to different batches")
    idx = [s["shard_index"] for s in shards]
    if len(set(idx)) != len(idx):
        raise ValueError("duplicate shard indexes")
    meta = shards[0]["meta"]
    for s in shards[1:]:
        for k in ("density", "bound", "n_episodes", "seed_base", "code_version"):
            if s["meta"].get(k) != meta.get(k):
                raise ValueError(f"shards disagree on {k}: {meta.get(k)!r} vs {s['meta'].get(k)!r}")
    n_expected = int(meta["n_episodes"])
    episodes = [e for s in shards for e in s["episodes"]]
    if len(episodes) != n_expected:
        raise ValueError(f"shards cover {len(episodes)} of {n_expected} episodes")
    if [e["episode"] for e in episodes] != list(range(n_expected)):
        raise ValueError("shards do not cover episodes 0..n-1 exactly once, in order")
    shape = tuple(shards[0]["bottleneck"]["shape"])
    ticks = np.zeros(shape, np.int64)
    for s in shards:
        bn = s["bottleneck"]
        if tuple(bn["shape"]) != shape:
            raise ValueError("shards have different grid shapes")
        if bn.get("dtype") != "int64" or int(bn.get("scale", 0)) != BOTTLENECK_SCALE:
            raise ValueError("shard bottleneck grid has an unexpected encoding (re-run the shard)")
        ticks += np.frombuffer(_unpack(bn["zlib_b64"]), np.int64).reshape(shape)
    bott = (ticks / BOTTLENECK_SCALE).astype(np.float32)
    batch = SimBatch(
        batch_id=bid, twin_id=shards[0]["twin_id"], twin_version=shards[0]["twin_version"],
        robot_id=shards[0]["robot_id"], task=Task.model_validate(shards[0]["task"]),
        density=shards[0].get("density", meta["density"]), geometry_bound=meta["bound"], n_episodes=n_expected,
        seed_base=int(meta["seed_base"]), code_version=meta.get("code_version", "dev"),
        results=summarize(episodes), wall_time_s=round(sum(s["wall_time_s"] for s in shards), 3))
    prefix = meta.get("out_prefix")
    if prefix:
        base = prefix.rstrip("/") + "/" + bid
        np.save(storage.path(base + ".bottleneck.npy"), bott)
        batch.bottleneck_uri = base + ".bottleneck.npy"
        if meta.get("record_episodes", True):
            with open(storage.path(base + ".episodes.jsonl"), "wb") as f:
                for s in shards:
                    if s.get("episodes_jsonl_zlib_b64"):
                        f.write(_unpack(s["episodes_jsonl_zlib_b64"]))
            batch.episodes_uri = base + ".episodes.jsonl"
        storage.write_json(base + ".summary.json", {"batch_id": bid, "episodes": episodes,
                                                   "waypoint_issues": shards[0].get("waypoint_issues") or []})
        storage.write_json(base + ".json", batch)
    return batch, episodes, bott


def _init_worker(data_root: str) -> None:
    storage.DATA = Path(data_root)  # same storage root as the parent process


def run_shards_local(specs: list[dict], workers: int | None = None) -> list[dict]:
    """Run shard specs in this process (workers<=1) or in a ProcessPoolExecutor."""
    workers = int(workers if workers is not None else os.environ.get("CLEARANCE_SIM_WORKERS", "1"))
    if workers <= 1 or len(specs) <= 1:
        return [run_shard(s) for s in specs]
    with ProcessPoolExecutor(max_workers=min(workers, len(specs)), initializer=_init_worker,
                             initargs=(str(storage.DATA),)) as ex:
        return list(ex.map(run_shard, specs))


def run_batch(twin: Twin, robot: RobotSpec, task: Task, density: str, bound: str, n_episodes: int,
              seed_base: int, *, overrides: dict | None = None, record_episodes: bool = True,
              out_prefix: str | None = None, workers: int | None = None, sim_config: dict | None = None,
              shard_size: int = P.SHARD_SIZE) -> SimBatch:
    return run_batch_full(twin, robot, task, density, bound, n_episodes, seed_base, overrides=overrides,
                          record_episodes=record_episodes, out_prefix=out_prefix, workers=workers,
                          sim_config=sim_config, shard_size=shard_size)[0]


def run_batch_full(twin: Twin, robot: RobotSpec, task: Task, density: str, bound: str, n_episodes: int,
                   seed_base: int, *, overrides: dict | None = None, record_episodes: bool = True,
                   out_prefix: str | None = None, workers: int | None = None, sim_config: dict | None = None,
                   shard_size: int = P.SHARD_SIZE) -> tuple[SimBatch, list[dict], np.ndarray]:
    """run_batch plus per-episode summaries and the bottleneck grid (for in-process callers)."""
    t0 = time.perf_counter()
    eff_density = (overrides or {}).get("density", density)
    specs = make_shard_specs(twin, robot, task, eff_density, bound, n_episodes, seed_base, overrides=overrides,
                             record_episodes=record_episodes, out_prefix=out_prefix, sim_config=sim_config,
                             shard_size=shard_size)
    batch, episodes, bott = merge_shards_full(run_shards_local(specs, workers))
    batch.wall_time_s = round(time.perf_counter() - t0, 3)
    if out_prefix:
        storage.write_json(out_prefix.rstrip("/") + "/" + batch.batch_id + ".json", batch)
    return batch, episodes, bott
