"""Frame sources for observer captures: a video file (mp4/webm) or a directory of frames."""
from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

import cv2
import numpy as np

from clearance_core.schemas import Capture

log = logging.getLogger(__name__)
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".bmp")


def capture_start_epoch(capture: Capture) -> Optional[float]:
    if not capture.started_at:
        return None
    try:
        return datetime.fromisoformat(capture.started_at.replace("Z", "+00:00")).timestamp()
    except ValueError:
        log.warning("unparseable started_at %r", capture.started_at)
        return None


def iter_video(path: Path, target_fps: float = 8.0) -> Iterator[tuple[float, int, np.ndarray]]:
    """Yield (t_seconds, frame_index, BGR frame) subsampled to about target_fps."""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise IOError(f"cannot open video {path}")
    native = cap.get(cv2.CAP_PROP_FPS)
    if not (0 < native <= 240):  # MediaRecorder webm often reports 0 or 1000
        native = 10.0
    step = max(1, int(round(native / target_fps)))
    idx, last_t = 0, -1.0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % step == 0:
                ms = cap.get(cv2.CAP_PROP_POS_MSEC)
                t = ms / 1000.0 if ms and ms > 0 else idx / native
                if t <= last_t:  # unreliable container timestamps
                    t = idx / native
                last_t = t
                yield t, idx, frame
            idx += 1
    finally:
        cap.release()


def _natural_key(name: str):
    """frame_2.jpg before frame_10.jpg (plain sorting puts 10 first when names are not zero-padded)."""
    return [int(tok) if tok.isdigit() else tok.lower() for tok in re.split(r"(\d+)", name)]


def iter_frame_dir(d: Path, fps: float, target_fps: float = 8.0,
                   timestamps: Optional[dict[str, float]] = None) -> Iterator[tuple[float, int, np.ndarray]]:
    """Frames in time order: by their timestamp when one is known for every frame, else by natural
    file-name order. With missing, non-finite or repeated timestamps the file order is kept."""
    files = sorted((p for p in d.iterdir() if p.suffix.lower() in IMAGE_EXT), key=lambda p: _natural_key(p.name))
    times = None
    if timestamps:
        ts = [timestamps.get(p.name) for p in files]
        if all(v is not None and math.isfinite(v) for v in ts) and len(set(ts)) == len(ts):
            order = sorted(range(len(files)), key=lambda k: ts[k])
            files, times = [files[k] for k in order], [ts[k] for k in order]
        elif any(v is not None for v in ts):
            log.warning("frame timestamps in %s are incomplete or repeated; using file order", d)
            times = [float(v) if v is not None and math.isfinite(v) else k / fps for k, v in enumerate(ts)]
    step = max(1, int(round(fps / target_fps)))
    for i, p in enumerate(files):
        if i % step:
            continue
        img = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if img is None:
            continue
        t = times[i] if times is not None else i / fps
        yield float(t), i, img


def _frame_timestamps(capture_dir: Path) -> Optional[dict[str, float]]:
    for name in ("frame_times.json", "timestamps.json"):
        p = capture_dir / name
        if p.exists():
            return {str(k): float(v) for k, v in json.loads(p.read_text(encoding="utf-8")).items()}
    p = capture_dir / "poses.jsonl"
    if p.exists():
        out = {}
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                out[rec["frame"]] = float(rec["t"])
        return out or None
    return None


def iter_capture_frames(capture_dir: str | Path, capture: Capture, target_fps: float = 8.0):
    d = Path(capture_dir)
    if capture.files.video and (d / capture.files.video).exists():
        yield from iter_video(d / capture.files.video, target_fps)
        return
    if capture.files.frames and (d / capture.files.frames).is_dir():
        fdir = d / capture.files.frames
        n = sum(1 for p in fdir.iterdir() if p.suffix.lower() in IMAGE_EXT)
        fps = n / capture.duration_s if capture.duration_s > 0 and n else 10.0
        yield from iter_frame_dir(fdir, fps, target_fps, _frame_timestamps(d))
        return
    raise FileNotFoundError(f"observer capture {d} has neither a video nor a frames directory")
