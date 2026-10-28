"""Run full Clearance checks on the three reference sites and export compact artifacts.

Pipeline per site (agents/orchestrator/run.py run_analysis): candidates -> static feasibility -> Monte Carlo batches
(process pool) -> Nemotron investigation loop -> layout optimizer -> code verdicts -> facts -> report -> fact-check.

Large outputs (episode JSONL, full Pinch Maps) stay under CLEARANCE_DATA (default ~/.cache/clearance/refchecks);
`--export` copies a compact subset into sample_sites/<site>/analyses/<analysis_id>/ in the storage key layout that
services/api/samples.py imports, plus a run record (run.json) with budget, timings and throughput.

Usage (repo root, PYTHONPATH=.):
    python scripts/run_reference_checks.py                 # run all three sites, then export
    python scripts/run_reference_checks.py --sites ref_cafe
    python scripts/run_reference_checks.py --export-only   # re-export from an earlier run
    python scripts/run_reference_checks.py --export-only --web   # also refresh apps/web/src/offline/fixtures/
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
os.environ.setdefault("CLEARANCE_DATA", str(Path.home() / ".cache" / "pg" / "refchecks"))
os.environ.setdefault("CLEARANCE_LLM_MODE", "offline")
os.environ.setdefault("CLEARANCE_TAVILY_MODE", "offline")
sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402

from clearance_core import storage  # noqa: E402
from clearance_core.schemas import RobotSpec, Task, Twin  # noqa: E402

# Example operator inputs, entered the way a user enters them in the console. They drive the payback section only.
EXAMPLE_INPUTS_NOTE = "example operator inputs entered by the user (reference site)"

SITES = {
    "ref_cafe": {
        "analysis_id": "an_ref_cafe_01",
        "task": ["DOCK", "PICKUP", "T1", "T3", "T5", "DOCK"],
        "task_note": "Delivery round: dock, kitchen pass-through (PICKUP), tables 1, 3 and 5, back to dock.",
        "robot_class": "A_service",
        "business": {"staff_cost_per_hour": 16.0, "task_hours_per_day": 5.0, "days_per_week": 6,
                     "maintenance_pct_per_year": 10.0, "currency": "USD", "robot_class": "A_service",
                     "payload_kg": 10.0, "note": EXAMPLE_INPUTS_NOTE},
    },
    "ref_storeroom": {
        "analysis_id": "an_ref_storeroom_01",
        "task": ["DOCK", "PICKUP", "S1", "S2", "S3", "S4", "DOCK"],
        "task_note": "Picking round: dock, staging pickup, shelf aisles S1-S3, back corner S4, back to dock.",
        "robot_class": "B_amr",
        "business": {"staff_cost_per_hour": 22.0, "task_hours_per_day": 4.0, "days_per_week": 5,
                     "maintenance_pct_per_year": 10.0, "currency": "USD", "robot_class": "B_amr",
                     "payload_kg": 60.0, "note": EXAMPLE_INPUTS_NOTE},
    },
    "ref_corridor": {
        "analysis_id": "an_ref_corridor_01",
        "task": ["DOCK", "PICKUP", "O4", "O3", "O2", "O1", "DOCK"],
        "task_note": "Office supply run: dock, print room pickup, offices O4 to O1, back to dock.",
        "robot_class": None,
        "business": {"staff_cost_per_hour": 20.0, "task_hours_per_day": 2.0, "days_per_week": 5,
                     "maintenance_pct_per_year": 10.0, "currency": "USD", "payload_kg": 10.0,
                     "note": EXAMPLE_INPUTS_NOTE},
    },
}

WORKERS = max(1, (os.cpu_count() or 2) - 1)
# Budget tuned so each site finishes in about 15 minutes or less on 11 workers (the simulator's pinch handling
# costs about twice the time per episode of earlier releases): 40 episodes per batch, optimizer on the 2 most
# promising robots that are not Cleared (candidate ranking order).
BUDGET = {
    "max_robots": 6, "densities": ["quiet", "normal", "peak"], "bounds": ["optimistic", "nominal", "conservative"],
    "episodes": 40, "seed_base": 1000, "max_workers": WORKERS, "investigate_max_robots": 2,
    "optimizer_max_robots": 2, "confirm_episodes": 50, "include_awaiting_review": True,
    "investigation": {"max_steps": 8, "max_experiments": 6, "probe_episodes": 30},
    "optimizer": {"screen_episodes": 30, "confirm_episodes": 50, "top_k_confirm": 3, "top_k_combo": 2,
                  "max_objects": 4, "max_moves_per_object": 8, "max_episodes": 900},
}
DATA = Path(os.environ["CLEARANCE_DATA"])


def stage_clock():
    """progress() callback that records when each stage starts and ends."""
    t: dict[str, dict] = {}

    def cb(doc):
        now = time.time()
        for name, st in doc["stages"].items():
            rec = t.setdefault(name, {})
            if st["state"] == "running" and "start" not in rec:
                rec["start"] = now
            elif st["state"] not in ("pending", "running") and "end" not in rec and "start" in rec:
                rec["end"] = now
    return t, cb


def run_site(site: str) -> dict:
    from agents.orchestrator.run import load_catalog, run_analysis
    cfg = SITES[site]
    src = REPO / "sample_sites" / site
    dst = DATA / "sites" / site
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("analyses"))
    twin = Twin.model_validate(json.loads((src / "twin_v1" / "twin.json").read_text(encoding="utf-8")))
    robots = [r for r in load_catalog() if not cfg["robot_class"] or r.robot_class == cfg["robot_class"]]
    aid = cfg["analysis_id"]
    adir = DATA / "analyses" / aid
    if adir.exists():
        shutil.rmtree(adir)
    _clear_usage(aid)
    timings, cb = stage_clock()
    t0 = time.time()
    doc = run_analysis(aid, twin, robots, Task(sequence=cfg["task"]), cfg["business"], budget=BUDGET, progress=cb)
    wall = time.time() - t0
    batch_eps = sum(v["n_episodes"] for v in doc["batch_index"].values())
    bt = timings.get("batches", {})
    batch_s = (bt.get("end", 0) - bt.get("start", 0)) if bt.get("end") else None
    n_exp = sum(1 for inv in doc["investigations"].values() for e in inv.get("experiments", []) if e.get("batch_id"))
    n_find = [f for inv in doc["investigations"].values() for f in inv.get("findings", [])]
    inv_stats = {"robots": sorted(doc["investigations"]), "experiments_run": n_exp,
                 "probe_episodes": n_exp * BUDGET["investigation"]["probe_episodes"],
                 "findings": len(n_find), "findings_confirmed": sum(1 for f in n_find if f.get("confirmed_by_probe"))}
    record = {
        "analysis_id": aid, "site_id": site, "task": cfg["task"], "task_note": cfg["task_note"],
        "robot_class": cfg["robot_class"], "robots_considered": [r.robot_id for r in robots],
        "budget": BUDGET, "status": doc["status"], "wall_time_s": round(wall, 1),
        "stage_times_s": {k: round(v["end"] - v["start"], 1) for k, v in timings.items() if "end" in v},
        "batch_episodes": batch_eps, "batch_stage_s": None if batch_s is None else round(batch_s, 1),
        "episodes_per_min_batches": (round(batch_eps / batch_s * 60, 1) if batch_s else None),
        "investigation": inv_stats, "factcheck": (doc.get("report") or {}).get("factcheck"),
        "verdicts": {v["robot_id"]: {"label": v["label"], "main_reason": v.get("main_reason")}
                     for v in doc.get("verdicts", [])}, "workers": WORKERS, "cpu_count": os.cpu_count(),
        "machine": f"{platform.system()} {platform.release()}, {platform.processor()}",
        "llm_mode": os.environ.get("CLEARANCE_LLM_MODE"), "tavily_mode": os.environ.get("CLEARANCE_TAVILY_MODE"),
        "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    storage.write_json(f"analyses/{aid}/run.json", record)
    return record


def _clear_usage(aid: str) -> None:
    """Fresh usage rows for a re-run of the same analysis id."""
    import sqlite3
    from agents.llm.usage import db_path
    try:
        con = sqlite3.connect(str(db_path()))
        con.execute("DELETE FROM usage WHERE analysis_id = ?", (aid,))
        con.commit()
        con.close()
    except sqlite3.Error:
        pass


# ------------------------------------------------------------------------------------------------- export
def _pick_episodes(summary_path: Path) -> list[int]:
    """One success and one non-success episode when both exist, else the first two."""
    try:
        eps = json.loads(summary_path.read_text(encoding="utf-8"))["episodes"]
    except (OSError, ValueError, KeyError):
        return [0]
    idx = list(range(len(eps)))
    ok = [i for i in idx if eps[i].get("outcome") == "success"]
    bad = [i for i in idx if eps[i].get("outcome") != "success"]
    pick = ok[:1] + bad[:1]
    return pick or idx[:2]


def _filter_jsonl(src: Path, dst: Path, keep: list[int]) -> None:
    keep_set = set(keep)
    with src.open("rb") as f, dst.open("wb") as out:
        for raw in f:
            if raw.startswith(b'{"episode":'):
                n = int(raw[11:raw.index(b",", 11)])
                if n in keep_set:
                    out.write(raw)


def export_site(site: str) -> dict:
    cfg = SITES[site]
    aid = cfg["analysis_id"]
    src = DATA / "analyses" / aid
    out = REPO / "sample_sites" / site / "analyses" / aid
    if out.exists():
        shutil.rmtree(out)
    (out / "batches").mkdir(parents=True)
    doc = json.loads((src / "analysis.json").read_text(encoding="utf-8"))
    for name in ("facts.json", "citations.json", "report.md", "investigation.json", "run.json"):
        if (src / name).exists():
            shutil.copy2(src / name, out / name)
    if (src / "investigations").is_dir():
        shutil.copytree(src / "investigations", out / "investigations")
    report = doc.get("report") or {}
    md = (src / "report.md").read_text(encoding="utf-8") if (src / "report.md").exists() else ""
    (out / "report.json").write_text(json.dumps({"analysis_id": aid, "markdown": md,
                                                 "factcheck": report.get("factcheck")}, indent=2), encoding="utf-8")

    # Replay batch per robot: normal traffic, nominal geometry (else the first batch of that robot).
    by_robot: dict[str, list[str]] = {}
    for bid, meta in doc.get("batch_index", {}).items():
        by_robot.setdefault(meta["robot_id"], []).append(bid)
    for rid, conf in (doc.get("confirmation_batches") or {}).items():
        by_robot.setdefault(rid, []).append(conf["batch_id"])
    replay: dict[str, str] = {}
    for rid, bids in by_robot.items():
        pref = [b for b in bids if doc["batch_index"].get(b, {}).get("density") == "normal"
                and doc["batch_index"].get(b, {}).get("bound") == "nominal"]
        conf = (doc.get("confirmation_batches") or {}).get(rid)
        replay[rid] = (pref or ([conf["batch_id"]] if conf else []) or bids)[0]
    if src.joinpath("batches").is_dir():
        for p in sorted((src / "batches").glob("*.json")):
            if p.name.endswith(".summary.json"):
                continue
            bid = p.stem
            b = json.loads(p.read_text(encoding="utf-8"))
            if bid in replay.values():
                keep = _pick_episodes(src / "batches" / f"{bid}.summary.json")
                ep = src / "batches" / f"{bid}.episodes.jsonl"
                if ep.exists():
                    _filter_jsonl(ep, out / "batches" / f"{bid}.episodes.jsonl", keep)
                    b["replay_episodes"] = {"kept_episode_indexes": keep,
                                            "note": "replay subset; the full batch is summarized in results"}
                else:
                    b["episodes_uri"] = None
                bn = src / "batches" / f"{bid}.bottleneck.npy"
                if bn.exists():
                    a = np.load(bn).astype(np.float64)
                    mx = float(a.max()) if a.size else 0.0
                    q = np.zeros(a.shape, np.uint8) if mx <= 0 else np.round(a / mx * 255).astype(np.uint8)
                    np.save(out / "batches" / f"{bid}.bottleneck.npy", q)
                    b["pinch_map_scale"] = {"dtype": "uint8", "max_value": mx,
                                            "note": "Pinch Map stored as relative intensity 0-255"}
                else:
                    b["bottleneck_uri"] = None
            else:
                b["episodes_uri"] = None
                b["bottleneck_uri"] = None
            (out / "batches" / p.name).write_text(json.dumps(b, indent=2), encoding="utf-8")
    doc["replay_batches"] = replay
    (out / "analysis.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    return {"site": site, "analysis_id": aid, "bytes": size, "replay_batches": replay}


WEB_FIXTURES = REPO / "apps" / "web" / "src" / "offline" / "fixtures"


def _rle(a: np.ndarray) -> list[int]:
    flat = a.ravel()
    if flat.size == 0:
        return []
    cut = np.flatnonzero(np.diff(flat)) + 1
    starts = np.concatenate(([0], cut))
    lens = np.diff(np.concatenate((starts, [flat.size])))
    out = np.empty(2 * len(starts), np.int64)
    out[0::2], out[1::2] = flat[starts], lens
    return out.tolist()


def export_web(sites: list[str]) -> dict:
    """Serve sample_sites/ through services/api (in-process) and store its responses as the web app's offline-mode
    fixtures, so offline mode shows exactly what the API serves for the reference sites."""
    import io
    import tempfile

    from fastapi.testclient import TestClient
    from PIL import Image

    from services.api.main import create_app
    from services.api.settings import Settings

    from agents.llm.usage import summary as usage_summary
    run_usage = usage_summary()  # usage table of the recorded runs (CLEARANCE_DATA), read before the API starts
    WEB_FIXTURES.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="clearance_web_export_"))
    settings = Settings(data_dir=tmp / "data", sample_dir=REPO / "sample_sites", catalog_dir=REPO / "catalog" / "robots",
                        web_dist=tmp / "no_web_dist", infra_dir=tmp / "infra", secret="offline-export",
                        catalog_writeback=False, job_workers=1)
    sizes = {}
    with TestClient(create_app(settings)) as c:
        def get(path):
            r = c.get(path)
            r.raise_for_status()
            return r.json()

        all_sites = get("/sites")
        all_sites = all_sites["sites"] if isinstance(all_sites, dict) else all_sites
        index = {"generated_by": "scripts/run_reference_checks.py --web", "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "sites": [x for x in all_sites if x["site_id"] in sites], "robots": get("/catalog/robots?all=1"),
                 "usage": run_usage, "analyses": {}}
        for site in sites:
            sd = get(f"/sites/{site}")
            analyses = get(f"/sites/{site}/analyses")
            twin = get(f"/sites/{site}/twin")
            png = c.get(f"/sites/{site}/twin/occupancy.png")
            png.raise_for_status()
            occ = np.array(Image.open(io.BytesIO(png.content)).convert("L"))
            out = {"site": sd, "site_analyses": analyses, "twin": twin,
                   "occupancy": {"width": int(occ.shape[1]), "height": int(occ.shape[0]),
                                 "res": twin["occupancy"]["resolution_m"], "origin": twin["occupancy"]["origin_xy"],
                                 "rle": _rle(occ)},
                   "analyses": {}}
            for a in analyses if isinstance(analyses, list) else analyses.get("analyses", []):
                aid = a["analysis_id"]
                doc = get(f"/analyses/{aid}")
                adoc = json.loads((REPO / "sample_sites" / site / "analyses" / aid / "analysis.json").read_text("utf-8"))
                bundle = {"doc": doc, "report": get(f"/analyses/{aid}/report"), "facts": get(f"/analyses/{aid}/facts"),
                          "investigation": c.get(f"/analyses/{aid}/investigation").json()
                          if c.get(f"/analyses/{aid}/investigation").status_code == 200 else None,
                          "batches": {}, "episodes": {}, "pinch": {}}
                bids = list(adoc.get("batch_index", {})) + [x["batch_id"] for x in
                                                             (adoc.get("confirmation_batches") or {}).values()]
                for bid in dict.fromkeys(bids):
                    r = c.get(f"/sim/batches/{bid}")
                    if r.status_code != 200:
                        continue
                    bundle["batches"][bid] = r.json()
                for bid in dict.fromkeys((adoc.get("replay_batches") or {}).values()):
                    eps = []
                    for n in range(1):  # one replay episode per robot keeps the web bundle small
                        r = c.get(f"/sim/batches/{bid}/episodes/{n}")
                        if r.status_code != 200:
                            break
                        eps.append(r.json())
                    bundle["episodes"][bid] = eps
                    r = c.get(f"/sim/batches/{bid}/bottleneck.npy")
                    if r.status_code == 200:
                        arr = np.load(io.BytesIO(r.content))
                        rr, cc = np.nonzero(arr)
                        bundle["pinch"][bid] = {"shape": list(arr.shape), "cells": np.stack(
                            [rr, cc, arr[rr, cc].astype(np.int64)], 1).ravel().tolist()}
                out["analyses"][aid] = bundle
                index["analyses"][aid] = site
            p = WEB_FIXTURES / f"{site}.json"
            p.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
            sizes[p.name] = p.stat().st_size
    p = WEB_FIXTURES / "index.json"
    p.write_text(json.dumps(index, indent=1), encoding="utf-8")
    sizes[p.name] = p.stat().st_size
    shutil.rmtree(tmp, ignore_errors=True)
    return sizes


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sites", nargs="*", default=list(SITES))
    ap.add_argument("--export-only", action="store_true")
    ap.add_argument("--no-export", action="store_true")
    ap.add_argument("--web", action="store_true", help="also write the web offline-mode fixtures")
    args = ap.parse_args(argv)
    print(f"CLEARANCE_DATA={DATA}  workers={WORKERS}")
    for site in args.sites:
        if not args.export_only:
            rec = run_site(site)
            print(json.dumps({k: rec[k] for k in ("analysis_id", "status", "wall_time_s", "stage_times_s",
                                                   "batch_episodes", "episodes_per_min_batches")}))
        if not args.no_export:
            print(json.dumps(export_site(site)))
    if args.web:
        print(json.dumps(export_web(args.sites)))


if __name__ == "__main__":
    main()
