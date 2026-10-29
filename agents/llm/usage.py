"""Usage table: every model call and job is logged to SQLite at data/usage.db (or CLEARANCE_USAGE_DB)."""
from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
import time
from pathlib import Path

from clearance_core import storage

_LOCK = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    model_id TEXT NOT NULL,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    duration_s REAL NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    analysis_id TEXT,
    meta TEXT
);
CREATE INDEX IF NOT EXISTS usage_model ON usage(model_id);
CREATE INDEX IF NOT EXISTS usage_analysis ON usage(analysis_id);
"""


def db_path() -> Path:
    p = Path(os.environ.get("CLEARANCE_USAGE_DB") or (storage.DATA / "usage.db"))
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _connect(path: Path | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(str(path or db_path()), timeout=60)
    con.execute("PRAGMA busy_timeout = 60000")
    # DDL only when the table is missing: a plain read does not compete for the write lock under concurrency
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='usage'").fetchone() is None:
        for attempt in range(40):
            try:
                con.executescript(_SCHEMA)
                break
            except sqlite3.OperationalError as e:
                if "locked" not in str(e) or attempt == 39:
                    raise
                time.sleep(0.05 * (attempt + 1))
    return con


def _int(v) -> int:
    try:
        f = float(v or 0)
    except (TypeError, ValueError):
        return 0
    return int(f) if math.isfinite(f) and f > 0 else 0


def _float(v) -> float:
    try:
        f = float(v or 0)
    except (TypeError, ValueError):
        return 0.0
    return f if math.isfinite(f) and f > 0 else 0.0


def estimate_cost(model_id: str, tokens_in: int, tokens_out: int, config: dict | None = None) -> float:
    """Cost from the price table in config/models.yaml (prices are VERIFY until the verify script runs)."""
    if config is None:
        from agents.llm.client import load_models_config
        try:
            config = load_models_config()
        except Exception:
            return 0.0
    for role in (config.get("roles") or {}).values():
        for cand in role.get("candidates", []):
            if cand.get("model_id") == model_id:
                price = cand.get("price_per_1m_usd") or {}
                return (_int(tokens_in) * _float(price.get("input")) + _int(tokens_out) * _float(price.get("output"))) / 1e6
    return 0.0


def log_usage(kind: str, model_id: str, tokens_in: int = 0, tokens_out: int = 0, duration_s: float = 0.0,
              cost_usd: float = 0.0, meta: dict | None = None) -> int:
    meta = dict(meta or {})
    analysis_id = meta.get("analysis_id")
    analysis_id = None if analysis_id is None else str(analysis_id)
    c = cost_usd
    try:
        bad_cost = c is not None and not (math.isfinite(float(c)) and float(c) >= 0)
    except (TypeError, ValueError):
        bad_cost = True
    if bad_cost:  # never store NaN/inf/negative cost: the summary must stay valid JSON
        meta["cost_known"] = False
        meta["cost_rejected"] = str(c)[:40]
    with _LOCK:
        con = _connect()
        try:
            cur = con.execute(
                "INSERT INTO usage (ts, kind, model_id, tokens_in, tokens_out, duration_s, cost_usd, analysis_id, meta)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (time.time(), str(kind), str(model_id), _int(tokens_in), _int(tokens_out), _float(duration_s),
                 0.0 if bad_cost else _float(cost_usd), analysis_id, _meta_json(meta)))
            con.commit()
            return int(cur.lastrowid)
        finally:
            con.close()


def _meta_json(meta: dict) -> str:
    try:
        return json.dumps(meta, default=str)
    except (ValueError, TypeError, RecursionError):
        return json.dumps({k: str(v)[:200] for k, v in meta.items()})


def _group(con, col: str, where: str, args: tuple) -> list[dict]:
    rows = con.execute(
        f"SELECT {col}, COUNT(*), SUM(tokens_in), SUM(tokens_out), SUM(cost_usd), SUM(duration_s)"
        f" FROM usage {where} GROUP BY {col} ORDER BY {col}", args).fetchall()
    return [{col: r[0], "calls": r[1], "tokens_in": r[2] or 0, "tokens_out": r[3] or 0,
             "cost_usd": round(r[4] or 0.0, 6), "duration_s": round(r[5] or 0.0, 3)} for r in rows]


def summary(analysis_id: str | None = None) -> dict:
    """Aggregates for GET /usage/summary: by model, by kind, totals, and cost per analysis."""
    where, args = ("WHERE analysis_id = ?", (analysis_id,)) if analysis_id else ("", ())
    with _LOCK:
        con = _connect()
        try:
            by_model = _group(con, "model_id", where, args)
            by_kind = _group(con, "kind", where, args)
            t = con.execute(f"SELECT COUNT(*), SUM(tokens_in), SUM(tokens_out), SUM(cost_usd), SUM(duration_s)"
                            f" FROM usage {where}", args).fetchone()
            unpriced = con.execute(
                "SELECT COUNT(*) FROM usage " + (where + " AND " if where else "WHERE ")
                + "json_valid(meta) AND json_extract(meta, '$.cost_known') = 0", args).fetchone()[0]
            per_analysis = con.execute(
                "SELECT analysis_id, COUNT(*), SUM(cost_usd) FROM usage WHERE analysis_id IS NOT NULL"
                + (" AND analysis_id = ?" if analysis_id else "") + " GROUP BY analysis_id", args).fetchall()
        finally:
            con.close()
    return {
        "by_model": by_model,
        "by_kind": by_kind,
        "totals": {"calls": t[0] or 0, "tokens_in": t[1] or 0, "tokens_out": t[2] or 0,
                   "cost_usd": round(t[3] or 0.0, 6), "duration_s": round(t[4] or 0.0, 3)},
        "per_analysis": [{"analysis_id": r[0], "calls": r[1], "cost_usd": round(r[2] or 0.0, 6)} for r in per_analysis],
        # calls whose cost could not be computed (price null in config/models.yaml, or no usage block returned):
        # cost_usd above excludes them, so it is a lower bound whenever this is > 0
        "unpriced_calls": int(unpriced or 0),
        "cost_complete": not unpriced,
        "prices_verified": _prices_verified(),
    }


def _prices_verified() -> bool:
    try:
        from agents.llm.client import load_models_config
        cfg = load_models_config()
    except Exception:
        return False
    cands = [c for r in (cfg.get("roles") or {}).values() for c in r.get("candidates", [])]
    return bool(cands) and all((c.get("price_per_1m_usd") or {}).get("verified") for c in cands)
