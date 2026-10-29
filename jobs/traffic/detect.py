"""Person detectors for J5. Ultralytics models are never used (AGPL-3.0).

Backends
* RTDETRPersonDetector - RT-DETR through transformers (GPU recommended). Code: Apache-2.0
  (transformers); checkpoint license in THIRD_PARTY_LICENSES.md. Install on the GPU job image:
      pip install torch --index-url https://download.pytorch.org/whl/cu121
      pip install "transformers>=4.40" pillow
  Model id: CLEARANCE_RTDETR_MODEL, default "PekingU/rtdetr_r50vd" (VERIFY on the HF hub).
* HOGPersonDetector - OpenCV's HOG + linear SVM people detector (Apache-2.0, CPU). Part of the main
  module in OpenCV 4.x and of the contrib build in OpenCV 5.
* MotionBlobDetector - background subtraction + upright-blob filter for fixed observer cameras
  (OpenCV MOG2, CPU). Fast and reliable on the blurred, low-fps observer video; it reports anything
  person-sized that moves, so it suits scenes where people are the main moving objects.
All return an (N, 5) float array [x0, y0, x1, y1, score] in pixels.
"""
from __future__ import annotations

import abc
import importlib.util
import logging
import os
from typing import Optional

import cv2
import numpy as np

log = logging.getLogger(__name__)
DEFAULT_RTDETR = "PekingU/rtdetr_r50vd"  # VERIFY id and license


def nms(dets: np.ndarray, iou_thr: float = 0.45) -> np.ndarray:
    if len(dets) == 0:
        return dets.reshape(0, 5)
    boxes = [[float(d[0]), float(d[1]), float(d[2] - d[0]), float(d[3] - d[1])] for d in dets]
    keep = cv2.dnn.NMSBoxes(boxes, [float(s) for s in dets[:, 4]], 0.0, iou_thr)
    keep = np.asarray(keep).reshape(-1)
    return dets[keep] if len(keep) else dets[:0]


class PersonDetector(abc.ABC):
    name = "person_detector"

    @classmethod
    def available(cls) -> bool:
        return True

    def reset(self) -> None:
        """Called before each new camera stream (stateful backends clear their model)."""

    @abc.abstractmethod
    def detect(self, frame_bgr: np.ndarray) -> np.ndarray:
        ...


_HOG_PROBE: Optional[tuple] = None


def _hog_api(refresh: bool = False):
    """(HOGDescriptor class, default people SVM getter) when this OpenCV build has a working HOG
    people detector, else None. OpenCV 4.x ships HOG in the main module; OpenCV 5 moved it to the
    contrib build (opencv-contrib-python-headless, VERIFY the exact module path there).

    The probe builds a descriptor and loads the default SVM once, so a build that exposes the names
    but cannot run them (partial contrib installs, lazily loaded submodules that raise on access)
    counts as unavailable instead of failing later; the result is cached per process."""
    global _HOG_PROBE
    if _HOG_PROBE is not None and not refresh:
        return _HOG_PROBE[0]
    api = None
    for name in ("", "xobjdetect", "objdetect"):
        try:
            mod = getattr(cv2, name) if name else cv2
            hog_cls = getattr(mod, "HOGDescriptor", None)
            if hog_cls is None:
                continue
            get = getattr(mod, "HOGDescriptor_getDefaultPeopleDetector", None) or \
                getattr(hog_cls, "getDefaultPeopleDetector", None)
            if get is None:
                continue
            svm = np.asarray(get(), dtype=np.float32).reshape(-1)
            if svm.size == 0:
                continue
            hog_cls().setSVMDetector(svm)
            api = (hog_cls, get)
            break
        except Exception as e:  # AttributeError, ImportError from lazy loaders, cv2.error, ...
            log.debug("HOG probe via cv2.%s failed: %s", name or "<main>", e)
    _HOG_PROBE = (api,)
    return api


class HOGPersonDetector(PersonDetector):
    name = "hog"

    @classmethod
    def available(cls) -> bool:
        return _hog_api() is not None

    def __init__(self, min_person_px: int = 128, win_stride: int = 8, scale: float = 1.05,
                 upscale_to: int = 720):
        api = _hog_api()
        if api is None:
            raise RuntimeError(f"OpenCV {cv2.__version__} has no HOGDescriptor (moved to the contrib build in "
                               "OpenCV 5); install opencv-contrib-python-headless or use the 'motion' detector")
        hog_cls, people = api
        self.hog = hog_cls()
        self.hog.setSVMDetector(people())
        self.win_stride = win_stride
        self.scale = scale
        self.upscale_to = upscale_to
        self.min_person_px = min_person_px

    def detect(self, frame_bgr: np.ndarray) -> np.ndarray:
        h, w = frame_bgr.shape[:2]
        if h < 1 or w < 1:
            return np.zeros((0, 5))
        s = max(1.0, self.upscale_to / h) if h < self.upscale_to else 1.0
        img = cv2.resize(frame_bgr, (int(w * s), int(h * s))) if s > 1.0 else frame_bgr
        if img.shape[0] < 128 or img.shape[1] < 64:  # smaller than the 64x128 detection window
            return np.zeros((0, 5))
        rects, weights = self.hog.detectMultiScale(img, winStride=(self.win_stride, self.win_stride),
                                                   padding=(8, 8), scale=self.scale)
        if len(rects) == 0:
            return np.zeros((0, 5))
        weights = np.asarray(weights, dtype=np.float64).reshape(-1)
        out = []
        for (x, y, rw, rh), wt in zip(rects, weights):
            # HOG boxes have ~10% padding around the person; tighten so the bottom edge is the feet.
            px, py = 0.15 * rw, 0.06 * rh
            score = 1.0 / (1.0 + np.exp(-2.0 * (wt - 0.3)))
            out.append([(x + px) / s, (y + py) / s, (x + rw - px) / s, (y + rh - py) / s, score])
        return nms(np.asarray(out))


class MotionBlobDetector(PersonDetector):
    name = "motion"

    def __init__(self, min_height_frac: float = 0.06, aspect_range=(1.1, 5.0), history: int = 500,
                 var_threshold: float = 25.0, learning_rate: float = 0.004, warmup_frames: int = 16):
        """Background model: the per-pixel median of the first `warmup_frames` frames (people walking
        through during warmup do not leave ghosts), then slow updates (`learning_rate` per frame) so
        people walking toward/away from the camera, who barely move in the image, are not absorbed.
        No detections are reported during warmup."""
        self.min_height_frac = min_height_frac
        self.aspect_range = aspect_range
        self.history = history
        self.var_threshold = var_threshold
        self.learning_rate = learning_rate
        self.warmup_frames = max(1, int(warmup_frames))
        self.reset()

    def reset(self) -> None:
        self.bg = cv2.createBackgroundSubtractorMOG2(history=self.history, varThreshold=self.var_threshold,
                                                     detectShadows=False)
        self.n = 0
        self._warm: list[np.ndarray] = []

    def detect(self, frame_bgr: np.ndarray) -> np.ndarray:
        h, w = frame_bgr.shape[:2]
        self.n += 1
        if self.n <= self.warmup_frames:
            self._warm.append(frame_bgr.copy())
            if self.n == self.warmup_frames:
                med = np.median(np.stack(self._warm), axis=0).astype(np.uint8)
                for k in range(1, 11):
                    self.bg.apply(med, learningRate=1.0 / k)
                self._warm = []
            return np.zeros((0, 5))
        fg = self.bg.apply(frame_bgr, learningRate=self.learning_rate)
        fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
        n, _, stats, _ = cv2.connectedComponentsWithStats((fg > 0).astype(np.uint8), connectivity=8)
        out = []
        for i in range(1, n):
            x, y, bw, bh, area = stats[i]
            if bh < self.min_height_frac * h:
                continue
            aspect = bh / max(bw, 1)
            if not (self.aspect_range[0] <= aspect <= self.aspect_range[1]):
                continue
            fill = area / float(bw * bh)
            if fill < 0.3:
                continue
            out.append([x, y, x + bw, y + bh, float(np.clip(0.5 + 0.5 * fill, 0, 0.95))])
        return nms(np.asarray(out, dtype=np.float64)) if out else np.zeros((0, 5))


class RTDETRPersonDetector(PersonDetector):
    name = "rtdetr"

    def __init__(self, model_id: Optional[str] = None, threshold: float = 0.3, device: Optional[str] = None):
        self.model_id = model_id or os.environ.get("CLEARANCE_RTDETR_MODEL", DEFAULT_RTDETR)
        self.threshold = threshold
        self.device = device
        self._m = None

    @classmethod
    def available(cls) -> bool:
        try:
            return all(importlib.util.find_spec(m) is not None for m in ("torch", "transformers"))
        except (ImportError, ValueError):  # half-installed package without a module spec
            return False

    @classmethod
    def gpu_available(cls) -> bool:
        if not cls.available():
            return False
        try:
            import torch
            return bool(torch.cuda.is_available())
        except Exception as e:  # broken CUDA / torch install: treat as no GPU
            log.warning("torch present but unusable (%s); RT-DETR skipped", e)
            return False

    def _load(self):
        if self._m is None:
            import torch
            from transformers import RTDetrForObjectDetection, RTDetrImageProcessor

            self.device = self.device or ("cuda" if torch.cuda.is_available() else "cpu")
            proc = RTDetrImageProcessor.from_pretrained(self.model_id)
            model = RTDetrForObjectDetection.from_pretrained(self.model_id).to(self.device).eval()
            person_ids = {int(k) for k, v in model.config.id2label.items() if str(v).lower() == "person"}
            if not person_ids:
                raise RuntimeError(f"{self.model_id} has no 'person' class")
            self._m = (proc, model, person_ids)
        return self._m

    def detect(self, frame_bgr: np.ndarray) -> np.ndarray:
        import torch
        from PIL import Image

        proc, model, person_ids = self._load()
        h, w = frame_bgr.shape[:2]
        inputs = proc(images=Image.fromarray(np.ascontiguousarray(frame_bgr[:, :, ::-1])), return_tensors="pt").to(self.device)
        with torch.no_grad():
            outputs = model(**inputs)
        res = proc.post_process_object_detection(outputs, target_sizes=torch.tensor([(h, w)]),
                                                 threshold=self.threshold)[0]
        out = [[*box.tolist(), float(score)] for box, score, lab in
               zip(res["boxes"].cpu(), res["scores"].cpu(), res["labels"].cpu()) if int(lab) in person_ids]
        return np.asarray(out, dtype=np.float64).reshape(-1, 5)


def make_person_detector(name: Optional[str] = None) -> PersonDetector:
    """auto (env CLEARANCE_TRAFFIC_DETECTOR): RT-DETR when torch/transformers + GPU exist, else HOG
    when this OpenCV build has it, else the motion-blob detector. An explicit "hog" on a build
    without HOG (the default opencv-python-headless 5.x wheel, also on Linux CI) falls back to the
    motion-blob detector with a warning instead of failing the traffic job."""
    name = (name or os.environ.get("CLEARANCE_TRAFFIC_DETECTOR", "auto") or "auto").strip().lower()
    if name == "auto":
        if RTDETRPersonDetector.gpu_available():
            name = "rtdetr"
        else:
            name = "hog" if HOGPersonDetector.available() else "motion"
    if name == "rtdetr":
        return RTDETRPersonDetector()
    if name == "hog":
        if HOGPersonDetector.available():
            return HOGPersonDetector()
        log.warning("OpenCV %s has no HOG people detector; using the motion-blob detector", cv2.__version__)
        return MotionBlobDetector()
    if name == "motion":
        return MotionBlobDetector()
    raise ValueError(f"unknown person detector {name!r} (auto|rtdetr|hog|motion)")
