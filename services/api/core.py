"""Single import point for the shared core package (clearance_core)."""
from __future__ import annotations

from pathlib import Path

from clearance_core import grid, schemas, storage


def set_data_root(path) -> None:
    """Point clearance_core.storage (and so clearance_core.grid) at a data root."""
    storage.DATA = Path(path)


__all__ = ["grid", "schemas", "storage", "set_data_root"]
