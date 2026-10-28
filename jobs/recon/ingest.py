"""J1 ingest: load and validate a Capture directory (PLAN 7.2 J1).

Paths in capture.json come from an upload: every one is resolved inside the capture root (see
jobs.common.imageio.confined_path) and the capture is rejected otherwise. Individual bad records are
dropped with a warning (a pose that is not a rigid transform, a feature-point line that does not
parse, a truncated last line); the capture is rejected when too few usable frames remain.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import numpy as np
from pydantic import ValidationError

from clearance_core.schemas import Capture, PoseRecord
from jobs.common.imageio import UnsafePath, confined_path

MAX_LIMITED_FRAC = 0.20
MIN_POSES = 10
MAX_POSES = 50_000
MAX_JSONL_BYTES = 512 * 1024 * 1024
MAX_SPEED_MPS = 3.0  # hand-held scanning; faster pose changes are tracking jumps
FILE_KEYS = ("frames", "poses", "depth", "confidence", "feature_points", "relative_depth")


class CaptureRejected(ValueError):
    """The capture cannot be reconstructed (schema problem, unsafe or missing files, poor tracking)."""


@dataclass
class CaptureData:
    root: Path
    capture: Capture
    raw: dict
    poses: list[PoseRecord]
    feature_points: dict[str, np.ndarray] = field(default_factory=dict)
    limited_frac: float = 0.0
    warnings: list[str] = field(default_factory=list)
    paths: dict[str, Path] = field(default_factory=dict)  # resolved files.* entries
    pose_jumps: list[int] = field(default_factory=list)   # pose indices that start after a tracking jump

    @property
    def tier(self) -> str:
        return self.capture.device.tier

    def dir_of(self, key: str) -> Path | None:
        return self.paths.get(key)

    def T(self, i: int) -> np.ndarray:
        return np.asarray(self.poses[i].T_world_cam, float).reshape(4, 4)

    def usable(self) -> list[int]:
        return [i for i, p in enumerate(self.poses) if p.tracking == "normal"]


def _is_rigid(T: np.ndarray) -> bool:
    if not np.all(np.isfinite(T)):
        return False
    R = T[:3, :3]
    return bool(np.allclose(T[3], [0, 0, 0, 1], atol=1e-6) and abs(np.linalg.det(R) - 1) < 1e-3
                and np.allclose(R.T @ R, np.eye(3), atol=1e-3))


def _safe_frame_name(name: str) -> bool:
    s = str(name).replace("\\", "/")
    if not s or "\x00" in s or s.startswith("/") or ":" in s:
        return False
    return ".." not in PurePosixPath(s).parts


def _read_lines(path: Path, what: str) -> list[str]:
    if path.stat().st_size > MAX_JSONL_BYTES:
        raise CaptureRejected(f"{what} is larger than {MAX_JSONL_BYTES // 2 ** 20} MiB")
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def _resolve_files(root: Path, raw: dict, cap: Capture) -> dict[str, Path]:
    files = dict(raw.get("files") or {})
    paths = {}
    for key in FILE_KEYS:
        rel = files.get(key, getattr(cap.files, key, None))
        if not rel:
            continue
        if not isinstance(rel, str):
            raise CaptureRejected(f"files.{key} must be a relative path string")
        try:
            paths[key] = confined_path(root, rel)
        except UnsafePath as e:
            raise CaptureRejected(f"files.{key}: {e}") from e
    return paths


def load_capture(capture_dir: str | Path, *, max_limited_frac: float = MAX_LIMITED_FRAC) -> CaptureData:
    root = Path(capture_dir)
    cj = root / "capture.json"
    if not cj.is_file():
        raise CaptureRejected(f"{cj} not found")
    try:
        raw = json.loads(cj.read_text(encoding="utf-8"))
        cap = Capture.model_validate(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError) as e:
        raise CaptureRejected(f"capture.json invalid: {e}") from e
    if cap.role != "scanner":
        raise CaptureRejected(f"role {cap.role!r}: only scanner captures are reconstructed")
    paths = _resolve_files(root, raw, cap)
    warnings: list[str] = []

    pf = paths.get("poses") or confined_path(root, "poses.jsonl")
    if not pf.is_file():
        raise CaptureRejected(f"poses file {pf} not found")
    lines = [(ln, s) for ln, s in enumerate(_read_lines(pf, "poses file"), 1) if s.strip()]
    poses: list[PoseRecord] = []
    n_bad = 0
    for k, (ln, line) in enumerate(lines):
        try:
            p = PoseRecord.model_validate_json(line)
        except ValidationError as e:
            if k == len(lines) - 1 and k > 0:  # interrupted write: the last line is cut short
                warnings.append(f"poses line {ln} is truncated and was ignored")
                break
            raise CaptureRejected(f"poses line {ln}: {e.errors()[0]['msg']}") from e
        if not _safe_frame_name(p.frame):
            raise CaptureRejected(f"poses line {ln}: frame name {p.frame!r} is not a plain file name")
        T = np.asarray(p.T_world_cam, float).reshape(4, 4)
        bad_K = not (all(np.isfinite(p.K)) and p.K[0] > 0 and p.K[1] > 0 and p.w > 0 and p.h > 0)
        if not _is_rigid(T) or bad_K:
            n_bad += 1
            p = p.model_copy(update={"tracking": "not_available"})
        poses.append(p)
        if len(poses) > MAX_POSES:
            raise CaptureRejected(f"more than {MAX_POSES} pose records")
    if n_bad:
        warnings.append(f"{n_bad} pose records are not rigid transforms or have invalid intrinsics; "
                        f"they are not used")
    if len(poses) < MIN_POSES:
        raise CaptureRejected(f"only {len(poses)} pose records")
    if len(poses) - sum(p.tracking != "normal" for p in poses) < MIN_POSES:
        raise CaptureRejected("fewer than 10 pose records with normal tracking and a valid transform")

    limited = sum(p.tracking != "normal" for p in poses) / len(poses)
    if limited > max_limited_frac:
        raise CaptureRejected(f"{limited:.0%} of frames have limited tracking or invalid poses "
                              f"(max {max_limited_frac:.0%})")

    for key in ("frames", "depth", "confidence"):
        if key in paths and not paths[key].is_dir():
            raise CaptureRejected(f"files.{key} = {getattr(cap.files, key)!r} but the directory is missing")
    if cap.device.tier == "T1_LIDAR" and "depth" not in paths:
        raise CaptureRejected("T1_LIDAR capture without a depth directory")

    fps: dict[str, np.ndarray] = {}
    fpp = paths.get("feature_points")
    if fpp is not None and fpp.is_file():
        n_bad_fp = 0
        for line in _read_lines(fpp, "feature points file"):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                pts = np.asarray(rec["points_world"], float).reshape(-1, 3)
                name = str(rec["frame"])
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                n_bad_fp += 1
                continue
            fps[name] = pts[np.all(np.isfinite(pts), axis=1)]
        if n_bad_fp:
            warnings.append(f"{n_bad_fp} feature-point lines could not be read and were ignored")
    elif cap.device.tier == "T2_VIO_MONO":
        raise CaptureRejected("T2_VIO_MONO capture needs feature_points to recover metric scale")

    ts = np.array([p.t for p in poses])
    if np.any(np.diff(ts) < 0):
        warnings.append("pose timestamps are not monotonic")

    # tracking jumps (relocalization after tracking loss): implausible speed between usable poses
    jumps = []
    use = [i for i, p in enumerate(poses) if p.tracking == "normal"]
    for a, b in zip(use[:-1], use[1:]):
        dt = max(poses[b].t - poses[a].t, 1e-3)
        dist = float(np.linalg.norm(np.asarray(poses[b].T_world_cam)[[3, 7, 11]]
                                    - np.asarray(poses[a].T_world_cam)[[3, 7, 11]]))
        if dist > 0.25 and dist / dt > MAX_SPEED_MPS:
            jumps.append(b)
    if jumps:
        warnings.append(f"{len(jumps)} pose jumps faster than {MAX_SPEED_MPS} m/s (tracking relocalization); "
                        f"geometry near frames {jumps[:5]} may be doubled")
    return CaptureData(root, cap, raw, poses, fps, limited, warnings, paths, jumps)
