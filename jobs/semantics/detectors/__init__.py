"""Detector backends for J4. Heavy backends import their dependencies lazily."""
from __future__ import annotations

import logging
import os
from typing import Optional, Union

from jobs.semantics.detectors.base import Detection2D, Detector, ImageDetector, Object3D, SceneContext
from jobs.semantics.detectors.geometric import GeometricDetector

log = logging.getLogger(__name__)

BACKENDS = ("auto", "gdino_sam2", "geometric")


def make_detector(name: str, **kw) -> Detector:
    if name == "geometric":
        return GeometricDetector(**kw)
    if name == "gdino_sam2":
        from jobs.semantics.detectors.gdino_sam2 import GroundingDinoSAM2
        return GroundingDinoSAM2(**kw)
    raise ValueError(f"unknown detector backend {name!r}; choose from {BACKENDS}")


def choose_detector(spec: Union[None, str, Detector], ctx: SceneContext) -> tuple[Detector, str]:
    """Resolve a detector. Returns (detector, reason). `auto` (default, or env
    CLEARANCE_SEMANTICS_DETECTOR) uses Grounding DINO + SAM 2 when torch/transformers and a GPU are
    present (or CLEARANCE_SEMANTICS_ALLOW_CPU_MODELS=1) and the capture can be lifted to 3D;
    otherwise the geometry-based detector."""
    if isinstance(spec, Detector):
        return spec, "explicit instance"
    name = spec or os.environ.get("CLEARANCE_SEMANTICS_DETECTOR", "auto")
    if name != "auto":
        return make_detector(name), f"requested {name}"
    from jobs.semantics.detectors.gdino_sam2 import GroundingDinoSAM2

    allow_cpu = os.environ.get("CLEARANCE_SEMANTICS_ALLOW_CPU_MODELS") == "1"
    if GroundingDinoSAM2.available() and (allow_cpu or GroundingDinoSAM2.gpu_available()):
        det = GroundingDinoSAM2()
        ok, why = det.can_run(ctx)
        if ok:
            return det, "auto: open-vocabulary models available"
        return GeometricDetector(), f"auto: geometry-based ({why})"
    return GeometricDetector(), "auto: geometry-based (torch/transformers or GPU not present)"


__all__ = ["Detection2D", "Detector", "ImageDetector", "Object3D", "SceneContext", "GeometricDetector",
           "make_detector", "choose_detector", "BACKENDS"]
