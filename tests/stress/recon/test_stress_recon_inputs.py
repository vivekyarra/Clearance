"""Real-world capture inputs: unsafe paths, damaged and oversized images, rotated depth, odd names."""
import json
import os
import shutil

import cv2
import numpy as np
import pytest

from jobs.common.imageio import FrameFiles, UnsafePath, confined_path, imread, imwrite
from jobs.recon.depth import LidarDepth
from jobs.recon.ingest import CaptureRejected, load_capture
from jobs.recon.pipeline import reconstruct


def _edit_capture_json(cap, **files):
    cj = json.loads((cap / "capture.json").read_text(encoding="utf-8"))
    cj["files"].update(files)
    (cap / "capture.json").write_text(json.dumps(cj), encoding="utf-8")


def _rewrite_poses(cap, fn):
    lines = (cap / "poses.jsonl").read_text(encoding="utf-8").splitlines()
    recs = [json.loads(s) for s in lines if s.strip()]
    out = fn(recs)
    (cap / "poses.jsonl").write_text("\n".join(out if isinstance(out[0], str) else map(json.dumps, out)) + "\n",
                                     encoding="utf-8")


# ---------------------------------------------------------------------------------------------- paths
@pytest.mark.parametrize("key", ["poses", "depth", "confidence", "frames", "feature_points", "relative_depth"])
@pytest.mark.parametrize("bad", ["../outside", "..\\outside", "/etc", "C:\\Windows", "C:relative", "depth/../../x",
                                 "\\\\server\\share"])
def test_capture_paths_must_stay_inside_root(small_capture, key, bad):
    _edit_capture_json(small_capture, **{key: bad})
    with pytest.raises(CaptureRejected, match=f"files.{key}"):
        load_capture(small_capture)


def test_symlink_escaping_root_is_rejected(small_capture, tmp_path):
    outside = tmp_path / "outside_depth"
    shutil.copytree(small_capture / "depth", outside)
    link = small_capture / "linked"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need extra privileges on this system")
    _edit_capture_json(small_capture, depth="linked")
    with pytest.raises(CaptureRejected, match="outside"):
        load_capture(small_capture)


@pytest.mark.parametrize("frame", ["../../secret.jpg", "/abs/000001.jpg", "C:\\x\\000001.jpg", "a/../../b.jpg"])
def test_pose_frame_names_must_be_plain(small_capture, frame):
    def damage(recs):
        recs[4]["frame"] = frame
        return recs
    _rewrite_poses(small_capture, damage)
    with pytest.raises(CaptureRejected, match="frame name"):
        load_capture(small_capture)


def test_confined_path_accepts_nested_and_matches_case(tmp_path):
    (tmp_path / "Depth").mkdir()
    p = confined_path(tmp_path, "depth")  # exact name on case-sensitive systems, as given elsewhere
    assert p.is_dir() and os.path.samefile(p, tmp_path / "Depth") and p.name.lower() == "depth"
    assert confined_path(tmp_path, "./a/b") == tmp_path / "a" / "b"
    with pytest.raises(UnsafePath):
        confined_path(tmp_path, "")


# ---------------------------------------------------------------------------------------------- images
def test_decompression_bomb_is_refused_from_header(tmp_path):
    big = np.zeros((6000, 6000), np.uint8)  # compresses to a few KB, decodes to 36 MB
    p = tmp_path / "bomb.png"
    imwrite(p, big)
    assert p.stat().st_size < 200_000
    assert imread(p) is None                                   # above the colour cap
    assert imread(p, max_wh=None, expect_wh=(320, 240)) is None  # above the pose image size
    small = tmp_path / "ok.png"
    imwrite(small, np.zeros((240, 320), np.uint8))
    assert imread(small, expect_wh=(320, 240)).shape == (240, 320)


def test_oversized_depth_png_is_dropped_not_decoded(small_capture):
    f = sorted((small_capture / "depth").iterdir())[3]
    imwrite(f, np.zeros((4000, 3000), np.uint16))  # aspect 4:3 like the pose, but far above the depth cap
    cap = load_capture(small_capture)
    src = LidarDepth(cap)
    assert src.get(3) is None and src.stats["dropped_missing"] == 1


def test_unicode_and_space_paths_round_trip(tmp_path, small_capture_src):
    d = tmp_path / "scan ü café 東京"
    shutil.copytree(small_capture_src, d)
    img = np.arange(12, dtype=np.uint16).reshape(3, 4)
    assert imwrite(d / "probe é.png", img)
    assert np.array_equal(imread(d / "probe é.png"), img)
    r = reconstruct(str(d))
    assert r["stats"]["n_depth_frames"] >= 60


def test_corrupt_and_wrong_format_depth_frames_are_counted(small_capture):
    files = sorted((small_capture / "depth").iterdir())
    files[0].write_bytes(files[0].read_bytes()[:200])           # truncated PNG
    files[1].write_bytes(b"\xff\xd8\xff\xe0 not really a jpeg")  # garbage
    files[2].write_bytes(b"")                                    # empty
    imwrite(files[3], (np.ones((192, 256)) * 150).astype(np.uint8))  # 8-bit: not millimetres
    imwrite(files[4], np.zeros((192, 256, 3), np.uint16))        # colour
    imwrite(files[5], np.zeros((192, 256), np.uint16))           # all invalid depth
    files[6].unlink()                                            # missing
    r = reconstruct(str(small_capture))
    st = r["stats"]["depth_stats"]
    assert st["dropped_missing"] >= 4 and st["dropped_format"] == 2 and st["dropped_empty"] >= 1
    assert r["stats"]["n_depth_frames"] == r["stats"]["n_usable"] - sum(
        st[k] for k in ("dropped_missing", "dropped_format", "dropped_aspect", "dropped_empty"))


def test_rotated_depth_maps_are_dropped(small_capture):
    """Depth stored portrait while the pose image is landscape: scaling K by (w/pw, h/ph) would silently
    distort geometry, so those frames are refused."""
    files = sorted((small_capture / "depth").iterdir())
    for f in files[:5]:
        d = imread(f)
        imwrite(f, np.ascontiguousarray(np.rot90(d)))
    cap = load_capture(small_capture)
    src = LidarDepth(cap)
    assert all(src.get(i) is None for i in range(5)) and src.stats["dropped_aspect"] == 5
    assert src.get(5) is not None


def test_portrait_capture_with_matching_intrinsics_is_used(small_capture):
    """A portrait capture is fine when the depth map and intrinsics are portrait too."""
    def to_portrait(recs):
        for r in recs:
            fx, fy, cx, cy = r["K"]
            # rotate the camera 90 degrees about its optical axis: new x = old -y, new y = old x
            T = np.asarray(r["T_world_cam"]).reshape(4, 4)
            Rz = np.array([[0, 1, 0, 0], [-1, 0, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1.0]])
            r["T_world_cam"] = (T @ np.linalg.inv(Rz)).ravel().tolist()
            r["K"] = [fy, fx, r["h"] - 1 - cy, cx]
            r["w"], r["h"] = r["h"], r["w"]
        return recs
    _rewrite_poses(small_capture, to_portrait)
    for f in (small_capture / "depth").iterdir():
        imwrite(f, np.ascontiguousarray(np.rot90(imread(f), -1)))
    for f in (small_capture / "confidence").iterdir():
        imwrite(f, np.ascontiguousarray(np.rot90(imread(f), -1)))
    r = reconstruct(str(small_capture))
    assert r["stats"]["depth_stats"]["dropped_aspect"] == 0
    g = r["grid"]
    rr, cc = g.world_to_cell(3.0, 2.0)
    assert g.data[rr, cc] == 255  # the room centre reads free in the site frame


def test_confidence_map_of_other_resolution(small_capture):
    for f in (small_capture / "confidence").iterdir():
        imwrite(f, cv2.resize(imread(f), (128, 96), interpolation=cv2.INTER_NEAREST))
    cap = load_capture(small_capture)
    src = LidarDepth(cap)
    fr = src.get(0)
    assert fr is not None and src.stats["confidence_size_mismatch"] == 1


def test_upper_case_file_names(small_capture):
    for sub in ("depth", "confidence"):
        for f in sorted((small_capture / sub).iterdir()):
            tmp = f.with_name(f.stem + ".tmp")
            f.rename(tmp)
            tmp.rename(f.with_name(f.stem + ".PNG"))
    ff = FrameFiles(small_capture / "depth")
    assert ff.find("000003.jpg").name == "000003.PNG"
    assert ff.find("..") is None
    assert reconstruct(str(small_capture))["stats"]["n_depth_frames"] >= 60


# ---------------------------------------------------------------------------------------------- records
def test_bad_pose_records_are_dropped_not_fatal(small_capture):
    def damage(recs):
        recs[3]["T_world_cam"][0] = float("nan")
        recs[5]["T_world_cam"][0] = 3.0                  # scaled rotation
        recs[7]["K"] = [0, 0, 1, 1]
        return [json.dumps(r) for r in recs] + ['{"t": 99, "frame": "0000']  # interrupted write
    _rewrite_poses(small_capture, damage)
    cap = load_capture(small_capture)
    assert {3, 5, 7}.isdisjoint(cap.usable())
    assert any("truncated" in w for w in cap.warnings) and any("rigid" in w for w in cap.warnings)
    assert reconstruct(str(small_capture))["stats"]["n_depth_frames"] >= 60


def test_garbage_feature_point_lines_are_skipped(small_capture):
    good = (small_capture / "feature_points.jsonl").read_text(encoding="utf-8").splitlines()
    bad = ['{"frame": "000001.jpg", "points_world": [[1, 2]]}', "not json", '{"points_world": []}',
           '{"frame": "000002.jpg", "points_world": [[1, 2, "x"]]}',
           '{"frame": "000003.jpg", "points_world": [[NaN, 1, 2], [1, 2, 3]]}']
    (small_capture / "feature_points.jsonl").write_text("\n".join(good[5:] + bad) + "\n", encoding="utf-8")
    _edit_capture_json(small_capture, feature_points="feature_points.jsonl")
    cap = load_capture(small_capture)
    assert any("feature-point lines" in w for w in cap.warnings)
    assert cap.feature_points["000003.jpg"].shape == (1, 3)


def test_pose_jump_after_tracking_loss_is_reported(small_capture):
    def jump(recs):
        for r in recs[40:]:
            r["T_world_cam"][3] += 1.5  # relocalization moved the map by 1.5 m
        return recs
    _rewrite_poses(small_capture, jump)
    cap = load_capture(small_capture)
    assert cap.pose_jumps == [40] and any("pose jumps" in w for w in cap.warnings)


# ---------------------------------------------------------------------------------------------- extremes
def test_every_depth_frame_invalid_is_a_clear_error(small_capture):
    for f in (small_capture / "depth").iterdir():
        imwrite(f, np.zeros((192, 256), np.uint16))
    with pytest.raises(ValueError, match="usable depth.*dropped_empty"):
        reconstruct(str(small_capture))


@pytest.mark.parametrize("n", [10, 12, 16])
def test_very_short_scan_succeeds_or_fails_clearly(small_capture, n):
    _rewrite_poses(small_capture, lambda recs: recs[:n])
    try:
        r = reconstruct(str(small_capture))
    except ValueError as e:  # CaptureRejected is a ValueError too
        assert str(e)
        return
    assert r["grid"].data.size > 0 and r["stats"]["n_depth_frames"] >= 10
