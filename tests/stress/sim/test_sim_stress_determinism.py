"""Determinism of the simulator: results depend on the twin geometry, robot, task and seed only."""
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from clearance_core import storage
from jobs.sim import jitcache
from jobs.sim.api import merge_shards, run_batch, run_shard
from jobs.sim.batch import make_shard_specs, run_batch_full
from jobs.sim.episode import episode_variation, make_context

sys.path.insert(0, str(Path(__file__).parent))
from sim_stress_rooms import cafe_like  # noqa: E402

REPO = Path(__file__).resolve().parents[3]


def test_waypoints_outside_the_task_do_not_change_episodes():
    # Regression: OTTO 100 on the reference storeroom moved from 90% to 84% on the same seeds when ENTRANCE/EXIT
    # waypoints (not on its task) were added: clutter boxes kept away from every waypoint and were drawn by rejection
    # from one shared stream, so one extra rejection shifted every later draw of the episode.
    a_twin, robot, task = cafe_like(key="sites/stress_sim/wp_a")
    b_twin, _, _ = cafe_like(extra_waypoints=[("ENTRANCE", 1.0, 0.5, 0.0, "entrance"),
                                              ("EXIT", 4.0, 2.9, 0.0, "exit")], key="sites/stress_sim/wp_b")
    ca = make_context(a_twin, robot, task, "normal", "nominal")
    cb = make_context(b_twin, robot, task, "normal", "nominal")
    for seed in range(40):
        pa, ja, cla = episode_variation(ca, seed)
        pb, jb, clb = episode_variation(cb, seed)
        assert ja == jb and len(cla) == len(clb)
        assert all(np.array_equal(x, y) for x, y in zip(pa, pb))
    _, ea, _ = run_batch_full(a_twin, robot, task, "normal", "nominal", 12, 100, record_episodes=False)
    _, eb, _ = run_batch_full(b_twin, robot, task, "normal", "nominal", 12, 100, record_episodes=False)
    assert ea == eb


def test_primitive_order_does_not_change_episodes():
    a_twin, robot, task = cafe_like(key="sites/stress_sim/order_a")
    b_twin, _, _ = cafe_like(reverse_primitives=True, key="sites/stress_sim/order_b")
    _, ea, _ = run_batch_full(a_twin, robot, task, "normal", "nominal", 10, 7, record_episodes=False)
    _, eb, _ = run_batch_full(b_twin, robot, task, "normal", "nominal", 10, 7, record_episodes=False)
    assert ea == eb
    assert any(e["n_jittered"] > 0 for e in ea) and any(e["n_clutter"] > 0 for e in ea)


def test_serial_pool_worker_counts_and_shard_sizes_are_bit_identical():
    twin, robot, task = cafe_like()
    runs = []
    for i, (workers, shard) in enumerate([(1, 25), (1, 1), (2, 3), (3, 4)]):
        b = run_batch(twin, robot, task, "normal", "nominal", 9, 500, out_prefix=f"analyses/det{i}/batches/",
                      workers=workers, shard_size=shard)
        runs.append((b, (storage.DATA / b.episodes_uri).read_bytes(),
                     np.load(storage.DATA / b.bottleneck_uri),
                     storage.read_json(f"analyses/det{i}/batches/{b.batch_id}.summary.json")["episodes"]))
    b0, j0, n0, e0 = runs[0]
    assert len(j0) > 1000
    for b, j, n, e in runs[1:]:
        assert b.results == b0.results
        assert j == j0
        assert np.array_equal(n, n0)
        assert e == e0


def test_merge_of_shuffled_shards_equals_single_run_and_rejects_mixed_shards():
    twin, robot, task = cafe_like()
    whole = run_batch(twin, robot, task, "peak", "nominal", 7, 21, out_prefix="analyses/m1/batches/")
    specs = make_shard_specs(twin, robot, task, "peak", "nominal", 7, 21, shard_size=3, batch_id=whole.batch_id,
                             out_prefix="analyses/m2/batches/")
    parts = [run_shard(json.loads(json.dumps(s))) for s in specs]
    merged = merge_shards([json.loads(json.dumps(p)) for p in (parts[2], parts[0], parts[1])])
    assert merged.results == whole.results
    assert (storage.DATA / merged.episodes_uri).read_bytes() == (storage.DATA / whole.episodes_uri).read_bytes()
    other = make_shard_specs(twin, robot, task, "peak", "nominal", 7, 99, shard_size=3, batch_id=whole.batch_id)
    with pytest.raises(ValueError, match="disagree"):
        merge_shards([parts[0], parts[1], run_shard(other[2])])
    bad = json.loads(json.dumps(parts[1]))
    bad["episodes"][0]["episode"] = 0  # overlaps shard 0
    with pytest.raises(ValueError, match="exactly once"):
        merge_shards([parts[0], bad, parts[2]])


def test_python_hash_seed_does_not_change_a_shard(tmp_path):
    twin, robot, task = cafe_like()
    spec = make_shard_specs(twin, robot, task, "normal", "nominal", 3, 4, inline_occupancy=True)[0]
    sp = tmp_path / "spec.json"
    sp.write_text(json.dumps(spec))
    (tmp_path / "empty").mkdir()
    outs = []
    for hs in ("0", "4242"):
        out = tmp_path / f"out_{hs}.json"
        env = {**os.environ, "PYTHONHASHSEED": hs, "PYTHONPATH": str(REPO), "CLEARANCE_DATA": str(tmp_path / "empty")}
        r = subprocess.run([sys.executable, "-m", "jobs.sim.cli", "shard", str(sp), str(out)], cwd=REPO, env=env,
                           capture_output=True, text=True, timeout=900)
        assert r.returncode == 0, r.stderr
        res = json.loads(out.read_text())
        outs.append((res["episodes"], res["bottleneck"]["zlib_b64"], res["episodes_jsonl_zlib_b64"]))
    assert outs[0] == outs[1]


def test_kernel_cache_is_keyed_by_every_kernel_module_and_fastmath_is_off():
    # numba validates a cached kernel against its own file only: control.choose_speed calls peds.poly_sdist, so an
    # edit to peds.py used to leave a stale choose_speed in the cache. The cache directory hashes all kernel modules.
    from jobs.sim import control, peds, planner
    assert Path(jitcache.CACHE_DIR).name.startswith("sim-")
    for fn in (control.choose_speed, peds.poly_sdist, peds.ped_step, planner.hybrid_astar, planner.rail_hits):
        assert fn.targetoptions.get("fastmath") is False
        assert str(fn._cache._cache_path).startswith(jitcache.CACHE_DIR)
    assert set(jitcache.KERNEL_MODULES) >= {"planner.py", "peds.py", "control.py"}
