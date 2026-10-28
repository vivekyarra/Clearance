"""J5 entry point: `build_traffic(observer_capture_dirs, twin) -> TrafficModel` (PLAN 7.5).

per observer: frames -> person detector -> ByteTrack-style tracker -> foot point -> floor (homography)
all observers: shared clock (Capture.started_at) -> cross-camera merge -> zones, OD pairs, arrival
rates, walking speeds, 15-min heatmaps, quiet/normal/peak -> TrafficModel(source="observed").
If nothing usable is observed, `build_user_estimated_traffic` provides the labeled fallback.

CLI: python -m jobs.traffic.run --twin <twin.json key> --observer <dir> [--observer <dir> ...]
"""
from __future__ import annotations

import argparse
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence, Union

import numpy as np

from clearance_core import grid as gridio
from clearance_core import storage
from clearance_core.grid import UNKNOWN, Grid
from clearance_core.schemas import TrafficModel, TrafficZone, Twin
from jobs.semantics.capture_io import load_capture
from jobs.traffic.analytics import compute_stats, heatmaps, zones_from_waypoints
from jobs.traffic.detect import PersonDetector, make_person_detector
from jobs.traffic.floor import FloorTrack, image_track_to_floor, merge_tracks
from jobs.traffic.homography import ObserverCamera
from jobs.traffic.tracker import ByteTracker
from jobs.traffic.video import capture_start_epoch, iter_capture_frames

log = logging.getLogger(__name__)


@dataclass
class ObserverResult:
    capture_id: str
    n_frames: int = 0
    n_detections: int = 0
    n_image_tracks: int = 0
    floor_tracks: list[FloorTrack] = field(default_factory=list)
    t_span: tuple[float, float] = (0.0, 0.0)
    error: Optional[str] = None


def _grid_for(twin: Twin) -> Grid:
    try:
        return gridio.load(twin.occupancy)
    except Exception:
        occ = twin.occupancy
        if occ.width <= 0 or occ.height <= 0:
            raise ValueError("twin occupancy grid is not loadable and has no size")
        return Grid(np.full((occ.height, occ.width), UNKNOWN, np.uint8), occ.resolution_m, tuple(occ.origin_xy))


def process_observer(capture_dir: str | Path, *, grid: Optional[Grid], detector: PersonDetector,
                     target_fps: float = 8.0, clock_zero: Optional[float] = None,
                     track_buffer_s: float = 3.0, min_track_s: float = 1.0) -> ObserverResult:
    d = Path(capture_dir)
    capture = load_capture(d)
    res = ObserverResult(capture.capture_id)
    pose_file = d / (capture.files.observer_pose or "observer_pose.json")
    if not pose_file.exists():
        res.error = "no observer_pose.json (camera not localized)"
        return res
    cam0 = ObserverCamera.from_file(pose_file)
    start = capture_start_epoch(capture)
    t_offset = (start - clock_zero) if (start is not None and clock_zero is not None) else 0.0
    detector.reset()
    tracker = ByteTracker(track_buffer=max(1, int(round(track_buffer_s * target_fps))))
    cam = cam0
    t_first = t_last = None
    for t, idx, frame in iter_capture_frames(d, capture, target_fps):
        if res.n_frames == 0:
            cam = cam0.scaled(frame.shape[1], frame.shape[0])
            t_first = t
        dets = detector.detect(frame)
        res.n_frames += 1
        res.n_detections += len(dets)
        tracker.update(dets, idx, t)
        t_last = t
    if res.n_frames == 0:
        res.error = "no frames decoded"
        return res
    res.t_span = (t_first + t_offset, t_last + t_offset)
    tracks = tracker.all_tracks(min_len=max(3, int(min_track_s * target_fps)))
    res.n_image_tracks = len(tracks)
    for tr in tracks:
        ft = image_track_to_floor(tr.history, cam, f"{capture.capture_id}:{tr.track_id}", t_offset=t_offset, grid=grid)
        if ft is not None and ft.duration >= min_track_s:
            res.floor_tracks.append(ft)
    return res


def build_user_estimated_traffic(twin: Twin, *, zones: Optional[list[TrafficZone]] = None,
                                 persons_per_min: Optional[dict[str, float]] = None,
                                 od_pairs: Optional[list[tuple[str, str, float]]] = None,
                                 walk_speed_mps: Optional[tuple[float, float]] = None) -> TrafficModel:
    """Traffic from user-marked zones and user-entered counts (report says "traffic: user-estimated")."""
    base = twin.traffic
    zones = list(zones if zones is not None else (base.zones or zones_from_waypoints(twin)))
    ids = {z.id for z in zones}
    ppm = dict(persons_per_min if persons_per_min is not None else base.persons_per_min)
    for k in ("quiet", "normal", "peak"):
        if k not in ppm:
            raise ValueError(f"persons_per_min needs '{k}'")
        if ppm[k] < 0:
            raise ValueError("persons_per_min must be >= 0")
    if not ppm["quiet"] <= ppm["normal"] <= ppm["peak"]:
        raise ValueError("persons_per_min must satisfy quiet <= normal <= peak")
    if od_pairs is None:
        od_pairs = list(base.od_pairs) if base.od_pairs else [(a, b, 1.0) for a in sorted(ids) for b in sorted(ids) if a != b]
    for a, b, w in od_pairs:
        if a not in ids or b not in ids:
            raise ValueError(f"OD pair ({a}, {b}) references an unknown zone")
        if w < 0:
            raise ValueError("OD weights must be >= 0")
    total = sum(w for _, _, w in od_pairs)
    od = [(a, b, round(w / total, 4)) for a, b, w in od_pairs] if total > 0 else []
    ws = walk_speed_mps or base.walk_speed_mps
    return TrafficModel(source="user_estimated", zones=zones, od_pairs=od, persons_per_min=ppm,
                        walk_speed_mps=tuple(ws), heatmap_uri=None)


def build_traffic(observer_capture_dirs: Sequence[str], twin: Twin, *,
                  detector: Union[None, str, PersonDetector, Callable[[], PersonDetector]] = None,
                  target_fps: float = 8.0, window_s: float = 900.0, out_prefix: Optional[str] = None,
                  zones: Optional[list[TrafficZone]] = None, merge_dist_m: float = 0.6,
                  fallback: bool = True) -> TrafficModel:
    t0 = time.time()
    grid = _grid_for(twin)

    def new_detector() -> PersonDetector:
        if isinstance(detector, PersonDetector):
            return detector
        if callable(detector):
            return detector()
        return make_person_detector(detector)

    starts = [capture_start_epoch(load_capture(d)) for d in observer_capture_dirs]
    known = [s for s in starts if s is not None]
    clock_zero = min(known) if len(known) == len(starts) and known else None
    if known and clock_zero is None:
        log.warning("some observers lack started_at; assuming all recordings started together")

    results: list[ObserverResult] = []
    for d in observer_capture_dirs:
        try:
            results.append(process_observer(d, grid=grid, detector=new_detector(), target_fps=target_fps,
                                            clock_zero=clock_zero))
        except Exception as e:
            log.exception("observer %s failed", d)
            results.append(ObserverResult(Path(d).name, error=f"{type(e).__name__}: {e}"))
    floor_tracks = [ft for r in results for ft in r.floor_tracks]
    merged = merge_tracks(floor_tracks, dist_thresh_m=merge_dist_m)
    spans = [r.t_span for r in results if r.n_frames > 0]

    if not merged or not spans:
        msg = "no person tracks observed" if spans else "no observer video could be processed"
        if not fallback:
            raise RuntimeError(msg)
        log.warning("%s; using user-estimated traffic", msg)
        model = build_user_estimated_traffic(twin, zones=zones)
        if out_prefix:
            storage.write_json(out_prefix.rstrip("/") + "/traffic_stats.json",
                               {"source": "user_estimated", "reason": msg, "observers": [_obs_summary(r) for r in results]})
        return model

    t_lo, t_hi = min(s[0] for s in spans), max(s[1] for s in spans)
    stats = compute_stats(merged, twin, t_lo, t_hi, window_s=window_s, zones=zones)
    heatmap_uri = None
    if out_prefix:
        pre = out_prefix.rstrip("/")
        maps, win_starts = heatmaps(merged, grid, t_lo, t_hi, window_s=window_s)
        win_keys = []
        for i, m in enumerate(maps):
            key = f"{pre}/heatmap_w{i:03d}.npy"
            np.save(storage.path(key), m)
            win_keys.append(key)
        heatmap_uri = f"{pre}/heatmap.npy"
        np.save(storage.path(heatmap_uri), maps.mean(axis=0).astype(np.float32))
        storage.write_json(f"{pre}/traffic_stats.json", {
            "source": "observed", "t_range": [t_lo, t_hi], "window_s": window_s,
            "heatmap_mean": heatmap_uri, "heatmap_windows": [{"start_s": round(s - t_lo, 1), "key": k}
                                                              for s, k in zip(win_starts, win_keys)],
            "heatmap_units": "person-seconds per cell (mean over windows for heatmap_mean)",
            "arrival_per_zone_per_min": stats.arrival_per_zone, "window_rates_per_min": stats.window_rates,
            "density_levels_per_min": stats.persons_per_min, "walk_speed_mps": stats.walk_speed,
            "walk_speed_observed": stats.speed_observed, "observers": [_obs_summary(r) for r in results],
            "n_floor_tracks": len(floor_tracks), "n_people_after_merge": len(merged),
            "wall_time_s": round(time.time() - t0, 2), **stats.extra})
    return TrafficModel(source="observed", zones=stats.zones, od_pairs=stats.od_pairs,
                        persons_per_min=stats.persons_per_min, walk_speed_mps=stats.walk_speed,
                        heatmap_uri=heatmap_uri)


def _obs_summary(r: ObserverResult) -> dict:
    return {"capture_id": r.capture_id, "frames": r.n_frames, "detections": r.n_detections,
            "image_tracks": r.n_image_tracks, "floor_tracks": len(r.floor_tracks), "error": r.error}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="J5: traffic model from fixed observer videos")
    ap.add_argument("--twin", required=True, help="storage key of twin.json")
    ap.add_argument("--observer", action="append", default=[], help="observer capture dir (repeatable)")
    ap.add_argument("--detector", default=None, choices=["auto", "rtdetr", "hog", "motion"])
    ap.add_argument("--fps", type=float, default=8.0)
    ap.add_argument("--write-twin", action="store_true", help="store the model in twin.traffic")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    twin = Twin.model_validate(storage.read_json(a.twin))
    prefix = str(Path(a.twin).parent).replace("\\", "/") + "/traffic"
    model = build_traffic(a.observer, twin, detector=a.detector, target_fps=a.fps, out_prefix=prefix)
    print(model.model_dump_json(indent=2))
    if a.write_twin:
        storage.write_json(a.twin, twin.model_copy(update={"traffic": model}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
