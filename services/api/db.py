"""Persistence: SQLAlchemy 2.x, SQLite by default (data/clearance.db), Postgres via CLEARANCE_DATABASE_URL.

Large artifacts live in storage; rows hold ids, status and storage keys only.
"""
from __future__ import annotations

import contextlib
import threading
import uuid
from datetime import datetime, timezone
from typing import Iterator, Optional

from sqlalchemy import JSON, Boolean, Float, Integer, String, Text, UniqueConstraint, create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class Base(DeclarativeBase):
    pass


class SiteRow(Base):
    __tablename__ = "sites"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(32), default="other")
    area_m2: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    marker: Mapped[dict] = mapped_column(JSON, default=dict)
    geometry_source: Mapped[str] = mapped_column(String(16), default="scan")  # scan | reference
    sample: Mapped[bool] = mapped_column(Boolean, default=False)
    readonly: Mapped[bool] = mapped_column(Boolean, default=False)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)


class CaptureRow(Base):
    __tablename__ = "captures"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    site_id: Mapped[str] = mapped_column(String(128), index=True)
    role: Mapped[str] = mapped_column(String(16))
    tier: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="registered")
    # registered -> processing -> done | failed ; observer captures end in "stored"
    meta: Mapped[dict] = mapped_column(JSON, default=dict)      # registration body (device, marker, ...)
    n_chunks: Mapped[int] = mapped_column(Integer, default=0)
    chunking: Mapped[str] = mapped_column(String(16), default="independent")
    files: Mapped[list] = mapped_column(JSON, default=list)     # individually uploaded file names
    input_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    job_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    twin_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)
    updated_at: Mapped[str] = mapped_column(String(40), default=now_iso, onupdate=now_iso)


class TwinRow(Base):
    __tablename__ = "twins"
    __table_args__ = (UniqueConstraint("site_id", "version", name="uq_twin_site_version"),)
    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    twin_id: Mapped[str] = mapped_column(String(128), index=True)
    site_id: Mapped[str] = mapped_column(String(128), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | ready
    key: Mapped[str] = mapped_column(String(512), default="")            # storage key of twin.json
    source: Mapped[str] = mapped_column(String(16), default="edit")      # capture | edit | sample
    parent_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    capture_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    job_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    readonly: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)


class AnalysisRow(Base):
    __tablename__ = "analyses"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    site_id: Mapped[str] = mapped_column(String(128), index=True)
    twin_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued | running | done | failed
    input_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    job_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    robot_ids: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    readonly: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)
    updated_at: Mapped[str] = mapped_column(String(40), default=now_iso, onupdate=now_iso)


class JobRow(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("kind", "input_hash", name="uq_job_kind_hash"),)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    input_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued | running | succeeded | failed
    backend: Mapped[str] = mapped_column(String(16), default="local")
    result: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)
    started_at: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    finished_at: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    duration_s: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class LiveSessionRow(Base):
    __tablename__ = "live_sessions"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    site_id: Mapped[str] = mapped_column(String(128), index=True)
    mode: Mapped[str] = mapped_column(String(16))          # chaos | coach
    robot_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    twin_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | closed | error
    n_events: Mapped[int] = mapped_column(Integer, default=0)
    n_frames: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)
    closed_at: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)


class RobotReviewRow(Base):
    __tablename__ = "robot_reviews"
    robot_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=True)
    verified_by: Mapped[str] = mapped_column(String(100))
    verified_at: Mapped[str] = mapped_column(String(40), default=now_iso)
    edits: Mapped[dict] = mapped_column(JSON, default=dict)
    notes: Mapped[str] = mapped_column(Text, default="")
    history: Mapped[list] = mapped_column(JSON, default=list)


class Database:
    def __init__(self, url: str):
        self.url = url
        kw: dict = {"future": True}
        if url.startswith("sqlite"):
            kw["connect_args"] = {"check_same_thread": False, "timeout": 30}
        self.engine = create_engine(url, **kw)
        if url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def _pragmas(dbapi_conn, _rec):  # noqa: ANN001
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA busy_timeout=30000")
                cur.close()
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.version_lock = threading.Lock()  # in-process guard; the unique constraint guards across processes

    def create_all(self) -> None:
        if self.url.startswith("sqlite:///"):
            from pathlib import Path

            Path(self.url[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)
        Base.metadata.create_all(self.engine)

    @contextlib.contextmanager
    def session(self) -> Iterator[Session]:
        s = self._sessions()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def dispose(self) -> None:
        self.engine.dispose()

    # ---- twin versions ----
    def reserve_twin_version(self, site_id: str, *, twin_id: str, source: str, parent_version: Optional[int] = None,
                             capture_id: Optional[str] = None, job_id: Optional[str] = None,
                             version: Optional[int] = None, readonly: bool = False, attempts: int = 20) -> int:
        """Insert a 'pending' row for the next free version (or a given one) and return the version number."""
        for _ in range(attempts):
            with self.version_lock:
                try:
                    with self.session() as s:
                        n = version
                        if n is None:
                            cur = s.scalar(select(func.max(TwinRow.version)).where(TwinRow.site_id == site_id))
                            n = (cur or 0) + 1
                        s.add(TwinRow(twin_id=twin_id, site_id=site_id, version=n, status="pending", source=source,
                                      parent_version=parent_version, capture_id=capture_id, job_id=job_id,
                                      readonly=readonly))
                    return n
                except IntegrityError:
                    if version is not None:
                        raise
                    continue
        raise RuntimeError(f"could not allocate a twin version for {site_id}")
