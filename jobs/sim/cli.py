"""Command line for simulation shards.

  python -m jobs.sim.cli shard spec.json out.json        run one shard spec (Serverless Job entry point)
  python -m jobs.sim.cli merge out.json shard1.json ...  merge shard outputs into a SimBatch JSON
  python -m jobs.sim.cli local specs.json out.json -w 8  run a list of specs with a local process pool
  python -m jobs.sim.cli bench [-n 20] [-w 1]            measure episodes/minute on a 10 x 8 m reference room
"""
from __future__ import annotations

import argparse
import json
import sys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m jobs.sim.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("shard")
    a.add_argument("spec")
    a.add_argument("out")
    m = sub.add_parser("merge")
    m.add_argument("out")
    m.add_argument("shards", nargs="+")
    loc = sub.add_parser("local")
    loc.add_argument("specs")
    loc.add_argument("out")
    loc.add_argument("-w", "--workers", type=int, default=4)
    bn = sub.add_parser("bench")
    bn.add_argument("-n", type=int, default=20)
    bn.add_argument("-w", "--workers", type=int, default=1)
    bn.add_argument("--density", default="normal")
    args = ap.parse_args(argv)
    if args.cmd == "bench":
        print(json.dumps(bench(args.n, args.workers, args.density), indent=2))
        return 0

    from .batch import merge_shards, run_shard, run_shards_local

    if args.cmd == "shard":
        with open(args.spec, encoding="utf-8") as f:
            spec = json.load(f)
        res = run_shard(spec)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(res, f)
        print(json.dumps({"batch_id": res["batch_id"], "shard_index": res["shard_index"],
                          "episodes": len(res["episodes"]), "wall_time_s": res["wall_time_s"]}))
    elif args.cmd == "merge":
        shards = []
        for p in args.shards:
            with open(p, encoding="utf-8") as f:
                shards.append(json.load(f))
        batch = merge_shards(shards)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(batch.model_dump_json(indent=2))
        print(batch.results.model_dump_json())
    else:
        with open(args.specs, encoding="utf-8") as f:
            specs = json.load(f)
        batch = merge_shards(run_shards_local(specs, args.workers))
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(batch.model_dump_json(indent=2))
        print(batch.results.model_dump_json())
    return 0


def bench(n: int = 20, workers: int = 1, density: str = "normal") -> dict:
    """Episodes/minute on a 10 x 8 m reference room (400 x 320 cells) with tables, a door and traffic."""
    import tempfile
    import time

    from clearance_core import storage
    from clearance_core.schemas import Task

    from .batch import run_batch_full
    from .twin_builder import RoomCanvas, box, make_twin, rect_robot

    storage.DATA = __import__("pathlib").Path(tempfile.mkdtemp(prefix="clearance_bench_"))
    c = RoomCanvas(10.0, 8.0, res=0.025)
    c.rect(4.9, 0.0, 5.1, 3.4)
    c.rect(4.9, 4.6, 5.1, 8.0)
    prims = [box(f"T{i}", 1.5 + 2.0 * (i % 2), 1.5 + 1.6 * (i // 2), 0.8, 0.8) for i in range(6)]
    prims += [box("C1", 7.0, 2.0, 0.45, 0.45, label="chair"), box("S1", 8.0, 6.5, 1.2, 0.5, label="shelf", movable=False)]
    zones = [("door_w", [(0.3, 3.0), (1.0, 3.0), (1.0, 5.0), (0.3, 5.0)]),
             ("door_e", [(9.0, 3.0), (9.7, 3.0), (9.7, 5.0), (9.0, 5.0)]),
             ("counter", [(6.0, 0.3), (8.0, 0.3), (8.0, 1.0), (6.0, 1.0)])]
    twin = make_twin(c, "bench/room", [("DOCK", 0.8, 7.2, 0.0, "dock"), ("PICK", 7.0, 1.4, 0.0, "pickup"),
                                       ("T1", 2.5, 4.0, 0.0, "dest"), ("T2", 8.5, 5.5, 0.0, "dest")],
                     primitives=prims, zones=zones,
                     od_pairs=[("door_w", "door_e", 1), ("door_e", "door_w", 1), ("door_w", "counter", 1),
                               ("counter", "door_w", 1)], persons_per_min={"quiet": 3, "normal": 8, "peak": 16})
    robot = rect_robot(0.5, 0.6)
    task = Task(sequence=["DOCK", "PICK", "T1", "PICK", "T2", "DOCK"])
    run_batch_full(twin, robot, task, density, "nominal", 1, 0)  # load compiled kernels
    t0 = time.perf_counter()
    batch, eps, _ = run_batch_full(twin, robot, task, density, "nominal", n, 1000, workers=workers)
    wall = time.perf_counter() - t0
    sim_s = sum(e["elapsed_s"] for e in eps)
    return {"episodes": n, "workers": workers, "density": density, "wall_s": round(wall, 2),
            "episodes_per_min": round(60 * n / wall, 1), "mean_sim_time_s": round(sim_s / n, 1),
            "realtime_factor": round(sim_s / wall, 1), "mean_peds_peak": round(sum(e["peds_peak"] for e in eps) / n, 1),
            "success_rate": batch.results.success_rate}


if __name__ == "__main__":
    sys.exit(main())
