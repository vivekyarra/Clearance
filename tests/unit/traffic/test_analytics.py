import numpy as np
import pytest

from clearance_core.grid import FREE, Grid
from clearance_core.schemas import TrafficZone, Twin, Waypoint
from jobs.traffic.analytics import (ZoneIndex, arrival_rates, compute_stats, density_levels, heatmaps, od_pairs,
                                    walking_speeds, window_rates, zone_sequence, zones_from_endpoints)
from jobs.traffic.floor import FloorTrack, image_track_to_floor, merge_tracks, track_distance

A, B, C = (0.5, 0.5), (5.5, 0.5), (5.5, 4.5)


def square(zid, c, s=0.6):
    return TrafficZone(id=zid, polygon=[(c[0] - s, c[1] - s), (c[0] + s, c[1] - s), (c[0] + s, c[1] + s), (c[0] - s, c[1] + s)])


ZONES = [square("A", A), square("B", B), square("C", C)]


def walk(tid, p0, p1, t0, speed=1.2, dt=0.125, dwell_s=0.0, cam="cam0", rng=None, noise=0.0):
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    dur = np.linalg.norm(p1 - p0) / speed
    ts = np.arange(0, dur + dwell_s + 1e-9, dt)
    frac = np.clip(ts / dur, 0, 1)
    xy = p0 + frac[:, None] * (p1 - p0)
    if rng is not None and noise:
        xy = xy + rng.normal(0, noise, xy.shape)
    return FloorTrack(f"{cam}:{tid}", ts + t0, xy, cameras={cam}, members=[f"{cam}:{tid}"])


def reference_tracks():
    """10 trips A->B, 5 trips B->C, 3 round trips A->B->A, spread over 30 minutes."""
    rng = np.random.default_rng(0)
    tracks, k = [], 0
    for i in range(10):
        tracks.append(walk(k, A, B, 60 + i * 80, speed=rng.uniform(1.0, 1.4))); k += 1
    for i in range(5):
        tracks.append(walk(k, B, C, 1000 + i * 120, speed=1.1)); k += 1
    for i in range(3):
        a = walk(k, A, B, 1300 + i * 60, speed=1.2)
        b = walk(k, B, A, a.t1 + 0.125, speed=1.2)
        tracks.append(FloorTrack(a.track_id, np.r_[a.t, b.t], np.r_[a.xy, b.xy], cameras={"cam0"})); k += 1
    return tracks


def test_od_pairs_from_zone_sequences():
    tracks = reference_tracks()
    zi = ZoneIndex(ZONES)
    assert zone_sequence(tracks[-1], zi) == ["A", "B", "A"]
    pairs, extra = od_pairs(tracks, zi)
    w = {(a, b): x for a, b, x in pairs}
    total = 10 + 5 + 3 * 2
    assert w[("A", "B")] == pytest.approx(13 / total, abs=1e-3)
    assert w[("B", "C")] == pytest.approx(5 / total, abs=1e-3)
    assert w[("B", "A")] == pytest.approx(3 / total, abs=1e-3)
    assert sum(w.values()) == pytest.approx(1.0, abs=1e-3)
    assert pairs[0][:2] == ("A", "B")  # sorted by weight


def test_arrival_rates_and_density_levels():
    tracks = reference_tracks()
    zi = ZoneIndex(ZONES)
    rates = arrival_rates(tracks, zi, duration_s=1800)
    assert rates["A"] == pytest.approx(13 / 30, abs=1e-3) and rates["B"] == pytest.approx(5 / 30, abs=1e-3)
    st = np.array([t.t0 for t in tracks])
    wr = window_rates(st, 0, 1800, 900, 60)
    assert len(wr) == 16
    lv = density_levels(wr)
    assert lv["quiet"] <= lv["normal"] <= lv["peak"]
    # explicit check against a direct count for the first window
    assert wr[0] == pytest.approx(((st >= 0) & (st <= 900)).sum() / 15.0)
    # degenerate inputs
    assert density_levels(np.array([])) == {"quiet": 0.0, "normal": 0.0, "peak": 0.0}
    one = window_rates(np.array([5.0, 10.0]), 0, 300, 900)
    assert one.shape == (1,) and one[0] == pytest.approx(2 / 5)


def test_density_percentiles_follow_definition():
    # arrivals: 1/min for 30 min then 4/min for 30 min
    st = np.r_[np.arange(0, 1800, 60.0), 1800 + np.arange(0, 1800, 15.0)]
    wr = window_rates(st, 0, 3600, 900, 60)
    lv = density_levels(wr)
    assert lv["quiet"] == pytest.approx(np.percentile(wr, 25), abs=1e-3)
    assert lv["normal"] == pytest.approx(np.median(wr), abs=1e-3)
    assert lv["peak"] == pytest.approx(np.percentile(wr, 90), abs=1e-3)
    assert lv["quiet"] < 2.0 < lv["peak"] and lv["peak"] == pytest.approx(4.0, abs=0.1)


def test_walking_speed_excludes_dwell():
    rng = np.random.default_rng(1)
    tracks = [walk(i, A, B, i * 30, speed=s, dwell_s=20.0, rng=rng, noise=0.02) for i, s in enumerate([1.0, 1.2, 1.4, 1.1])]
    sp = walking_speeds(tracks)
    assert len(sp) == 4
    assert np.allclose(np.sort(sp), [1.0, 1.1, 1.2, 1.4], atol=0.08)


def test_heatmap_person_seconds():
    g = Grid(np.full((100, 140), FREE, np.uint8), 0.05, (-0.5, -0.5))
    tr = walk(0, (1.0, 1.0), (1.0, 1.0001), 0.0, speed=1e-4, dwell_s=60.0)  # standing 1 minute
    tr2 = walk(1, A, B, 1000.0)
    maps, starts = heatmaps([tr, tr2], g, 0.0, 1800.0, window_s=900.0)
    assert maps.shape == (2, 100, 140) and maps.dtype == np.float32 and starts == [0.0, 900.0]
    assert maps[0].sum() == pytest.approx(tr.duration, rel=0.02)
    assert maps[1].sum() == pytest.approx(tr2.duration, rel=0.05)
    r, c = g.world_to_cell(1.0, 1.0)
    assert np.unravel_index(np.argmax(maps[0]), maps[0].shape) == (int(r), int(c))


def test_auto_zones_and_compute_stats():
    tracks = reference_tracks()
    twin = Twin(twin_id="t", site_id="s", occupancy={"uri": "x.png", "width": 140, "height": 100})
    zones = zones_from_endpoints(tracks)
    assert len(zones) == 3
    st = compute_stats(tracks, twin, 0, 1800)
    assert st.extra["zone_source"] == "track_endpoints" and len(st.zones) == 3
    assert st.od_pairs[0][2] == pytest.approx(13 / 21, abs=1e-3)
    assert st.speed_observed and 1.0 <= st.walk_speed[0] <= 1.4
    # user zones in the twin take precedence; waypoints are the last resort
    twin2 = twin.model_copy(update={"traffic": twin.traffic.model_copy(update={"zones": ZONES})})
    assert compute_stats(tracks, twin2, 0, 1800).extra["zone_source"] == "twin"
    twin3 = twin.model_copy(update={"waypoints": [Waypoint(id="DOOR", xy=A, kind="entrance")]})
    assert compute_stats([], twin3, 0, 1800).extra["zone_source"] == "waypoints"


def test_cross_camera_merge():
    rng = np.random.default_rng(2)
    # the same two people seen by two cameras (small projection offsets), plus one person only cam1 sees
    p1a = walk(1, A, B, 10, cam="cam0", rng=rng, noise=0.05)
    p1b = walk(7, (A[0] + 0.15, A[1]), (B[0] + 0.15, B[1]), 10.3, cam="cam1", rng=rng, noise=0.05)
    p2a = walk(2, B, C, 12, cam="cam0", rng=rng, noise=0.05)
    p2b = walk(8, B, C, 12, cam="cam1", rng=rng, noise=0.05)
    p3 = walk(9, C, A, 40, cam="cam1")
    assert track_distance(p1a, p1b) < 0.3
    merged = merge_tracks([p1a, p1b, p2a, p2b, p3])
    assert len(merged) == 3
    m1 = next(m for m in merged if "cam0:1" in m.members)
    assert m1.members == ["cam0:1", "cam1:7"] and m1.cameras == {"cam0", "cam1"}
    assert np.all(np.diff(m1.t) > 0)
    # two different people walking side by side 1.5 m apart are not merged
    q1 = walk(1, (0, 0), (5, 0), 0, cam="cam0")
    q2 = walk(2, (0, 1.5), (5, 1.5), 0, cam="cam1")
    assert len(merge_tracks([q1, q2])) == 2
    # same-camera tracks never merge, even when close (two people walking together)
    r1 = walk(1, (0, 0), (5, 0), 0, cam="cam0")
    r2 = walk(2, (0, 0.3), (5, 0.3), 0, cam="cam0")
    assert len(merge_tracks([r1, r2])) == 2
    # a group cannot absorb two overlapping tracks of one camera through a third camera
    s1 = walk(1, (0, 0), (5, 0), 0, cam="cam0")
    s2 = walk(2, (0, 0.4), (5, 0.4), 0, cam="cam0")
    s3 = walk(3, (0, 0.2), (5, 0.2), 0, cam="cam1")
    assert len(merge_tracks([s1, s2, s3])) == 2
    assert merge_tracks([]) == []


def test_image_track_to_floor_filters():
    from jobs.semantics.camera import look_at_arkit
    from jobs.traffic.homography import ObserverCamera

    T = look_at_arkit((0.0, 0.0, 2.5), (4.0, 0.0, 0.0))
    cam = ObserverCamera.from_pose(T, [500.0, 500.0, 320.0, 240.0], 640, 480, convention="arkit")
    xs = np.linspace(3.0, 5.0, 20)
    uv, ok = cam.floor_to_image(np.c_[xs, np.zeros(20)])
    assert ok.all()
    hist = [(i, i * 0.125, u - 15, v - 90, u + 15, v, 0.9) for i, (u, v) in enumerate(uv)]
    ft = image_track_to_floor(hist, cam, "cam0:1")
    assert ft is not None and np.allclose(ft.xy[:, 0], xs, atol=1e-6) and ft.cameras == {"cam0"}
    # standing still for the whole track (or a background ghost): not walking traffic
    still = [(i, i * 0.125, uv[0, 0] - 15, uv[0, 1] - 90, uv[0, 0] + 15, uv[0, 1], 0.9) for i in range(20)]
    assert image_track_to_floor(still, cam, "cam0:2") is None
    # feet cut off by the bottom image edge are not projected
    cut = [(i, i * 0.125, 300, 400, 330, 480, 0.9) for i in range(20)]
    assert image_track_to_floor(cut, cam, "cam0:3") is None
    # a single teleporting box is removed
    jump = list(hist)
    jump[10] = (10, 1.25, 10, 300, 40, 470, 0.9)
    ft2 = image_track_to_floor(jump, cam, "cam0:4")
    assert np.abs(np.diff(ft2.xy[:, 0])).max() < 0.5
