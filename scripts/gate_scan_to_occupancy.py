"""Scan-to-occupancy gate: scan -> occupancy accuracy (docs/GATE.md).

Reference-room accuracy run (no hardware):
    python scripts/gate_scan_to_occupancy.py                      # cafe + storeroom, T1 + T2, seeds 0 1
    python scripts/gate_scan_to_occupancy.py --rooms cafe --tiers T1_LIDAR --seeds 0 --no-doc

On-site scan run (real iPhone):
    python scripts/gate_scan_to_occupancy.py --stray <StrayScanner export dir> --tape tape.json
    python scripts/gate_scan_to_occupancy.py --capture <Capture dir> --tape tape.json
tape.json: [{"name": "aisle 1", "a_xy": [x, y], "b_xy": [x, y], "value_m": 0.812}, ...] with a/b read
approximately off <out>/occ_annotated.png (0.5 m grid, labelled in metres); the measured value is
the free run through the segment midpoint, so endpoints only need to straddle the gap.
Generated data goes to ~/.cache/clearance/gate (override with --out).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
DEFAULT_OUT = Path(os.environ.get("CLEARANCE_GATE_OUT", Path.home() / ".cache" / "clearance" / "gate"))
os.environ.setdefault("CLEARANCE_DATA", str(DEFAULT_OUT / "data"))
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMBA_NUM_THREADS"):
    os.environ.setdefault(_v, "2")

import numpy as np  # noqa: E402

GO = {"T1_LIDAR": 0.03, "T2_VIO_MONO": 0.05}
AISLE_TOL = 0.05


def _products(twin) -> dict:
    """Grid, thresholds and stats of a twin written by build_twin_from_capture."""
    from clearance_core import grid as cgrid
    from clearance_core import storage

    rep = storage.read_json(twin.occupancy.uri.rsplit("/", 1)[0] + "/recon_report.json")
    stats = {k: v for k, v in rep.items() if k not in ("thresholds", "floor_align")}
    return {"grid": cgrid.load(twin.occupancy), "thresholds": rep["thresholds"], "stats": stats}


def run_one(job: dict) -> dict:
    from jobs.recon.measure import aisle_passable, evaluate_grid
    from jobs.recon.pipeline import build_twin_from_capture
    from jobs.reference.render import render_capture
    from jobs.reference.rooms import make_room

    kind, seed, tier, out = job["kind"], job["seed"], job["tier"], Path(job["out"])
    res = {"kind": kind, "seed": seed, "tier": tier, "ok": False, "holdout": bool(job.get("holdout"))}
    try:
        t0 = time.perf_counter()
        room = make_room(kind, seed)
        cap_dir = out / "captures" / f"{kind}_s{seed}_{tier}"
        if not (cap_dir / "capture.json").is_file() or job.get("rerender"):
            render_capture(room, str(cap_dir), tier, seed)
        meta = json.loads((cap_dir / "render_meta.json").read_text(encoding="utf-8"))
        res["render_s"] = meta.get("render_s", round(time.perf_counter() - t0, 1))
        res.update(n_frames=meta["n_frames"], path_m=meta["path_length_m"], final_drift_m=meta["final_drift_m"])

        t1 = time.perf_counter()
        site_id = f"gate_{kind}_s{seed}_{tier}"
        twin = build_twin_from_capture(str(cap_dir), site_id, f"sites/{site_id}/twin_v1/")
        res["recon_s"] = round(time.perf_counter() - t1, 1)
        r = _products(twin)
        grid = r["grid"]
        ev = evaluate_grid(grid, room.gt_measurements)
        res.update(median=ev["median_abs_err_m"], p90=ev["p90_abs_err_m"], max=ev["max_abs_err_m"], rows=ev["rows"],
                   stats=r["stats"])
        if "aisle_080" in room.notes:
            a = room.notes["aisle_080"]
            res["aisle_080"] = aisle_passable(grid, a["p0"], a["p1"], a["width_m"], tol_m=AISLE_TOL)
        if room.twin.thresholds:
            gt = room.twin.thresholds[0]
            gmid = np.mean(np.asarray(gt.segment), axis=0)
            near = [t for t in r["thresholds"] if np.linalg.norm(np.mean(np.asarray(t["segment"]), axis=0) - gmid) < 0.4]
            res["threshold"] = {"gt_height_m": gt.height_m, "detected": bool(near),
                                "height_m": near[0]["height_m"] if near else None,
                                "status": near[0]["status"] if near else None,
                                "floor_noise_m": r["stats"]["floor_noise_m"],
                                "other_candidates": len(r["thresholds"]) - len(near)}
        res["ok"] = True
    except Exception as e:  # report, do not hide
        res["error"] = f"{type(e).__name__}: {e}"
        res["traceback"] = traceback.format_exc()
    return res


def pooled(results, tier, holdout=False):
    errs = [abs(row["err_m"]) for r in results if r["tier"] == tier and r["ok"] and r.get("holdout", False) == holdout
            for row in r["rows"]]
    if not errs:
        return None
    return {"n": len(errs), "median": round(float(np.median(errs)), 4), "p90": round(float(np.percentile(errs, 90)), 4),
            "max": round(float(np.max(errs)), 4)}


def verdict(results):
    checks = []
    checks.append(("pipeline ran end to end on every run (no manual file handling)", all(r["ok"] for r in results)))
    results = [r for r in results if not r.get("holdout")]
    for tier, lim in GO.items():
        p = pooled(results, tier)
        if p is not None:
            checks.append((f"{tier} pooled median abs error {p['median'] * 100:.1f} cm <= {lim * 100:.0f} cm",
                           p["median"] <= lim))
    for r in results:
        if "aisle_080" in r:
            a = r["aisle_080"]
            checks.append((f"0.80 m aisle passable within +-5 cm ({r['kind']} s{r['seed']} {r['tier']}: median "
                           f"{a['median_width_m']:.3f} m, worst error {a['max_abs_err_m'] * 100:.1f} cm, centerline free "
                           f"{a['centerline_free']})", a["passable"]))
    return all(ok for _, ok in checks), checks


def print_table(results):
    print(f"\n{'room':10s} {'seed':>4s} {'tier':12s} {'frames':>6s} {'render s':>8s} {'recon s':>7s} {'median cm':>9s} "
          f"{'p90 cm':>7s} {'max cm':>7s}")
    for r in results:
        if not r["ok"]:
            print(f"{r['kind']:10s} {r['seed']:4d} {r['tier']:12s} FAILED: {r.get('error')}")
            continue
        print(f"{r['kind'] + ('*' if r.get('holdout') else ''):10s} {r['seed']:4d} {r['tier']:12s} {r['n_frames']:6d} {r['render_s']:8.1f} {r['recon_s']:7.1f} "
              f"{r['median'] * 100:9.1f} {r['p90'] * 100:7.1f} {r['max'] * 100:7.1f}")


def write_doc(results, go, checks, path: Path, wall_s: float):
    from jobs.recon.pipeline import PARAMS
    from jobs.reference.render import DEFAULT_NOISE

    L = []
    L.append("# Scan gate: scan to occupancy\n")
    L.append(f"Generated by `scripts/gate_scan_to_occupancy.py` on {time.strftime('%Y-%m-%d %H:%M')} "
             f"(wall time {wall_s / 60:.1f} min, CPU only).\n")
    L.append("## Verdict\n")
    L.append(f"**Reference-room accuracy run (software half): {'GO' if go else 'NO-GO'}.** "
             "The on-site scan run (hardware half) is still open; the overall gate needs both. "
             "Protocol below.\n")
    for text, ok in checks:
        L.append(f"- [{'x' if ok else ' '}] {text}")
    L.append("")
    L.append("## Pooled error per tier\n")
    L.append("Gate set = cafe + storeroom (pipeline parameters were tuned while looking at these rooms). "
             "Held-out set = rooms never used for tuning; reported for honesty, not part of the verdict.\n")
    L.append("| set | tier | n distances | median abs err | p90 abs err | max abs err | GO limit (median) |")
    L.append("|---|---|---|---|---|---|---|")
    for hold in (False, True):
        for tier, lim in GO.items():
            p = pooled(results, tier, hold)
            if p:
                L.append(f"| {'held-out' if hold else 'gate'} | {tier} | {p['n']} | {p['median'] * 100:.1f} cm | "
                         f"{p['p90'] * 100:.1f} cm | {p['max'] * 100:.1f} cm | {lim * 100:.0f} cm |")
    L.append("")
    L.append("## Per run\n")
    L.append("| room | seed | tier | keyframes | path m | render s | recon s | median | p90 | max | 0.80 m aisle | door lip |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        if not r["ok"]:
            L.append(f"| {r['kind']} | {r['seed']} | {r['tier']} | failed: {r.get('error', '')} |||||||||")
            continue
        aisle = "-"
        if "aisle_080" in r:
            a = r["aisle_080"]
            aisle = f"{a['median_width_m']:.3f} m ({'passable' if a['passable'] else 'FAIL'})"
        lip = "-"
        if "threshold" in r:
            t = r["threshold"]
            lip = (f"{t['height_m'] * 100:.1f} cm, {t['status']}" if t["detected"] else "not detected") + \
                  f" (GT {t['gt_height_m'] * 100:.0f} cm)"
        L.append(f"| {r['kind'] + (' (held-out)' if r.get('holdout') else '')} | {r['seed']} | {r['tier']} | {r['n_frames']} | {r['path_m']:.0f} | {r['render_s']:.0f} | "
                 f"{r['recon_s']:.0f} | {r['median'] * 100:.1f} cm | {r['p90'] * 100:.1f} cm | {r['max'] * 100:.1f} cm | "
                 f"{aisle} | {lip} |")
    L.append("")
    L.append("Seed 0 rooms are square to the site frame with a sub-cell offset; seed 1 rooms are rotated 8-25 degrees, "
             "so walls do not follow grid rows. Errors are measured on the 2.5 cm occupancy grid with sub-cell "
             "interpolation; unknown cells count as blocked, exactly as the simulator treats them.\n")
    L.append("### Largest errors\n")
    worst = sorted(((abs(row["err_m"]), r, row) for r in results if r["ok"] for row in r["rows"]), key=lambda x: -x[0])[:8]
    L.append("| run | distance | ground truth | measured | error |")
    L.append("|---|---|---|---|---|")
    for e, r, row in worst:
        L.append(f"| {r['kind']} s{r['seed']} {r['tier']} | {row['name']} | {row['gt_m']:.3f} m | {row['measured_m']:.3f} m "
                 f"| {row['err_m'] * 100:+.1f} cm |")
    L.append("")
    L.append(SCOPE_TEXT.format(t1=json.dumps(PARAMS["T1_LIDAR"]), t2=json.dumps(PARAMS["T2_VIO_MONO"]),
                               noise=json.dumps(DEFAULT_NOISE)))
    L.append(PROTOCOL_TEXT)
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


SCOPE_TEXT = """## What the reference-room run does and does not show

How each capture is made (`jobs/reference/render.py`): a hand-held walk at about 1.4 m camera height, 0.5 m/s,
with the heading swept +-65 degrees and the camera pitched 20-38 degrees down. Keyframes every 10 cm or 10 degrees.
Depth is ray-cast against the exact room geometry, then degraded:

- T1 (LiDAR): 1 % depth-proportional noise, 0.3 % smooth per-frame field, edge dropout and mixed pixels, medium
  confidence beyond 3.5 m, nothing beyond 5 m.
- T2 (no LiDAR): no metric depth. Relative inverse depth with a smooth per-frame shape error (4.5 % std), 1.5 %
  pixel noise and edge blur, then a random per-frame affine map; 100-300 metric feature points per frame with 2 cm
  noise and 5 % outliers.
- Both: pose drift (random walk, about 1 cm and 0.06 degrees per 10 m), a 0.4 degree world tilt plus 1.5 cm height
  offset that the floor fit must remove, and two bursts of `limited` tracking frames with 3 cm / 1.5 degree pose errors
  (dropped by the pipeline).

Full noise parameters: `{noise}`

Pipeline parameters: T1 `{t1}`; T2 `{t2}`.

Scope of the conclusion:

- The T2 result rests on an error model for the relative-depth network. Real Depth Anything V2 Small errors are
  not independent between frames and are not limited to smooth fields (textureless walls, glass, dark floors).
  The per-frame polynomial scale correction (`local_degree=3`) fits smooth errors well by construction, so the T2
  number here is an upper bound on what to expect. **VERIFY on the on-site scan run with the real model.**
- Feature points here are spread uniformly over visible surfaces; ARKit produces them only on textured surfaces.
- Occlusion shadows are real effects: floor hidden behind a cart or a chair reads as unknown, which the simulator
  treats as blocked. These show up as the largest errors (for example cart-to-wall and the 20 cm chair gaps).
- Door lips of 2-3 cm are near the T2 noise floor; the pipeline reports them with status `flagged_manual` when
  their height is under twice the floor-height noise, so the user measures them by hand.
- The reconstruction uses ray-based TSDF fusion with free-space counting in numpy/numba (no Open3D), 2.5 cm cells.
"""

PROTOCOL_TEXT = """## On-site scan run (hardware half): protocol

Goal: one real room, scanned with an iPhone, turned into an occupancy grid without manual file handling, and 10
tape-measured distances compared against it. Budget: about 1 hour.

### What you need
1. A LiDAR iPhone/iPad Pro with the free **Stray Scanner** app (exports depth + poses), or our own iOS capture app
   once it exists. A non-LiDAR iPhone (for example iPhone 13) is T2: it needs ARKit raw feature points, which Stray
   Scanner does not export (VERIFY whether it records on non-LiDAR devices at all), so T2 hardware testing waits for our app.
2. A printed AprilTag tag36h11 id 0 (generate with `python -c "import cv2; cv2.imwrite('tag0.png', cv2.aruco.generateImageMarker(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11), 0, 1000))"`),
   printed at 100 % so the black square is 16.0 cm. Measure the printed black square with a ruler and use that value
   (`--marker-size`). Tape it flat on the floor near the entrance.
3. A tape measure (or laser measure) and a notebook.

### Scan
1. Clear people out of the room if possible. Turn on all lights.
2. Start Stray Scanner, begin recording while pointing at the marker from about 1 m for 2-3 seconds.
3. Walk slowly (about half walking speed) holding the phone at chest height, tilted down about 30 degrees.
   Walk every aisle in both directions, sweep left and right, and finish back at the marker. 3-6 minutes for 50 m2.
4. Stop recording. Export the dataset (Files app / AirDrop / USB) to the laptop, e.g. `~/.cache/clearance/onsite/<dataset>`.

### Tape measurements (10 or more)
Measure free gaps between obstacle faces at floor-to-hip height, like a robot would see them: aisle widths (3 places
along the narrowest aisle), gaps between tables or chairs, door widths (jamb to jamb), shelf-to-wall gaps and one
full room width. Note each value to the millimetre and sketch where it was taken.

### Run
```
set PYTHONPATH=D:\\NVIDIA
python scripts/gate_scan_to_occupancy.py --stray ~/.cache/clearance/onsite/<dataset> --marker-size 0.160
```
This imports the export, builds the twin, and writes `occ.png` and `occ_annotated.png` (0.5 m grid in site
coordinates; the marker is the origin) next to the imported capture. Open `occ_annotated.png`, read approximate
endpoints for each tape distance, and write `tape.json`:
```
[{"name": "aisle A x3", "a_xy": [2.10, 3.40], "b_xy": [2.10, 4.25], "value_m": 0.812}, ...]
```
Then run again with `--tape tape.json`. The script prints the per-distance error, median and p90, and appends the
result to `docs/GATE.md` under "On-site scan run results".

### Decide
GO if the pipeline ran without manual file handling and the T1 median error is at most 3 cm (T2: 5 cm) and the
narrowest aisle reads passable at the taped width +-5 cm. Otherwise NO-GO: the twin editor becomes the primary
geometry source (trace over the scan, tape-entered widths) and the scan is used for visuals only.
"""


def onsite(args):
    import cv2
    from jobs.common.imageio import imwrite

    from clearance_core.schemas import Marker
    from jobs.recon.importers import import_stray_scanner
    from jobs.recon.measure import evaluate_grid
    from jobs.recon.pipeline import build_twin_from_capture

    out = Path(args.out)
    if args.stray:
        src = Path(args.stray)
        cap_dir = out / "onsite" / f"{src.name}_capture"
        t0 = time.perf_counter()
        import_stray_scanner(src, cap_dir, site_id="onsite", marker=Marker(size_m=args.marker_size))
        print(f"imported {src} -> {cap_dir} in {time.perf_counter() - t0:.1f} s")
        imp = json.loads((cap_dir / "capture.json").read_text(encoding="utf-8"))["import"]
        print(json.dumps(imp, indent=2))
        if not imp.get("anchored_to_marker"):
            print("WARNING: marker not anchored; the site origin in occ_annotated.png is the ARKit session start, "
                  "not the marker")
    else:
        cap_dir = Path(args.capture)
    t1 = time.perf_counter()
    r = _products(build_twin_from_capture(str(cap_dir), "onsite", "sites/onsite/twin_v1/"))
    print(f"reconstructed in {time.perf_counter() - t1:.1f} s: {json.dumps(r['stats']['timings'])}")
    for w in r["stats"].get("warnings", []):
        print("WARNING:", w)
    for reg in r["stats"].get("unknown_regions", [])[:10]:
        print(f"unseen floor (rescan): {reg['area_m2']:.2f} m2 around {reg['centroid_xy']}")
    g = r["grid"]
    imwrite(cap_dir / "occ.png", g.data)
    vis = cv2.cvtColor(g.data, cv2.COLOR_GRAY2BGR)
    vis = cv2.resize(vis, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
    h, w = g.shape
    x0, y0 = g.origin
    for xv in np.arange(np.ceil(x0 / 0.5) * 0.5, x0 + w * g.res, 0.5):
        c = int((xv - x0) / g.res * 2)
        cv2.line(vis, (c, 0), (c, 2 * h), (200, 120, 0) if abs(xv % 1) > 1e-6 else (255, 0, 0), 1)
        if abs(xv % 1) < 1e-6:
            cv2.putText(vis, f"{xv:.0f}", (c + 2, 12), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (255, 0, 0), 1)
    for yv in np.arange(np.ceil(y0 / 0.5) * 0.5, y0 + h * g.res, 0.5):
        rr = int((h - (yv - y0) / g.res) * 2)
        cv2.line(vis, (0, rr), (2 * w, rr), (200, 120, 0) if abs(yv % 1) > 1e-6 else (255, 0, 0), 1)
        if abs(yv % 1) < 1e-6:
            cv2.putText(vis, f"{yv:.0f}", (2, rr - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (255, 0, 0), 1)
    cv2.circle(vis, (int(-x0 / g.res * 2), int((h + y0 / g.res) * 2)), 5, (0, 0, 255), 2)
    imwrite(cap_dir / "occ_annotated.png", vis)
    print(f"wrote {cap_dir / 'occ_annotated.png'} (red circle = site origin / marker)")
    print("thresholds:", json.dumps(r["thresholds"]))
    if args.tape:
        tape = json.loads(Path(args.tape).read_text(encoding="utf-8"))
        ev = evaluate_grid(g, tape)
        for row in ev["rows"]:
            print(f"  {row['name']:30s} tape {row['gt_m']:.3f}  twin {row['measured_m']:.3f}  err {row['err_m'] * 100:+.1f} cm")
        print(f"median {ev['median_abs_err_m'] * 100:.1f} cm, p90 {ev['p90_abs_err_m'] * 100:.1f} cm, n={ev['n']}")
        tier = r["stats"]["tier"]
        ok = ev["median_abs_err_m"] <= GO.get(tier, 0.03)
        doc = REPO / "docs" / "GATE.md"
        lines = ["", "## On-site scan run results", "",
                 f"{time.strftime('%Y-%m-%d %H:%M')}, capture `{cap_dir}`, tier {tier}, {r['stats']['n_depth_frames']} frames, "
                 f"recon {r['stats']['timings']['total_s']} s.", "",
                 f"Median abs error {ev['median_abs_err_m'] * 100:.1f} cm, p90 {ev['p90_abs_err_m'] * 100:.1f} cm over "
                 f"{ev['n']} tape distances: **{'GO' if ok else 'NO-GO'}** on accuracy.", "",
                 "| distance | tape | twin | error |", "|---|---|---|---|"]
        lines += [f"| {row['name']} | {row['gt_m']:.3f} m | {row['measured_m']:.3f} m | {row['err_m'] * 100:+.1f} cm |"
                  for row in ev["rows"]]
        if args.doc:
            with open(doc, "a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            print(f"appended to {doc}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rooms", nargs="+", default=["cafe", "storeroom"])
    ap.add_argument("--holdout", nargs="*", default=["corridor"], help="rooms reported but not used for the verdict")
    ap.add_argument("--tiers", nargs="+", default=["T1_LIDAR", "T2_VIO_MONO"])
    ap.add_argument("--seeds", nargs="+", type=int, default=[0, 1])
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--rerender", action="store_true")
    ap.add_argument("--no-doc", dest="doc", action="store_false")
    ap.add_argument("--stray", help="Stray Scanner export directory (on-site scan run)")
    ap.add_argument("--capture", help="Capture directory in our layout (on-site scan run)")
    ap.add_argument("--tape", help="tape.json with measured distances (on-site scan run)")
    ap.add_argument("--marker-size", type=float, default=0.160)
    args = ap.parse_args()
    if args.stray or args.capture:
        return onsite(args)

    t0 = time.perf_counter()
    jobs = [{"kind": k, "seed": s, "tier": t, "out": args.out, "rerender": args.rerender, "holdout": k in args.holdout}
            for k in list(args.rooms) + list(args.holdout) for s in args.seeds for t in args.tiers]
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        results = list(ex.map(run_one, jobs))
    wall = time.perf_counter() - t0
    for r in results:
        if not r["ok"]:
            print(r["traceback"], file=sys.stderr)
    print_table(results)
    for tier in GO:
        p = pooled(results, tier)
        ph = pooled(results, tier, True)
        if ph:
            print(f"held-out {tier}: median {ph['median'] * 100:.1f} cm  p90 {ph['p90'] * 100:.1f} cm (n={ph['n']})")
        if p:
            print(f"pooled {tier}: median {p['median'] * 100:.1f} cm  p90 {p['p90'] * 100:.1f} cm  max {p['max'] * 100:.1f} cm (n={p['n']})")
    go, checks = verdict(results)
    for text, ok in checks:
        print(("PASS " if ok else "FAIL ") + text)
    print(f"\nReference-room accuracy run: {'GO' if go else 'NO-GO'}  (wall {wall:.0f} s)")
    (Path(args.out) / "gate_results.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    if args.doc:
        write_doc(results, go, checks, REPO / "docs" / "GATE.md", wall)
        print(f"wrote {REPO / 'docs' / 'GATE.md'}")
    return 0 if go else 1


if __name__ == "__main__":
    sys.exit(main())
