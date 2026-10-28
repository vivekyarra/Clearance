"""Clearance shared core. Importing it loads <repo>/.env (if present) without overriding variables
already set in the environment, so NEBIUS_API_KEY / TAVILY_API_KEY can live in a local, gitignored file. CLEARANCE_SKIP_DOTENV=1 disables local settings (.env and config/*.local.yaml)."""
import os
from pathlib import Path

_ENV = Path(__file__).resolve().parent.parent / ".env"
if _ENV.is_file() and not os.environ.get("CLEARANCE_SKIP_DOTENV"):
    for _line in _ENV.read_text(encoding="utf-8-sig").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        _k, _v = _k.strip().removeprefix("export ").strip(), _v.strip().strip('"').strip("'")
        if _k and _k not in os.environ:
            os.environ[_k] = _v
