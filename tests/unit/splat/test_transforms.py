"""ARKit -> nerfstudio / COLMAP camera conversion for splat training (J3)."""
import json
import math

import cv2
import numpy as np
import pytest

from clearance_core.schemas import Capture, CaptureFiles, Device, PoseRecord
from jobs.semantics.camera import look_at_arkit, qvec_to_rotmat, rotmat_to_qvec
from jobs.splat.dataset import colmap_extrinsics, prepare_dataset
from jobs.splat.ply import read_gaussian_ply, rgb_to_sh0, sh0_to_rgb, write_gaussian_ply
from jobs.splat.train import EXIT_NO_GPU, gpu_backend_status, main

K = [500.0, 510.0, 320.0, 240.0]


def colmap_project(q, t, K, X):
    R = qvec_to_rotmat(q)
    Xc = R @ X + t
    return np.array([K[0] * Xc[0] / Xc[2] + K[2], K[1] * Xc[1] / Xc[2] + K[3]]), Xc[2]


@pytest.mark.parametrize("eye,target", [((0, 0, 1.5), (3, 1, 0.5)), ((2, -1, 1.2), (2.1, 3, 0.0)),
                                        ((-1, 2, 0.4), (-3, 2.5, 1.8)), ((0.3, 0.2, 2.0), (0.3, 0.2, 0.0))])
def test_colmap_extrinsics_project_like_arkit(eye, target):
    T = look_at_arkit(eye, target)
    q, t = colmap_extrinsics(T)
    assert q[0] >= 0 and abs(np.linalg.norm(q) - 1) < 1e-12
    uv, depth = colmap_project(q, t, K, np.asarray(target, float))
    assert np.allclose(uv, [320, 240], atol=1e-6) and depth > 0  # target sits on the optical axis, in front
    # a point up and to the right in the world, as seen by the ARKit camera, lands up-right in the image
    right, up = T[:3, 0], T[:3, 1]
    X = np.asarray(target, float) + 0.2 * right + 0.1 * up
    uv2, _ = colmap_project(q, t, K, X)
    assert uv2[0] > 320 and uv2[1] < 240
    # OpenCV camera centre equals the ARKit camera position
    assert np.allclose(-qvec_to_rotmat(q).T @ t, eye, atol=1e-9)


def test_quaternion_roundtrip():
    rng = np.random.default_rng(0)
    for _ in range(200):
        q = rng.normal(size=4)
        q /= np.linalg.norm(q)
        q = q if q[0] >= 0 else -q
        assert np.allclose(rotmat_to_qvec(qvec_to_rotmat(q)), q, atol=1e-9)
    R = qvec_to_rotmat([0, 1, 0, 0])  # 180 deg about x: the ARKit -> OpenCV flip
    assert np.allclose(R, np.diag([1, -1, -1]))


def write_scan(tmp_path, n=5, img_size=(320, 240)):
    cap_dir = tmp_path / "cap"
    (cap_dir / "frames").mkdir(parents=True)
    poses = []
    for i in range(n):
        a = 2 * math.pi * i / n
        T = look_at_arkit((2 * math.cos(a), 2 * math.sin(a), 1.4), (0, 0, 0.5))
        name = f"{i:06d}.jpg"
        cv2.imwrite(str(cap_dir / "frames" / name), np.full((img_size[1], img_size[0], 3), 100 + 20 * i, np.uint8))
        poses.append(PoseRecord(t=i, frame=name, T_world_cam=T.ravel().tolist(), K=K, w=640, h=480,
                                tracking="limited" if i == n - 1 else "normal"))
    (cap_dir / "poses.jsonl").write_text("\n".join(p.model_dump_json() for p in poses) + "\n")
    (cap_dir / "feature_points.jsonl").write_text(json.dumps({"frame": poses[0].frame,
                                                              "points_world": [[0, 0, 0.5], [0.2, 0.1, 0.7], [1, 1, 0]]}) + "\n")
    cap = Capture(capture_id="cap", site_id="s", device=Device(model="ref", tier="T2_VIO_MONO"), role="scanner",
                  files=CaptureFiles(frames="frames", feature_points="feature_points.jsonl"))
    (cap_dir / "capture.json").write_text(cap.model_dump_json())
    return cap_dir, poses


def test_prepare_dataset_writes_consistent_layouts(tmp_path):
    cap_dir, poses = write_scan(tmp_path)
    out = tmp_path / "ds"
    summary = prepare_dataset(cap_dir, out, copy_images=True)
    assert summary["n_frames"] == 4  # tracking-limited frame skipped
    tj = json.loads((out / "transforms.json").read_text())
    f0 = tj["frames"][0]
    assert f0["file_path"] == "images/000000.jpg" and (out / f0["file_path"]).exists()
    # transform_matrix is the ARKit camera-to-world (OpenGL axes), unchanged
    assert np.allclose(np.array(f0["transform_matrix"]).ravel(), poses[0].T_world_cam)
    # intrinsics rescaled from the 640x480 calibration to the 320x240 images
    assert (f0["w"], f0["h"]) == (320, 240) and f0["fl_x"] == pytest.approx(250) and f0["cy"] == pytest.approx(120)
    # COLMAP images.txt reproduces the same projection
    lines = [l for l in (out / "sparse/0/images.txt").read_text().splitlines() if l and not l.startswith("#")]
    vals = lines[0].split()
    q, t = np.array(vals[1:5], float), np.array(vals[5:8], float)
    cams = [l for l in (out / "sparse/0/cameras.txt").read_text().splitlines() if l and not l.startswith("#")]
    assert len(cams) == 1 and cams[0].split()[1] == "PINHOLE"
    fx, fy, cx, cy = map(float, cams[0].split()[4:8])
    uv, depth = colmap_project(q, t, [fx, fy, cx, cy], np.array([0, 0, 0.5]))
    assert depth > 0 and np.allclose(uv, [cx, cy], atol=1e-4)
    # the same point through the nerfstudio matrix: in front means negative z in OpenGL camera axes
    c2w = np.array(f0["transform_matrix"])
    Xc = np.linalg.inv(c2w) @ np.array([0, 0, 0.5, 1])
    assert Xc[2] < 0
    pts = [l for l in (out / "sparse/0/points3D.txt").read_text().splitlines() if l and not l.startswith("#")]
    assert len(pts) == 3


def test_prepare_errors_and_cli_without_gpu(tmp_path, capsys):
    cap_dir, _ = write_scan(tmp_path, n=2)  # only one usable frame
    with pytest.raises(ValueError):
        prepare_dataset(cap_dir, tmp_path / "ds")
    cap_dir2, _ = write_scan(tmp_path / "b", n=6)
    ok, why = gpu_backend_status()
    code = main(["--capture", str(cap_dir2), "--out", str(tmp_path / "ds2"), "--iters", "10"])
    if ok:
        pytest.skip("GPU present: training path exercised elsewhere")
    assert code == EXIT_NO_GPU
    err = capsys.readouterr().err
    assert "skipped" in err and why.split(":")[0] in err
    assert (tmp_path / "ds2" / "transforms.json").exists()
    assert main(["--capture", str(cap_dir2), "--out", str(tmp_path / "ds3"), "--prepare-only"]) == 0


def test_gaussian_ply_roundtrip(tmp_path):
    rng = np.random.default_rng(0)
    n = 50
    means = rng.normal(size=(n, 3))
    rgb = rng.uniform(size=(n, 3))
    path = tmp_path / "splat.ply"
    write_gaussian_ply(path, means, np.log(np.full((n, 3), 0.02)), np.tile([1, 0, 0, 0], (n, 1)),
                       np.zeros(n), rgb_to_sh0(rgb))
    d = read_gaussian_ply(path)
    assert np.allclose(np.c_[d["x"], d["y"], d["z"]], means, atol=1e-6)
    assert np.allclose(sh0_to_rgb(np.c_[d["f_dc_0"], d["f_dc_1"], d["f_dc_2"]]), rgb, atol=1e-6)
    assert list(d)[:3] == ["x", "y", "z"] and "rot_3" in d and "opacity" in d
