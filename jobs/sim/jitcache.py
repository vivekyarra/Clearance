"""Compiled-kernel cache for the simulator's numba functions.

numba checks a cached kernel against its own source file only: a kernel that calls a function from another module
(control.choose_speed -> peds.poly_sdist) keeps running the old callee after that module changes. The cache
therefore lives in a directory named after a hash of every kernel module (and the numba version), so any edit to
any of them starts a fresh cache instead of mixing stale machine code into a run."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import numba
from numba.core import config as numba_config

KERNEL_MODULES = ("planner.py", "peds.py", "control.py", "jitcache.py")


def _cache_dir() -> str:
    base = os.environ.get("NUMBA_CACHE_DIR") or str(Path.home() / ".cache" / "pg" / "numba")
    h = hashlib.sha1(numba.__version__.encode())
    here = Path(__file__).resolve().parent
    for name in KERNEL_MODULES:
        h.update(name.encode())
        h.update((here / name).read_bytes().replace(b"\r\n", b"\n"))
    return str(Path(base) / f"sim-{h.hexdigest()[:12]}")


CACHE_DIR = _cache_dir()


def njit_cached(fn):
    """numba.njit(cache=True) with the cache in CACHE_DIR. fastmath stays off: results must not depend on how the
    compiler reorders floating-point operations."""
    prev = numba_config.CACHE_DIR
    numba_config.CACHE_DIR = CACHE_DIR
    try:
        return numba.njit(cache=True, fastmath=False)(fn)
    finally:
        numba_config.CACHE_DIR = prev
