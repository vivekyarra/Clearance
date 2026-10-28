import math

import numpy as np

from jobs.semantics.camera import PinholeCamera
from jobs.semantics.detectors.base import Detection2D, ImageDetector
from jobs.semantics.lift import LiftInput, detection_mask, lift_and_cluster, zbuffer_visible
from jobs.semantics.primitives import fit_primitive

from .scene_builder import RefBox, RefCyl, object_points, orbit_poses, render

SCENE = [RefBox("table", 0.3, 0.2, 0.4, 1.0, 0.6, 0.74), RefCyl("trash bin", -0.9, -0.6, 0.4, 0.7)]


def yaw_err(a, b):
    d = (a - b) % math.pi
    return min(d, math.pi - d)


class MaskFromRenderDetector(ImageDetector):
    """Test double: perfect masks from the analytic renderer, optional extra spurious masks."""
    name = "render_masks"

    def __init__(self, poses, objects, spurious=None):
        self.poses = {p.frame: p for p in poses}
        self.objects = objects
        self.spurious = spurious or {}
        self.prompts = [o.label for o in objects]

    def detect_frame(self, image_bgr, frame):
        cam = PinholeCamera.from_pose_record(self.poses[frame])
        _, ids = render(cam, self.objects)
        dets = []
        for k, ob in enumerate(self.objects):
            m = ids == k
            if m.sum() > 20:
                ys, xs = np.nonzero(m)
                dets.append(Detection2D(frame, ob.label, 0.9, (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1), m))
        dets += self.spurious.get(frame, [])
        return dets


def build_inputs(poses, objects, with_depth=True, spurious=None):
    det = MaskFromRenderDetector(poses, objects, spurious)
    out = []
    for p in poses:
        cam = PinholeCamera.from_pose_record(p)
        z, _ = render(cam, objects)
        out.append(LiftInput(p, (cam.h, cam.w), det.detect_frame(None, p.frame), z if with_depth else None))
    return out


def check_objects(objs):
    labels = sorted(o.label for o in objs)
    assert labels == ["table", "trash bin"]
    by = {o.label: o for o in objs}
    t = fit_primitive(by["table"].points, "table", prim_id="T1", confidence=by["table"].score)
    assert yaw_err(t.pose.yaw, 0.4) < math.radians(4)
    assert abs(t.size[0] - 1.0) < 0.06 and abs(t.size[1] - 0.6) < 0.06 and abs(t.size[2] - 0.74) < 0.05
    assert np.hypot(t.pose.xyz[0] - 0.3, t.pose.xyz[1] - 0.2) < 0.04
    b = fit_primitive(by["trash bin"].points, "trash bin", prim_id="TB1", confidence=0.9)
    assert b.type == "cylinder" and abs(b.size[0] - 0.4) < 0.06
    assert np.hypot(b.pose.xyz[0] + 0.9, b.pose.xyz[1] + 0.6) < 0.05
    assert by["table"].n_views >= 6


def test_lift_with_depth_multi_view():
    poses = orbit_poses((0, 0), 2.6, 8)
    objs = lift_and_cluster(build_inputs(poses, SCENE), voxel=0.03)
    check_objects(objs)


def test_lift_from_point_cloud_without_depth():
    poses = orbit_poses((0, 0), 2.6, 8)
    rng = np.random.default_rng(0)
    floor = np.c_[rng.uniform(-3, 3, (20000, 2)), np.zeros(20000)]
    cloud = np.concatenate([object_points(o, 6000, rng) for o in SCENE] + [floor])
    objs = lift_and_cluster(build_inputs(poses, SCENE, with_depth=False), cloud=cloud, voxel=0.03)
    check_objects(objs)


def test_single_view_spurious_detection_is_voted_out():
    poses = orbit_poses((0, 0), 2.6, 8)
    cam = PinholeCamera.from_pose_record(poses[3])
    _, ids = render(cam, SCENE)
    m = ids == 0  # table pixels mislabeled as "chair" in one frame only
    ys, xs = np.nonzero(m)
    spur = {poses[3].frame: [Detection2D(poses[3].frame, "chair", 0.8, (xs.min(), ys.min(), xs.max(), ys.max()), m)]}
    objs = lift_and_cluster(build_inputs(poses, SCENE, spurious=spur), voxel=0.03)
    assert "chair" not in {o.label for o in objs}
    check_objects(objs)


def test_detection_mask_box_and_resize():
    d = Detection2D("f", "box", 0.5, (10, 10, 50, 40))
    m = detection_mask(d, (60, 80))
    assert m.shape == (60, 80) and m[25, 30] and not m[5, 5]
    assert m.sum() < 40 * 30  # eroded
    d2 = Detection2D("f", "box", 0.5, (0, 0, 1, 1), mask=np.ones((30, 40), bool))
    assert detection_mask(d2, (60, 80)).shape == (60, 80)


def test_zbuffer_hides_occluded_points():
    poses = orbit_poses((0, 0), 2.6, 1)
    cam = PinholeCamera.from_pose_record(poses[0])
    ray = cam.rays(np.array([[80.5, 60.5]]))[0]  # through a pixel centre
    front = cam.center + ray * 1.0
    back = cam.center + ray * 2.0
    idx, _ = zbuffer_visible(np.array([front, back]), cam, downscale=1)
    assert list(idx) == [0]


def test_empty_inputs():
    assert lift_and_cluster([]) == []
    poses = orbit_poses((0, 0), 2.6, 3)
    inputs = [LiftInput(p, (120, 160), [], None) for p in poses]
    assert lift_and_cluster(inputs) == []
