"""Grounding DINO (open-vocabulary boxes) + SAM 2 (masks) backend. GPU recommended.

Licenses: Grounding DINO (IDEA-Research) Apache-2.0; SAM 2 (Meta) Apache-2.0; transformers Apache-2.0.
Checkpoint licenses: see THIRD_PARTY_LICENSES.md.

Install (on the GPU job image, NOT in the dev venv; weights go to ~/.cache/clearance or the
job's cache volume via HF_HOME):

    pip install torch --index-url https://download.pytorch.org/whl/cu121   # match the CUDA version
    pip install "transformers>=4.40" pillow
    pip install "git+https://github.com/facebookresearch/sam2.git"          # SAM 2 predictor
    export HF_HOME=/cache/hf

Model ids (VERIFY availability on the HF hub before relying on them):
    CLEARANCE_GDINO_MODEL  default "IDEA-Research/grounding-dino-tiny"
    CLEARANCE_SAM2_MODEL   default "facebook/sam2-hiera-tiny"
If SAM 2 is not importable the backend still runs and uses (eroded) boxes as masks, with a
confidence penalty, because box masks bleed into the background.
"""
from __future__ import annotations

import importlib.util
import logging
import os
from typing import Optional

import numpy as np

from jobs.semantics.detectors.base import Detection2D, ImageDetector
from jobs.semantics.primitives import PROMPT_LABELS, normalize_label

log = logging.getLogger(__name__)

DEFAULT_GDINO = "IDEA-Research/grounding-dino-tiny"  # VERIFY
DEFAULT_SAM2 = "facebook/sam2-hiera-tiny"             # VERIFY


class GroundingDinoSAM2(ImageDetector):
    name = "gdino_sam2"

    def __init__(self, prompts: Optional[list[str]] = None, box_threshold: float = 0.35,
                 text_threshold: float = 0.25, device: Optional[str] = None,
                 gdino_model: Optional[str] = None, sam2_model: Optional[str] = None,
                 box_mask_penalty: float = 0.7):
        self.prompts = prompts or list(PROMPT_LABELS)
        self.box_threshold = box_threshold
        self.text_threshold = text_threshold
        self.device = device
        self.gdino_model = gdino_model or os.environ.get("CLEARANCE_GDINO_MODEL", DEFAULT_GDINO)
        self.sam2_model = sam2_model or os.environ.get("CLEARANCE_SAM2_MODEL", DEFAULT_SAM2)
        self.box_mask_penalty = box_mask_penalty
        self._gd = None
        self._sam = None
        self._sam_tried = False

    @classmethod
    def available(cls) -> bool:
        return all(importlib.util.find_spec(m) is not None for m in ("torch", "transformers"))

    @classmethod
    def gpu_available(cls) -> bool:
        if not cls.available():
            return False
        import torch
        return bool(torch.cuda.is_available())

    # ---- lazy model loading ----
    def _load(self):
        if self._gd is not None:
            return
        import torch
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

        self.device = self.device or ("cuda" if torch.cuda.is_available() else "cpu")
        proc = AutoProcessor.from_pretrained(self.gdino_model)
        model = AutoModelForZeroShotObjectDetection.from_pretrained(self.gdino_model).to(self.device).eval()
        self._gd = (proc, model)

    def _load_sam(self):
        if self._sam_tried:
            return self._sam
        self._sam_tried = True
        try:
            from sam2.sam2_image_predictor import SAM2ImagePredictor
            self._sam = SAM2ImagePredictor.from_pretrained(self.sam2_model, device=self.device)
        except Exception as e:  # missing package or weights: degrade to box masks
            log.warning("SAM 2 unavailable (%s); using box masks", e)
            self._sam = None
        return self._sam

    def _text_prompt(self) -> str:
        # Grounding DINO expects lower-case phrases separated by " . " and ending with "."
        return " . ".join(p.lower() for p in self.prompts) + " ."

    def detect_frame(self, image_bgr: np.ndarray, frame: str) -> list[Detection2D]:
        import torch
        from PIL import Image

        self._load()
        proc, model = self._gd
        rgb = np.ascontiguousarray(image_bgr[:, :, ::-1])
        h, w = rgb.shape[:2]
        inputs = proc(images=Image.fromarray(rgb), text=self._text_prompt(), return_tensors="pt").to(self.device)
        with torch.no_grad():
            outputs = model(**inputs)
        kwargs = dict(text_threshold=self.text_threshold, target_sizes=[(h, w)])
        try:  # transformers renamed box_threshold -> threshold
            res = proc.post_process_grounded_object_detection(outputs, inputs.input_ids,
                                                              threshold=self.box_threshold, **kwargs)[0]
        except TypeError:
            res = proc.post_process_grounded_object_detection(outputs, inputs.input_ids,
                                                              box_threshold=self.box_threshold, **kwargs)[0]
        labels = res.get("text_labels", res.get("labels"))
        boxes = res["boxes"].detach().cpu().numpy()
        scores = res["scores"].detach().cpu().numpy()
        dets = []
        for box, score, lab in zip(boxes, scores, labels):
            label = self._match_prompt(str(lab))
            if label is None:
                continue
            dets.append(Detection2D(frame=frame, label=label, score=float(score),
                                    box=tuple(float(v) for v in box)))
        if not dets:
            return dets
        sam = self._load_sam()
        if sam is None:
            for d in dets:
                d.score *= self.box_mask_penalty
            return dets
        with torch.inference_mode():
            sam.set_image(rgb)
            for d in dets:
                masks, mscores, _ = sam.predict(box=np.array(d.box), multimask_output=False)
                d.mask = np.asarray(masks[0]) > 0.5
                d.score *= float(np.clip(np.asarray(mscores).reshape(-1)[0], 0.0, 1.0)) ** 0.5
        return dets

    def _match_prompt(self, text: str) -> Optional[str]:
        """Grounding DINO can return merged phrases ('chair table'); map to one prompt label."""
        t = normalize_label(text)
        if t in [normalize_label(p) for p in self.prompts]:
            return t
        for p in sorted(self.prompts, key=len, reverse=True):
            if normalize_label(p) in t:
                return normalize_label(p)
        return None
