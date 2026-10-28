"""Traffic statistics from floor tracks (PLAN 7.5 steps 5-6): zones, OD pairs, arrival rates,
walking speeds, heatmaps per 15-min window and quiet/normal/peak density levels."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np
from scipy.ndimage import gaussian_filter
from shapely.geometry import MultiPoint, Point, Polygon

from clearance_core.grid import Grid
from clearance_core.schemas import TrafficZone, Twin
from jobs.semantics.cluster import dbscan
from jobs.traffic.floor import FloorTrack

DEFAULT_WALK = (1.2, 0.25)


# ---------- zones ----------
def zones_from_waypoints(twin: Twin, half_size_m: float = 0.75) -> list[TrafficZone]:
    out = []
    for w in twin.waypoints:
        x, y = w.xy
        s = half_size_m
        out.append(TrafficZone(id=f"Z_{w.id}", polygon=[(x - s, y - s), (x + s, y - s), (x + s, y + s), (x - s, y + s)]))
    return out


def zones_from_endpoints(tracks: Sequence[FloorTrack], eps_m: float = 0.8, min_samples: int = 2,
                         buffer_m: float = 0.5) -> list[TrafficZone]:
    """Auto zones where tracks begin/end (doors, counters): DBSCAN on endpoints, buffered hulls."""
    if not tracks:
        return []
    pts = np.array([p for tr in tracks for p in (tr.xy[0], tr.xy[-1])])
    lab = dbscan(pts, eps=eps_m, min_samples=min_samples)
    zones = []
    for k in range(lab.max() + 1):
        cl = pts[lab == k]
        poly = MultiPoint([tuple(p) for p in cl]).convex_hull.buffer(buffer_m, quad_segs=4)
        poly = poly.simplify(0.05)
        zones.append(TrafficZone(id=f"Z{k + 1}", polygon=[(round(x, 3), round(y, 3)) for x, y in poly.exterior.coords[:-1]]))
    return zones


class ZoneIndex:
    def __init__(self, zones: Sequence[TrafficZone], snap_m: float = 0.75):
        self.zones = list(zones)
        self.polys = [Polygon(z.polygon) for z in self.zones]
        self.snap_m = snap_m

    def zone_of(self, xy) -> Optional[str]:
        p = Point(float(xy[0]), float(xy[1]))
        best, best_d = None, self.snap_m
        for z, poly in zip(self.zones, self.polys):
            if poly.contains(p):
                return z.id
            d = poly.distance(p)
            if d <= best_d:
                best, best_d = z.id, d
        return best


def zone_sequence(track: FloorTrack, zi: ZoneIndex, dt: float = 0.5, min_dwell_samples: int = 1) -> list[str]:
    """Distinct consecutive zones a track visits (ignoring time outside every zone)."""
    _, xy = track.resample(dt, max_gap=10.0)
    if len(xy) == 0:
        return []
    seq: list[str] = []
    run_zone, run_len = None, 0
    for p in xy:
        z = zi.zone_of(p)
        if z == run_zone:
            run_len += 1
        else:
            run_zone, run_len = z, 1
        if z is not None and run_len >= min_dwell_samples and (not seq or seq[-1] != z):
            seq.append(z)
    return seq


def od_pairs(tracks: Sequence[FloorTrack], zi: ZoneIndex) -> tuple[list[tuple[str, str, float]], dict]:
    counts: dict[tuple[str, str], int] = {}
    first_zone: dict[str, Optional[str]] = {}
    for tr in tracks:
        seq = zone_sequence(tr, zi)
        first_zone[tr.track_id] = zi.zone_of(tr.xy[0])
        for a, b in zip(seq[:-1], seq[1:]):
            counts[(a, b)] = counts.get((a, b), 0) + 1
    total = sum(counts.values())
    pairs = sorted(((a, b, c / total) for (a, b), c in counts.items()), key=lambda p: (-p[2], p[0], p[1])) if total else []
    return [(a, b, round(w, 4)) for a, b, w in pairs], {"trip_counts": {f"{a}->{b}": c for (a, b), c in counts.items()},
                                                         "first_zone": first_zone}


# ---------- rates and density ----------
def arrival_rates(tracks: Sequence[FloorTrack], zi: ZoneIndex, duration_s: float) -> dict[str, float]:
    """Persons per minute starting in each zone (plus "_unzoned" for tracks starting elsewhere)."""
    mins = max(duration_s / 60.0, 1e-6)
    out: dict[str, float] = {z.id: 0.0 for z in zi.zones}
    for tr in tracks:
        z = zi.zone_of(tr.xy[0]) or "_unzoned"
        out[z] = out.get(z, 0.0) + 1.0
    return {k: round(v / mins, 4) for k, v in out.items()}


def window_rates(start_times: np.ndarray, t_lo: float, t_hi: float, window_s: float = 900.0,
                 stride_s: float = 60.0) -> np.ndarray:
    """Arrivals per minute in sliding windows of length window_s (clipped to the recording)."""
    span = t_hi - t_lo
    if span <= 0:
        return np.array([len(start_times) * 60.0 / max(window_s, 1.0)])
    w = min(window_s, span)
    starts = np.arange(t_lo, t_hi - w + 1e-9, stride_s) if span > w else np.array([t_lo])
    st = np.sort(np.asarray(start_times, dtype=np.float64))
    counts = np.searchsorted(st, starts + w, side="right") - np.searchsorted(st, starts, side="left")
    return counts / (w / 60.0)


def density_levels(rates: np.ndarray) -> dict[str, float]:
    """quiet = 25th percentile window, normal = median, peak = 90th percentile."""
    r = np.asarray(rates, dtype=np.float64)
    if r.size == 0:
        return {"quiet": 0.0, "normal": 0.0, "peak": 0.0}
    return {"quiet": round(float(np.percentile(r, 25)), 3), "normal": round(float(np.percentile(r, 50)), 3),
            "peak": round(float(np.percentile(r, 90)), 3)}


# ---------- speeds ----------
def walking_speeds(tracks: Sequence[FloorTrack], dt: float = 0.5, moving_mps: float = 0.3,
                   min_moving_s: float = 2.0, clip=(0.3, 2.5)) -> np.ndarray:
    """Per-track median speed while moving (dwelling excluded)."""
    out = []
    for tr in tracks:
        ts, xy = tr.resample(dt, max_gap=1.0)
        if len(ts) < 3:
            continue
        v = np.linalg.norm(np.diff(xy, axis=0), axis=1) / np.maximum(np.diff(ts), 1e-6)
        mv = v[v > moving_mps]
        if len(mv) * dt >= min_moving_s:
            out.append(float(np.clip(np.median(mv), *clip)))
    return np.asarray(out)


def speed_distribution(speeds: np.ndarray) -> tuple[tuple[float, float], bool]:
    """(mean, std) and whether it was observed (False -> default prior)."""
    if speeds.size == 0:
        return DEFAULT_WALK, False
    std = float(speeds.std(ddof=1)) if speeds.size > 1 else DEFAULT_WALK[1]
    return (round(float(speeds.mean()), 3), round(max(std, 0.05), 3)), True


# ---------- heatmaps ----------
def heatmaps(tracks: Sequence[FloorTrack], grid: Grid, t_lo: float, t_hi: float, window_s: float = 900.0,
             dt: float = 0.2, sigma_m: float = 0.15) -> tuple[np.ndarray, list[float]]:
    """Person-seconds per cell, one (H,W) float32 layer per window_s window."""
    H, W = grid.shape
    n_win = max(1, int(np.ceil((t_hi - t_lo) / window_s - 1e-9)))
    starts = [t_lo + k * window_s for k in range(n_win)]
    maps = np.zeros((n_win, H, W), dtype=np.float64)
    for tr in tracks:
        ts, xy = tr.resample(dt, max_gap=1.5)
        if len(ts) == 0:
            continue
        r, c = grid.world_to_cell(xy[:, 0], xy[:, 1])
        k = np.clip(((ts - t_lo) // window_s).astype(int), 0, n_win - 1)
        ok = (r >= 0) & (r < H) & (c >= 0) & (c < W)
        np.add.at(maps, (k[ok], r[ok], c[ok]), dt)
    if sigma_m > 0:
        s = sigma_m / grid.res
        for i in range(n_win):
            total = maps[i].sum()
            maps[i] = gaussian_filter(maps[i], s, mode="constant")
            if maps[i].sum() > 0:
                maps[i] *= total / maps[i].sum()  # keep person-seconds after blurring at the borders
    return maps.astype(np.float32), starts


@dataclass
class TrafficStats:
    zones: list[TrafficZone]
    od_pairs: list[tuple[str, str, float]]
    persons_per_min: dict[str, float]
    walk_speed: tuple[float, float]
    speed_observed: bool
    arrival_per_zone: dict[str, float]
    window_rates: list[float]
    extra: dict = field(default_factory=dict)


def compute_stats(tracks: Sequence[FloorTrack], twin: Twin, t_lo: float, t_hi: float,
                  window_s: float = 900.0, zones: Optional[list[TrafficZone]] = None) -> TrafficStats:
    if zones is None:
        zone_src = "twin"
        zones = list(twin.traffic.zones)
        if not zones:
            zones, zone_src = zones_from_endpoints(tracks), "track_endpoints"
        if not zones:
            zones, zone_src = zones_from_waypoints(twin), "waypoints"
    else:
        zone_src = "given"
    zi = ZoneIndex(zones)
    pairs, od_extra = od_pairs(tracks, zi)
    starts = np.array([tr.t0 for tr in tracks])
    rates = window_rates(starts, t_lo, t_hi, window_s)
    speeds = walking_speeds(tracks)
    ws, observed = speed_distribution(speeds)
    return TrafficStats(zones=zones, od_pairs=pairs, persons_per_min=density_levels(rates), walk_speed=ws,
                        speed_observed=observed, arrival_per_zone=arrival_rates(tracks, zi, t_hi - t_lo),
                        window_rates=[round(float(r), 3) for r in rates],
                        extra={"zone_source": zone_src, "n_tracks": len(tracks), "speeds": speeds.round(3).tolist(),
                               "duration_s": round(t_hi - t_lo, 2), **od_extra})
