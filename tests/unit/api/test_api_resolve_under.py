"""resolve_under must not mistake a Windows extended-length path (seen while a file is being replaced) for
an escape from the storage root, and must still refuse real escapes."""
from __future__ import annotations

from pathlib import Path

import pytest

from services.api import security
from services.api.security import BadKey, resolve_under

BS = chr(92)
EXTENDED = BS + BS + "?" + BS


def test_extended_length_prefix_is_stripped(monkeypatch):
    real = Path.resolve

    def resolve(self, strict=False):
        r = real(self, strict)
        return Path(EXTENDED + str(r)) if r.drive and "upload" in r.parts else r

    monkeypatch.setattr(Path, "resolve", resolve)
    p = security._resolve(Path.cwd() / "upload")
    assert not str(p).startswith(EXTENDED)


def test_resolve_under_with_extended_paths(tmp_path, monkeypatch):
    real = Path.resolve
    (tmp_path / "captures" / "c" / "upload").mkdir(parents=True)

    def resolve(self, strict=False):
        r = real(self, strict)
        return Path(EXTENDED + str(r)) if r.drive and r.name == "upload" else r

    monkeypatch.setattr(Path, "resolve", resolve)
    p = resolve_under(tmp_path, "captures/c/upload/part_0000.zip")
    assert p.name == "part_0000.zip" and Path(str(tmp_path.resolve())) in p.parents


def test_real_escapes_still_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
        (root / "f.txt").symlink_to(outside / "x.txt")
    except OSError:
        pytest.skip("symlinks need extra privileges on this system")
    with pytest.raises(BadKey):
        resolve_under(root, "link/x.txt")
    with pytest.raises(BadKey):
        resolve_under(root, "f.txt")
    with pytest.raises(BadKey):
        resolve_under(root, "../outside/x.txt")
