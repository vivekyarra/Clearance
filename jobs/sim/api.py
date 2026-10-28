"""Public contract of the simulation module (docs/CONTRACTS.md, jobs/sim/api.py).

rasterize_twin(twin, bound, p90_err_m, overrides) -> Grid
check_feasibility(twin, robot, task, bound) -> Feasibility
check_waypoints(twin, robot, task=None, bound) -> [{"waypoint", "ok", "gap_m", "message", ...}]
run_batch(twin, robot, task, density, bound, n_episodes, seed_base, *, overrides, record_episodes, out_prefix) -> SimBatch
run_shard(spec) -> dict            JSON in / JSON out, one Serverless Job (or one process) per shard
merge_shards(shards) -> SimBatch
LiveSession(twin, robot, task, density, seed): set_joystick(vx, vy), step() -> frame

overrides = {"move": {obj_id: {"dx", "dy", "dyaw"}}, "remove": [obj_id], "add_boxes": [Primitive | dict],
             "ped_zone_scale": {zone_id: factor}, "density": "none|quiet|normal|peak"}
"""
from __future__ import annotations

from .batch import (make_shard_specs, merge_shards, merge_shards_full, run_batch, run_batch_full, run_shard,
                    run_shards_local, wilson)
from .feasibility import check_feasibility, check_waypoints
from .live import LiveSession
from .raster import rasterize_twin

__all__ = ["rasterize_twin", "check_feasibility", "check_waypoints", "run_batch", "run_shard", "merge_shards", "LiveSession",
           "make_shard_specs", "run_shards_local", "run_batch_full", "merge_shards_full", "wilson"]
