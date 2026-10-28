"""J4 entry point: `extract_semantics(capture_dir, twin) -> Twin` (PLAN 7.3).

keyframes -> detector (Grounding DINO + SAM 2, or geometry-based) -> 3D objects -> primitives
(source="auto", with confidence) -> detector hazards + vision-LLM hazards -> new Twin.

CLI: python -m jobs.semantics.run --capture <dir> --twin <storage key of twin.json> [--out-key ...]
"""
from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path
from typing import Optional, Union

import numpy as np

from clearance_core import grid as gridio
from clearance_core import storage
from clearance_core.grid import UNKNOWN, Grid
from clearance_core.schemas import Hazard, Primitive, Twin
from jobs.semantics.capture_io import (cloud_from_depth, load_capture, load_feature_points, load_poses,
                                       load_twin_cloud)
from jobs.semantics.detectors import Detector, GeometricDetector, Object3D, SceneContext, choose_detector
from jobs.semantics.hazards import HazardPassResult, run_hazard_pass
from jobs.semantics.keyframes import select_keyframes
from jobs.semantics.primitives import (HAZARD_ONLY_LABELS, fit_primitive, footprint_iou, next_ids,
                                       normalize_label)

log = logging.getLogger(__name__)
AUTO_HAZARD_SOURCES = ("vision_llm",)


def grid_bounds(grid: Grid) -> Optional[tuple[float, float, float, float]]:
    known = grid.data != UNKNOWN
    if not known.any():
        return None
    rows = np.flatnonzero(known.any(axis=1))
    cols = np.flatnonzero(known.any(axis=0))
    x0, y1 = grid.cell_to_world(rows[0], cols[0])
    x1, y0 = grid.cell_to_world(rows[-1], cols[-1])
    return float(x0), float(x1), float(y0), float(y1)


def _load_grid(twin: Twin) -> Optional[Grid]:
    try:
        return gridio.load(twin.occupancy)
    except Exception as e:
        log.warning("occupancy grid not loadable (%s)", e)
        return None


def objects_to_primitives(objects: list[Object3D], taken_ids, *, min_size_m: float = 0.08):
    """Fit primitives; returns (primitives, detector_hazards)."""
    prims: list[Primitive] = []
    hazards: list[Hazard] = []
    fit_objs = []
    for o in objects:
        label = normalize_label(o.label)
        if label in HAZARD_ONLY_LABELS:
            c = o.points.mean(axis=0)
            hazards.append(Hazard(label=HAZARD_ONLY_LABELS[label], xy=(float(c[0]), float(c[1])),
                                  source=f"detector:{o.backend or 'auto'}", confidence=round(float(o.score), 3),
                                  frame_refs=list(o.frames)))
        else:
            fit_objs.append(o)
    ids = next_ids([o.label for o in fit_objs], taken_ids)
    for o, pid in zip(fit_objs, ids):
        if o.n_views > 0:  # image backends: more views, more trust
            conf = o.score * (0.5 + 0.5 * min(1.0, o.n_views / 3.0))
        else:
            conf = o.score
        p = fit_primitive(o.points, o.label, prim_id=pid, confidence=round(float(np.clip(conf, 0.01, 0.99)), 3),
                          shape=o.shape, height=o.height, denoise=o.n_views > 0)
        if max(p.size[0], p.size[1]) < min_size_m:
            continue
        prims.append(p)
    # Same physical object detected under two labels: keep the more confident one.
    prims.sort(key=lambda p: -p.confidence)
    kept: list[Primitive] = []
    for p in prims:
        if all(footprint_iou(p, k) < 0.5 for k in kept):
            kept.append(p)
    return kept, hazards


def merge_into_twin(twin: Twin, new_prims: list[Primitive], new_hazards: list[Hazard],
                    replace_auto: bool = True, overlap_iou: float = 0.3) -> tuple[list[Primitive], list[Hazard]]:
    base = [p for p in twin.primitives if not (replace_auto and p.source == "auto")]
    prims = list(base)
    for p in new_prims:
        if all(footprint_iou(p, b) < overlap_iou for b in base):
            prims.append(p)
    keep_h = [h for h in twin.hazards
              if not (replace_auto and (h.source in AUTO_HAZARD_SOURCES or h.source.startswith("detector:")))]
    return prims, keep_h + new_hazards


def extract_semantics(capture_dir: str, twin: Twin, *, detector: Union[None, str, Detector] = None,
                      llm=None, n_keyframes: int = 40, n_hazard_frames: int = 10, hazards: bool = True,
                      replace_auto: bool = True, out_prefix: Optional[str] = None) -> Twin:
    t0 = time.time()
    cdir = Path(capture_dir)
    capture = load_capture(cdir)
    poses = load_poses(cdir, capture)
    grid = _load_grid(twin)
    bounds = grid_bounds(grid) if grid is not None else None
    sel = select_keyframes(poses, k=n_keyframes, bounds=bounds) if poses else None
    keyframes = sel.frames if sel else []

    cloud = load_twin_cloud(twin)
    cloud_src = "twin" if cloud is not None else None
    if cloud is None and keyframes:
        cloud = cloud_from_depth(cdir, capture, keyframes)
        cloud_src = "depth" if cloud is not None else None
    if cloud is None:
        cloud = load_feature_points(cdir, capture)
        cloud_src = "feature_points" if cloud is not None else None

    ctx = SceneContext(capture_dir=cdir, capture=capture, poses=poses, keyframes=keyframes, grid=grid,
                       cloud=cloud, height_band=tuple(twin.occupancy.height_band_m))
    det, why = choose_detector(detector, ctx)
    ok, cant = det.can_run(ctx)
    notes = [why]
    if not ok and not isinstance(det, GeometricDetector):
        notes.append(f"{det.name} cannot run: {cant}; using geometry-based detector")
        det = GeometricDetector()
        ok, cant = det.can_run(ctx)
    try:
        if not ok:
            notes.append(f"{det.name} cannot run: {cant}; no objects extracted")
            objects = []
        else:
            objects = det.detect(ctx)
    except Exception as e:
        if isinstance(det, GeometricDetector) or (detector not in (None, "auto")):
            raise
        log.exception("detector %s failed; using geometry-based detector", det.name)
        notes.append(f"{det.name} failed ({e}); used geometry-based detector")
        det = GeometricDetector()
        objects = det.detect(ctx)

    new_prims, det_hazards = objects_to_primitives(objects, [p.id for p in twin.primitives])
    prims, kept_hazards = merge_into_twin(twin, new_prims, det_hazards, replace_auto=replace_auto)

    hz = HazardPassResult(status="skipped")
    if hazards and keyframes:
        hz = run_hazard_pass(keyframes, ctx.read_frame, prims, llm=llm, n_frames=n_hazard_frames)
        kept_hazards = kept_hazards + hz.hazards

    assets = dict(twin.assets)
    if out_prefix:
        key = out_prefix.rstrip("/") + "/semantics.json"
        storage.write_json(key, {
            "capture_id": capture.capture_id, "detector": det.name, "notes": notes,
            "keyframes": [f.frame for f in keyframes],
            "keyframe_coverage": {"probe": sel.probe_coverage, "element": sel.element_coverage} if sel else None,
            "cloud_source": cloud_src, "n_objects": len(objects),
            "primitives_added": [p.id for p in new_prims if p in prims],
            "hazard_pass": {"status": hz.status, "frames_sent": hz.frames_sent, "violations": hz.violations,
                            "dropped": hz.dropped, "n_hazards": len(hz.hazards)},
            "wall_time_s": round(time.time() - t0, 3)})
        assets["semantics_report"] = key
    log.info("semantics: %d primitives (%d new), %d hazards via %s in %.1fs", len(prims), len(new_prims),
             len(kept_hazards), det.name, time.time() - t0)
    return twin.model_copy(update={"primitives": prims, "hazards": kept_hazards, "assets": assets}, deep=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="J4: semantics + primitives + hazards for a twin")
    ap.add_argument("--capture", required=True, help="scanner capture directory")
    ap.add_argument("--twin", required=True, help="storage key of twin.json (relative to CLEARANCE_DATA)")
    ap.add_argument("--out-key", default=None, help="storage key to write the updated twin (default: overwrite)")
    ap.add_argument("--detector", default=None, choices=["auto", "gdino_sam2", "geometric"])
    ap.add_argument("--no-hazards", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    twin = Twin.model_validate(storage.read_json(a.twin))
    prefix = str(Path(a.twin).parent).replace("\\", "/")
    out = extract_semantics(a.capture, twin, detector=a.detector, hazards=not a.no_hazards, out_prefix=prefix)
    storage.write_json(a.out_key or a.twin, out)
    print(f"{len(out.primitives)} primitives, {len(out.hazards)} hazards -> {a.out_key or a.twin}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
