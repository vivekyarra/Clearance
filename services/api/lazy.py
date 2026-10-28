"""Import other workstreams' modules on demand; a missing module becomes a clean HTTP 503."""
from __future__ import annotations

import importlib
from typing import Optional


class ComponentUnavailable(RuntimeError):
    def __init__(self, module: str, reason: str = ""):
        self.module = module
        self.reason = reason
        super().__init__(f"{module}: component unavailable in this deployment" + (f" ({reason})" if reason else ""))


def require(module: str, attr: Optional[str] = None):
    try:
        mod = importlib.import_module(module)
    except ImportError as e:
        raise ComponentUnavailable(module, str(e)) from e
    if attr is None:
        return mod
    obj = getattr(mod, attr, None)
    if obj is None:
        raise ComponentUnavailable(f"{module}.{attr}", "entry point missing")
    return obj
