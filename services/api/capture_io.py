"""Capture upload assembly: zip chunks -> capture directory, with zip-slip and archive-size guards."""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from typing import Iterable

from services.api.security import BadKey, safe_key

SKIP_PREFIXES = ("__MACOSX/",)
MAX_ENTRIES = 200_000


class CaptureError(ValueError):
    pass


def chunk_key(capture_id: str, i: int) -> str:
    return f"captures/{capture_id}/upload/part_{i:04d}.zip"


def file_key(capture_id: str, name: str) -> str:
    return f"captures/{capture_id}/capture/{safe_key(name)}"


def capture_root_key(capture_id: str) -> str:
    return f"captures/{capture_id}/capture"


def _entry_name(info: zipfile.ZipInfo) -> str | None:
    """Validated relative name for a zip entry, or None to skip (dirs, OS metadata)."""
    name = info.filename
    if "\\" in name:
        raise CaptureError(f"zip entry uses backslashes: {name!r}")
    if info.is_dir() or name.endswith("/"):
        return None
    if name.startswith(SKIP_PREFIXES) or any(seg.startswith(".") and seg not in (".", "..")
                                             for seg in name.split("/")):
        return None
    try:
        return safe_key(name)
    except BadKey as e:
        raise CaptureError(f"unsafe zip entry {info.filename!r}: {e}") from e


def extract_zip(zpath: Path, dest: Path, *, budget: list[int]) -> list[str]:
    """Extract into dest after validating every entry. budget = [remaining_bytes] shared across parts."""
    try:
        zf = zipfile.ZipFile(zpath)
    except zipfile.BadZipFile as e:
        raise CaptureError(f"{zpath.name} is not a valid zip archive") from e
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ENTRIES:
            raise CaptureError(f"{zpath.name}: too many entries ({len(infos)})")
        plan = []
        for info in infos:
            name = _entry_name(info)
            if name is None:
                continue
            budget[0] -= info.file_size
            if budget[0] < 0:
                raise CaptureError("capture exceeds the maximum extracted size")
            plan.append((info, name))
        dest_r = dest.resolve()
        written = []
        for info, name in plan:
            target = (dest_r / name).resolve()
            if dest_r not in target.parents:
                raise CaptureError(f"zip entry escapes capture dir: {info.filename!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as out:
                copied = 0
                for block in iter(lambda: src.read(1 << 20), b""):
                    copied += len(block)
                    if copied > info.file_size:  # entry larger than its declared size
                        raise CaptureError(f"zip entry larger than declared: {info.filename!r}")
                    out.write(block)
            written.append(name)
        return written


def assemble(parts: Iterable[Path], dest: Path, *, chunking: str, max_bytes: int, work_dir: Path) -> list[str]:
    """independent: each part is its own zip (later parts overwrite earlier files).
    split: parts are consecutive byte ranges of one zip."""
    parts = list(parts)
    dest.mkdir(parents=True, exist_ok=True)
    budget = [int(max_bytes)]
    if not parts:
        return []
    if chunking == "split":
        work_dir.mkdir(parents=True, exist_ok=True)
        joined = work_dir / "joined.zip"
        with joined.open("wb") as out:
            for p in parts:
                with p.open("rb") as f:
                    shutil.copyfileobj(f, out, 1 << 20)
        try:
            return extract_zip(joined, dest, budget=budget)
        finally:
            joined.unlink(missing_ok=True)
    names: list[str] = []
    for p in parts:
        names.extend(extract_zip(p, dest, budget=budget))
    return names


def find_capture_root(base: Path) -> Path:
    """The directory holding capture.json / poses.jsonl (archives often wrap everything in one folder)."""
    markers = ("capture.json", "poses.jsonl")
    if any((base / m).is_file() for m in markers):
        return base
    subdirs = [d for d in base.iterdir() if d.is_dir()] if base.is_dir() else []
    files = [f for f in base.iterdir() if f.is_file()] if base.is_dir() else []
    if len(subdirs) == 1 and not files and any((subdirs[0] / m).is_file() for m in markers):
        return subdirs[0]
    return base
