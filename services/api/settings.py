"""Clearance API configuration from CLEARANCE_* environment variables."""
from __future__ import annotations

import logging
import os
import secrets
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from services.api.core import storage

log = logging.getLogger("clearance.api")

REPO = Path(storage.REPO)
DEFAULT_CORS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.environ.get("CLEARANCE_" + name)
    return default if v in (None, "") else v


def env_bool(name: str, default: bool) -> bool:
    v = env(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(storage.DATA))
    database_url: Optional[str] = None          # default sqlite:///<data_dir>/clearance.db
    sample_dir: Path = REPO / "sample_sites"
    catalog_dir: Path = REPO / "catalog" / "robots"
    web_dist: Path = REPO / "apps" / "web" / "dist"
    infra_dir: Path = REPO / "infra"
    secret: Optional[str] = None                 # HMAC key for upload tokens; persisted in data_dir if unset
    public_api_base: Optional[str] = None        # e.g. http://192.168.1.20:8000 so phones on the LAN can reach us
    public_web_base: Optional[str] = None
    cors_origins: list[str] = field(default_factory=lambda: list(DEFAULT_CORS))
    dispatcher: str = "local"                    # local | nebius
    job_workers: int = 4
    job_mode: str = "thread"                     # thread | process (local dispatcher)
    upload_ttl_s: int = 6 * 3600
    max_upload_bytes: int = 1 << 30              # per PUT
    max_extract_bytes: int = 3 << 30             # per capture (archive-size guard)
    live_hz: float = 20.0
    live_max_sessions: int = 16
    live_idle_ttl_s: float = 600.0
    live_unclaimed_ttl_s: float = 120.0          # sessions no client ever joined
    live_max_per_site: int = 4
    live_max_clients: int = 8                    # WebSocket clients per session
    live_create_per_min: int = 10                # POST /live per client IP (operator exempt)
    ws_msg_rate: float = 30.0                    # token bucket per WebSocket client (messages/s)
    ws_msg_burst: float = 60.0
    catalog_writeback: bool = True
    operator_token: Optional[str] = None         # CLEARANCE_OPERATOR_TOKEN; generated into data_dir if unset
    deployed: bool = False                       # CLEARANCE_DEPLOYED=1: hosted deployment, strict config
    allowed_hosts: list[str] = field(default_factory=list)   # TrustedHostMiddleware when set
    asset_hosts: list[str] = field(default_factory=list)     # https hosts twin assets may point at
    join_ttl_s: int = 4 * 3600
    capture_token_ttl_s: int = 48 * 3600
    live_token_ttl_s: int = 12 * 3600
    import_samples: bool = True
    s3_bucket: Optional[str] = None
    s3_endpoint: Optional[str] = None            # Nebius Object Storage endpoint URL (VERIFY in docs/VERIFIED.md)
    s3_region: Optional[str] = None
    s3_access_key: Optional[str] = None
    s3_secret_key: Optional[str] = None
    s3_prefix: str = ""

    def __post_init__(self):
        self.data_dir = Path(self.data_dir)
        if not self.database_url:
            self.database_url = f"sqlite:///{(self.data_dir / 'clearance.db').as_posix()}"

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(env("DATA") or storage.DATA)
        cors = env("CORS_ORIGINS")
        return cls(
            data_dir=data_dir,
            database_url=env("DATABASE_URL"),
            sample_dir=Path(env("SAMPLE_DIR") or REPO / "sample_sites"),
            catalog_dir=Path(env("CATALOG_DIR") or REPO / "catalog" / "robots"),
            web_dist=Path(env("WEB_DIST") or REPO / "apps" / "web" / "dist"),
            infra_dir=Path(env("INFRA_DIR") or REPO / "infra"),
            secret=env("API_SECRET"),
            public_api_base=env("PUBLIC_API_BASE"),
            public_web_base=env("PUBLIC_WEB_BASE"),
            cors_origins=DEFAULT_CORS + [c.strip() for c in cors.split(",") if c.strip()] if cors else list(DEFAULT_CORS),
            dispatcher=env("DISPATCHER", "local"),
            job_workers=int(env("JOB_WORKERS", str(min(4, os.cpu_count() or 2)))),
            job_mode=env("JOB_MODE", "thread"),
            upload_ttl_s=int(env("UPLOAD_TTL_S", str(6 * 3600))),
            max_upload_bytes=int(env("MAX_UPLOAD_BYTES", str(1 << 30))),
            max_extract_bytes=int(env("MAX_EXTRACT_BYTES", str(3 << 30))),
            live_hz=float(env("LIVE_HZ", "20")),
            catalog_writeback=env_bool("CATALOG_WRITEBACK", True),
            operator_token=env("OPERATOR_TOKEN"),
            deployed=env_bool("DEPLOYED", False),
            allowed_hosts=_csv(env("ALLOWED_HOSTS")),
            asset_hosts=_csv(env("ASSET_HOSTS")),
            join_ttl_s=int(env("JOIN_TTL_S", str(4 * 3600))),
            import_samples=env_bool("IMPORT_SAMPLES", True),
            s3_bucket=env("S3_BUCKET"),
            s3_endpoint=env("S3_ENDPOINT"),
            s3_region=env("S3_REGION"),
            s3_access_key=env("S3_ACCESS_KEY_ID") or os.environ.get("AWS_ACCESS_KEY_ID"),
            s3_secret_key=env("S3_SECRET_ACCESS_KEY") or os.environ.get("AWS_SECRET_ACCESS_KEY"),
            s3_prefix=env("S3_PREFIX", "") or "",
        )

    def check_deployment(self) -> None:
        """Hosted deployments must not fall back to generated secrets or the Host header."""
        if not self.deployed:
            return
        problems = []
        if not self.operator_token:
            problems.append("CLEARANCE_OPERATOR_TOKEN is required")
        if not self.public_api_base and not self.allowed_hosts:
            problems.append("set CLEARANCE_PUBLIC_API_BASE or CLEARANCE_ALLOWED_HOSTS (upload URLs must not "
                            "come from the request Host header)")
        if problems:
            raise RuntimeError("CLEARANCE_DEPLOYED=1: " + "; ".join(problems))

    def load_operator_token(self) -> str:
        """CLEARANCE_OPERATOR_TOKEN, else <data_dir>/.operator_token (created and printed once)."""
        if self.operator_token:
            return self.operator_token
        p = self.data_dir / ".operator_token"
        if p.is_file():
            tok = p.read_text(encoding="utf-8").strip()
            if len(tok) >= 16:
                log.info("operator token: read from %s", p)
                return tok
        p.parent.mkdir(parents=True, exist_ok=True)
        tok = secrets.token_urlsafe(32)
        p.write_text(tok, encoding="utf-8")
        try:
            os.chmod(p, 0o600)
        except OSError:  # pragma: no cover - Windows ACLs
            pass
        print(f"[clearance] operator token (shown once; also saved to {p}): {tok}", file=sys.stderr, flush=True)
        return tok

    def load_secret(self) -> bytes:
        if self.secret:
            return self.secret.encode()
        p = self.data_dir / ".api_secret"
        if p.exists():
            return p.read_text(encoding="utf-8").strip().encode()
        p.parent.mkdir(parents=True, exist_ok=True)
        s = secrets.token_hex(32)
        p.write_text(s, encoding="utf-8")
        return s.encode()


def _csv(v: Optional[str]) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]
