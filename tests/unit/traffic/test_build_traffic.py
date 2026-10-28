import numpy as np
import pytest

from clearance_core import grid as gridio
from clearance_core import storage
from clearance_core.grid import FREE, Grid
from clearance_core.schemas import TrafficModel, TrafficZone, Twin
from jobs.traffic.detect import HOGPersonDetector, MotionBlobDetector, make_person_detector, nms
from jobs.traffic.run import build_traffic, build_user_estimated_traffic, process_observer

from .observer_scene import Person, write_observer

PEOPLE = [Person((0.8, 1.4), (7.2, 1.6), 1.2, 2.0),
          Person((7.2, 2.8), (0.8, 2.6), 1.0, 4.0),
          Person((0.8, 2.0), (7.2, 2.0), 1.4, 11.0)]
ZONES = [TrafficZone(id="W", polygon=[(0, 0), (2, 0), (2, 4), (0, 4)]),
         TrafficZone(id="E", polygon=[(6, 0), (8, 0), (8, 4), (6, 4)])]
CAM0 = ((-0.3, 2.0, 3.0), (4.5, 2.0, 0.0))
CAM1 = ((8.3, 2.0, 3.0), (3.5, 2.0, 0.0))


@pytest.fixture
def twin(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA", tmp_path / "data")
    g = Grid(np.full((80, 160), FREE, np.uint8), 0.05, (0.0, 0.0))  # 8 m x 4 m room
    occ = gridio.save(g, "sites/s1/twin_v1/occ.png")
    return Twin(twin_id="tw1", site_id="s1", occupancy=occ)


def test_two_observers_end_to_end(tmp_path, twin):
    d0 = write_observer(tmp_path / "obs0", *CAM0, PEOPLE, as_video=True)
    # second phone started 2 s later, writes frames, and stores its pose in the OpenCV convention
    d1 = write_observer(tmp_path / "obs1", *CAM1, PEOPLE, as_video=False, start_offset_s=2.0, duration_s=18.0,
                        pose_convention="opencv")
    model = build_traffic([str(d0), str(d1)], twin, detector=MotionBlobDetector, zones=ZONES,
                          out_prefix="sites/s1/twin_v1/traffic")
    assert isinstance(model, TrafficModel) and model.source == "observed"
    stats = storage.read_json("sites/s1/twin_v1/traffic/traffic_stats.json")
    assert stats["n_people_after_merge"] == 3, stats
    assert all(o["error"] is None for o in stats["observers"])
    assert stats["n_floor_tracks"] > 3  # people were seen by both cameras and merged
    od = {(a, b): w for a, b, w in model.od_pairs}
    assert od[("W", "E")] == pytest.approx(2 / 3, abs=0.01) and od[("E", "W")] == pytest.approx(1 / 3, abs=0.01)
    mean, std = model.walk_speed_mps
    assert abs(mean - np.mean([1.2, 1.0, 1.4])) < 0.12
    assert 0.05 <= std < 0.4
    # 3 arrivals in a ~20 s recording: a single window -> quiet = normal = peak = 3 / span
    span_min = (stats["t_range"][1] - stats["t_range"][0]) / 60
    assert model.persons_per_min["normal"] == pytest.approx(3 / span_min, abs=0.01)
    assert model.persons_per_min["quiet"] <= model.persons_per_min["normal"] <= model.persons_per_min["peak"]
    hm = np.load(storage.DATA / model.heatmap_uri)
    assert hm.shape == (80, 160) and hm.dtype == np.float32
    observed_s = sum(p.end - p.start for p in PEOPLE)
    assert 0.6 * observed_s < hm.sum() <= observed_s * 1.05
    r, c = np.unravel_index(np.argmax(hm), hm.shape)
    assert 0.8 <= (r + 0.5) * 0.05 and (r + 0.5) * 0.05 <= 3.2  # hottest cell lies on the walking lanes
    assert stats["heatmap_windows"][0]["key"].endswith("heatmap_w000.npy")


def test_single_observer_track_geometry(tmp_path, twin):
    d0 = write_observer(tmp_path / "obs0", *CAM0, PEOPLE[:1], duration_s=10.0, as_video=False)  # codec-independent
    res = process_observer(d0, grid=gridio.load(twin.occupancy), detector=MotionBlobDetector())
    assert res.error is None and len(res.floor_tracks) == 1
    ft = res.floor_tracks[0]
    p = PEOPLE[0]
    # positions match the ground-truth line at the same timestamps (box-bottom -> floor)
    gt = np.array([p.at(t) for t in ft.t])
    err = np.linalg.norm(ft.xy - gt, axis=1)
    assert np.median(err) < 0.15 and err.max() < 0.5
    assert ft.xy[:, 0].min() > 1.4  # the floor right under the camera is out of view


def test_fallback_when_nothing_is_observed(tmp_path, twin):
    empty = write_observer(tmp_path / "empty", *CAM0, [], duration_s=4.0)
    no_pose = write_observer(tmp_path / "nopose", *CAM0, PEOPLE, duration_s=4.0, write_pose=False)
    twin2 = twin.model_copy(update={"traffic": twin.traffic.model_copy(update={"zones": ZONES})})
    model = build_traffic([str(empty), str(no_pose)], twin2, detector="motion", out_prefix="sites/s1/twin_v1/traffic")
    assert model.source == "user_estimated"
    assert {(a, b) for a, b, _ in model.od_pairs} == {("W", "E"), ("E", "W")}
    stats = storage.read_json("sites/s1/twin_v1/traffic/traffic_stats.json")
    assert stats["source"] == "user_estimated"
    assert any(o["error"] and "observer_pose" in o["error"] for o in stats["observers"])
    with pytest.raises(RuntimeError):
        build_traffic([str(empty)], twin2, detector="motion", fallback=False)
    assert build_traffic([str(tmp_path / "does_not_exist")], twin2).source == "user_estimated"


def test_user_estimated_builder_validation(twin):
    m = build_user_estimated_traffic(twin, zones=ZONES, persons_per_min={"quiet": 1, "normal": 3, "peak": 8},
                                     od_pairs=[("W", "E", 3.0), ("E", "W", 1.0)])
    assert m.source == "user_estimated" and m.od_pairs == [("W", "E", 0.75), ("E", "W", 0.25)]
    assert m.heatmap_uri is None
    with pytest.raises(ValueError):
        build_user_estimated_traffic(twin, zones=ZONES, persons_per_min={"quiet": 5, "normal": 3, "peak": 8})
    with pytest.raises(ValueError):
        build_user_estimated_traffic(twin, zones=ZONES, od_pairs=[("W", "X", 1.0)])
    with pytest.raises(ValueError):
        build_user_estimated_traffic(twin, zones=ZONES, persons_per_min={"quiet": 1, "normal": 2})
    assert build_user_estimated_traffic(twin, zones=[]).od_pairs == []


def test_detector_backends_on_cpu():
    blank = np.full((240, 320, 3), 180, np.uint8)
    if HOGPersonDetector.available():
        assert HOGPersonDetector().detect(blank).shape == (0, 5)
        assert make_person_detector("auto").name in ("hog", "rtdetr")
    else:  # OpenCV 5 main build: HOG lives in contrib
        with pytest.raises(RuntimeError):
            HOGPersonDetector()
        assert make_person_detector("auto").name in ("motion", "rtdetr")
    with pytest.raises(ValueError):
        make_person_detector("yolo")
    mot = MotionBlobDetector(warmup_frames=3)
    for _ in range(5):
        assert len(mot.detect(blank)) == 0
    frame = blank.copy()
    frame[60:200, 100:140] = 30  # an upright dark blob appears
    d = mot.detect(frame)
    assert len(d) == 1 and d[0, 3] == pytest.approx(200, abs=3)
    flat = blank.copy()
    flat[200:215, 20:300] = 30  # wide, flat blob: not person-shaped
    mot.reset()
    for _ in range(3):
        mot.detect(blank)
    assert len(mot.detect(flat)) == 0
    dets = np.array([[0, 0, 10, 20, 0.9], [1, 1, 11, 21, 0.8], [50, 50, 60, 70, 0.7]])
    assert len(nms(dets)) == 2 and nms(np.zeros((0, 5))).shape == (0, 5)
