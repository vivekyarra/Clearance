"""Safe image file I/O and path handling for uploaded captures.

- imread / imwrite work with any path (OpenCV's own cannot open non-ASCII paths on Windows).
- imread checks the image header size before decoding, so a small file that declares a huge image
  (decompression bomb) is refused instead of allocating gigabytes.
- FrameFiles looks per-frame files up by stem without regard to case (Linux is case sensitive,
  Windows and macOS usually are not).
- confined_path resolves a path taken from an uploaded capture.json and refuses anything that leaves
  the capture root (absolute paths, drive letters, '..', symlinks pointing outside).
"""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath, PureWindowsPath

# OpenCV reads this once, on first decode; keep it well below its 2^30 default
os.environ.setdefault("OPENCV_IO_MAX_IMAGE_PIXELS", str(4096 * 4096))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

MAX_COLOR_WH = (4096, 4096)
MAX_DEPTH_WH = (1024, 1024)
SIZE_TOLERANCE = 1.05


class UnsafePath(ValueError):
    """A path from uploaded data points outside the directory it must stay in."""


def image_size(path) -> tuple[int, int] | None:
    """(width, height) from the file header without decoding the pixels; None if unreadable."""
    try:
        from PIL import Image
        with Image.open(str(path)) as im:
            return int(im.size[0]), int(im.size[1])
    except Exception:  # noqa: BLE001 - any unreadable / unknown file is simply not an image
        return None


def imread(path, flags: int = cv2.IMREAD_UNCHANGED, *, max_wh: tuple[int, int] | None = MAX_COLOR_WH,
           expect_wh: tuple[int, int] | None = None) -> np.ndarray | None:
    """Decode an image file. None when it is missing, empty, not decodable, larger than max_wh, or larger
    than expect_wh (times SIZE_TOLERANCE) when given. Sizes are checked from the header first."""
    p = Path(path)
    try:
        if not p.is_file() or p.stat().st_size == 0:
            return None
    except OSError:
        return None
    wh = image_size(p)
    if wh is None:
        return None
    limits = [lim for lim in (max_wh, expect_wh) if lim is not None]
    for lw, lh in limits:
        tol = SIZE_TOLERANCE if (lw, lh) == expect_wh else 1.0
        if wh[0] > lw * tol or wh[1] > lh * tol:
            return None
    try:
        buf = np.fromfile(str(p), dtype=np.uint8)
        img = cv2.imdecode(buf, flags)
    except (OSError, ValueError, cv2.error):
        return None
    return img


def imwrite(path, img: np.ndarray, params=None) -> bool:
    """cv2.imwrite for any path. The format follows the file extension."""
    ext = Path(path).suffix or ".png"
    ok, buf = cv2.imencode(ext, img, params or [])
    if not ok:
        return False
    buf.tofile(str(path))
    return True


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def confined_path(root, rel: str) -> Path:
    """root / rel for a relative path from uploaded data. Raises UnsafePath for absolute paths, drive
    letters, '..' components, or a result (after following symlinks) outside root. Components that do
    not exist with the exact case are matched without regard to case. The path need not exist."""
    root = Path(root)
    s = str(rel)
    if not s.strip() or "\x00" in s:
        raise UnsafePath(f"empty or invalid path {rel!r}")
    win, posix = PureWindowsPath(s), PurePosixPath(s.replace("\\", "/"))
    if win.drive or win.root or posix.is_absolute():
        raise UnsafePath(f"absolute path {rel!r} is not allowed")
    parts = [p for p in posix.parts if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise UnsafePath(f"path {rel!r} leaves its directory")
    cur = root
    for part in parts:
        nxt = cur / part
        if not nxt.exists() and cur.is_dir():
            low = part.lower()
            nxt = next((c for c in sorted(cur.iterdir()) if c.name.lower() == low), nxt)
        cur = nxt
    if not _inside(cur, root):
        raise UnsafePath(f"path {rel!r} resolves outside {root}")
    return cur


class FrameFiles:
    """Files of one per-frame directory (depth/, confidence/, frames/, reldepth/), looked up by frame
    stem and extension without regard to case: '000001.PNG' answers a lookup for ('000001', '.png').
    Only the stem of the frame name is used, and files whose real location is outside the directory
    (symlinks) are ignored."""

    def __init__(self, directory):
        self.dir = Path(directory)
        self._index: dict[str, Path] | None = None

    def _build(self) -> dict[str, Path]:
        idx: dict[str, Path] = {}
        if self.dir.is_dir():
            for p in sorted(self.dir.iterdir()):
                if p.is_file() and _inside(p, self.dir):
                    idx.setdefault(p.name.lower(), p)
        return idx

    def find(self, frame_name: str, exts=(".png",)) -> Path | None:
        if self._index is None:
            self._index = self._build()
        stem = PurePosixPath(str(frame_name).replace("\\", "/")).stem
        if not stem or stem in (".", ".."):
            return None
        for e in exts:
            hit = self._index.get(f"{stem}{e}".lower())
            if hit is not None:
                return hit
        return None
