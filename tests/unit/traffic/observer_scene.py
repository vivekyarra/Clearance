"""Reference observer recordings for J5 tests: people as upright boxes walking straight lines,
rendered from fixed, marker-localized cameras into an mp4 or a frames directory."""
from __future__ import annotations

import json
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np

from clearance_core.schemas import Capture, CaptureFiles, Device
from jobs.semantics.camera import PinholeCamera, look_at_arkit

K = [260.0, 260.0, 160.0, 120.0]
W, H = 320, 240
T0 = datetime(2026, 10, 27, 10, 0, 0, tzinfo=timezone.utc)


@dataclass
class Person:
    p0: tuple
    p1: tuple
    speed: float
    start: float
    height: float = 1.7
    width: float = 0.5

    @property
    def end(self) -> float:
        return self.start + np.linalg.norm(np.subtract(self.p1, self.p0)) / self.speed

    def at(self, t):
        if not (self.start <= t <= self.end):
            return None
        f = (t - self.start) / (self.end - self.start)
        return np.asarray(self.p0) + f * (np.asarray(self.p1) - np.asarray(self.p0))


def background(seed=0):
    rng = np.random.default_rng(seed)
    img = np.zeros((H, W, 3), np.uint8)
    img[:] = (185, 190, 180)
    img[int(H * 0.45):] = (150, 155, 160)
    noise = rng.integers(-12, 12, (H, W, 1))
    img = np.clip(img.astype(int) + noise, 0, 255).astype(np.uint8)
    return cv2.GaussianBlur(img, (3, 3), 0)


def render_frame(cam: PinholeCamera, people, t, bg):
    img = bg.copy()
    items = []
    for k, p in enumerate(people):
        xy = p.at(t)
        if xy is None:
            continue
        (uf, vf), = cam.project(np.array([[xy[0], xy[1], 0.0]]))[0]
        (uh, vh), = cam.project(np.array([[xy[0], xy[1], p.height]]))[0]
        depth = cam.world_to_cam(np.array([[xy[0], xy[1], p.height / 2]]))[0, 2]
        if depth <= 0.2:
            continue
        half = 0.5 * p.width * cam.K[0, 0] / depth
        items.append((depth, k, uf, vf, vh, half))
    for depth, k, uf, vf, vh, half in sorted(items, reverse=True):  # far first
        color = (40 + 30 * k, 30, 90 - 10 * k)
        cv2.rectangle(img, (int(uf - half), int(vh)), (int(uf + half), int(vf)), color, -1)
    return img


def write_observer(out: Path, eye, target, people, *, duration_s=20.0, fps=8.0, start_offset_s=0.0,
                   as_video=True, pose_convention="arkit", write_pose=True, capture_id=None) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    T = look_at_arkit(eye, target)
    cam = PinholeCamera.from_arkit(T, K, W, H)
    bg = background(zlib.crc32(out.name.encode()) % 1000)  # same scene on every OS and temp-dir layout
    n = int(duration_s * fps)
    frames = [render_frame(cam, people, start_offset_s + i / fps, bg) for i in range(n)]
    files = CaptureFiles(poses=None, observer_pose="observer_pose.json")
    if as_video:
        vw = cv2.VideoWriter(str(out / "observer.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
        for f in frames:
            vw.write(f)
        vw.release()
        files.video = "observer.mp4"
    else:
        (out / "frames").mkdir(exist_ok=True)
        for i, f in enumerate(frames):
            cv2.imwrite(str(out / "frames" / f"{i:05d}.jpg"), f, [cv2.IMWRITE_JPEG_QUALITY, 90])
        files.frames = "frames"
    if write_pose:
        M = T if pose_convention == "arkit" else T @ np.diag([1.0, -1.0, -1.0, 1.0])
        (out / "observer_pose.json").write_text(json.dumps({"T_site_cam": M.ravel().tolist(), "K": K, "w": W, "h": H}))
    cap = Capture(capture_id=capture_id or out.name, site_id="site_ref",
                  device=Device(model="reference-observer", tier="T3_FIXED_OBSERVER"), role="observer", files=files,
                  started_at=(T0 + timedelta(seconds=start_offset_s)).isoformat().replace("+00:00", "Z"),
                  duration_s=duration_s)
    (out / "capture.json").write_text(cap.model_dump_json(indent=1))
    return out
