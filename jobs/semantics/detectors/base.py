"""Detector interface for J4.

Two kinds of backends:
* ImageDetector: per-keyframe 2D detections + masks (e.g. Grounding DINO + SAM 2). The shared
  base class lifts masks to 3D with depth/pose, votes across views and clusters per label.
* geometry detectors (GeometricDetector) produce 3D objects straight from the reconstruction.
Both return `Object3D`s, which run.py turns into twin primitives.
"""
from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

from clearance_core.grid import Grid
from clearance_core.schemas import Capture, PoseRecord

log = logging.getLogger(__name__)


@dataclass
class Detection2D:
    frame: str
    label: str
    score: float
    box: tuple[float, float, float, float]  # x0, y0, x1, y1 in pixels of the frame image
    mask: Optional[np.ndarray] = None        # bool HxW at the frame image size; None = use box


@dataclass
class Object3D:
    label: str
    points: np.ndarray                 # (N,3) site-frame points supporting the object
    score: float                       # detector confidence in [0,1]
    n_views: int = 1
    frames: list[str] = field(default_factory=list)
    shape: Optional[str] = None        # force "box"/"cylinder", else decided by label/footprint
    height: Optional[float] = None     # force a height (heuristic backends)
    backend: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class SceneContext:
    capture_dir: Path
    capture: Capture
    poses: list[PoseRecord]                 # all poses
    keyframes: list[PoseRecord]             # selected keyframes
    grid: Optional[Grid] = None             # twin occupancy grid
    cloud: Optional[np.ndarray] = None      # (N,3) site-frame point cloud if any
    height_band: tuple[float, float] = (0.03, 2.0)

    def read_frame(self, frame: str):
        from jobs.semantics.capture_io import read_frame
        return read_frame(self.capture_dir, self.capture, frame)

    def read_depth(self, frame: str):
        from jobs.semantics.capture_io import read_depth
        return read_depth(self.capture_dir, self.capture, frame)

    @property
    def has_depth(self) -> bool:
        return bool(self.capture.files.depth) and (self.capture_dir / self.capture.files.depth).is_dir()


class Detector(abc.ABC):
    name: str = "detector"

    @classmethod
    def available(cls) -> bool:
        """True if the backend's dependencies are importable (cheap check, no model load)."""
        return True

    def can_run(self, ctx: SceneContext) -> tuple[bool, str]:
        return True, ""

    @abc.abstractmethod
    def detect(self, ctx: SceneContext) -> list[Object3D]:
        ...


class ImageDetector(Detector):
    """Subclasses implement detect_frame(); lifting/voting/clustering is shared."""
    prompts: list[str]
    voxel_m: float = 0.04
    min_view_ratio: float = 0.25
    cluster_eps_m: float = 0.10

    @abc.abstractmethod
    def detect_frame(self, image_bgr: np.ndarray, frame: str) -> list[Detection2D]:
        ...

    def can_run(self, ctx: SceneContext) -> tuple[bool, str]:
        if not ctx.capture.files.frames:
            return False, "capture has no frames"
        if not ctx.has_depth and ctx.cloud is None:
            return False, "no depth maps and no point cloud to lift masks into 3D"
        return True, ""

    def detect(self, ctx: SceneContext) -> list[Object3D]:
        from jobs.semantics.lift import LiftInput, lift_and_cluster

        inputs: list[LiftInput] = []
        for pose in ctx.keyframes:
            img = ctx.read_frame(pose.frame)
            if img is None:
                log.warning("missing frame %s", pose.frame)
                continue
            dets = self.detect_frame(img, pose.frame)
            depth = ctx.read_depth(pose.frame) if ctx.has_depth else None
            inputs.append(LiftInput(pose=pose, image_hw=img.shape[:2], detections=dets, depth=depth))
        return lift_and_cluster(inputs, cloud=ctx.cloud, voxel=self.voxel_m,
                                min_view_ratio=self.min_view_ratio, eps=self.cluster_eps_m,
                                height_band=ctx.height_band, backend=self.name)
