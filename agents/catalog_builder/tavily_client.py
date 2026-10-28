"""Tavily Search + Extract wrapper (REST API, https://docs.tavily.com).

Modes (env CLEARANCE_TAVILY_MODE):
  live     POST https://api.tavily.com/{search,extract} with `Authorization: Bearer $TAVILY_API_KEY`.
  offline  answer from recorded responses under recorded/tavily/{search,extract}/ (same JSON shape).
Default: live when TAVILY_API_KEY is set, otherwise offline.
With record=True (or CLEARANCE_TAVILY_RECORD=1) live responses are also written as recorded responses.
"""
from __future__ import annotations

import clearance_core  # noqa: F401  (loads <repo>/.env)

import hashlib
import json
import os
import re
import time
from pathlib import Path

API_BASE = "https://api.tavily.com"
RECORDED_DIR = Path(__file__).resolve().parent / "recorded" / "tavily"


class TavilyError(RuntimeError):
    pass


def tavily_mode() -> str:
    m = os.environ.get("CLEARANCE_TAVILY_MODE", "").strip().lower()
    if m in ("live", "offline"):
        return m
    return "live" if os.environ.get("TAVILY_API_KEY") else "offline"


def record_key(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower().split("://", 1)[-1]).strip("_")[:70]
    return f"{slug}_{hashlib.sha1(text.strip().encode('utf-8')).hexdigest()[:8]}"


class TavilyClient:
    def __init__(self, mode: str | None = None, api_key: str | None = None,
                 recorded_dir: str | Path | None = None, record: bool | None = None,
                 timeout: float = 60.0, max_retries: int = 2):
        self.mode = (mode or tavily_mode()).lower()
        if self.mode not in ("live", "offline"):
            raise ValueError(f"mode must be live|offline, got {self.mode!r}")
        self.api_key = api_key or os.environ.get("TAVILY_API_KEY")
        self.recorded_dir = Path(recorded_dir or os.environ.get("CLEARANCE_TAVILY_RECORDED", RECORDED_DIR))
        self.record = bool(int(os.environ.get("CLEARANCE_TAVILY_RECORD", "0"))) if record is None else record
        self.timeout = timeout
        self.max_retries = max_retries
        self.calls: list[dict] = []  # simple per-client log: {"kind","n","mode","credits"}

    # ---------- public API ----------
    def search(self, query: str, *, max_results: int = 5, search_depth: str = "basic",
               include_domains: list[str] | None = None, include_raw_content: bool = False) -> dict:
        if self.mode == "offline":
            p = self.recorded_dir / "search" / f"{record_key(query)}.json"
            resp = self._read(p) or {"query": query, "results": [], "answer": None, "images": [],
                                     "response_time": 0.0, "_note": "no recorded response for this query"}
            self._log("search", len(resp.get("results", [])), None)
            return resp
        body = {"query": query, "max_results": max_results, "search_depth": search_depth,
                "include_raw_content": include_raw_content, "include_usage": True}
        if include_domains:
            body["include_domains"] = include_domains
        resp = self._post("/search", body)
        self._log("search", len(resp.get("results", [])), (resp.get("usage") or {}).get("credits"))
        if self.record:
            self._write(self.recorded_dir / "search" / f"{record_key(query)}.json", resp)
        return resp

    def extract(self, urls: list[str] | str, *, extract_depth: str = "advanced",
                fmt: str = "markdown", query: str | None = None) -> dict:
        urls = [urls] if isinstance(urls, str) else list(urls)
        if not urls:
            return {"results": [], "failed_results": [], "response_time": 0.0}
        if self.mode == "offline":
            results, failed = [], []
            for u in urls:
                rec = self._read(self.recorded_dir / "extract" / f"{record_key(u)}.json")
                if rec and rec.get("raw_content"):
                    results.append(rec)
                else:
                    failed.append({"url": u, "error": "no recorded response for this url"})
            self._log("extract", len(results), None)
            return {"results": results, "failed_results": failed, "response_time": 0.0}
        out = {"results": [], "failed_results": [], "response_time": 0.0}
        for i in range(0, len(urls), 20):  # API accepts up to 20 URLs per call
            body = {"urls": urls[i:i + 20], "extract_depth": extract_depth, "format": fmt, "include_usage": True}
            if query:
                body["query"] = query
            resp = self._post("/extract", body)
            out["results"] += resp.get("results", [])
            out["failed_results"] += resp.get("failed_results", [])
            out["response_time"] += float(resp.get("response_time") or 0.0)
            self._log("extract", len(resp.get("results", [])), (resp.get("usage") or {}).get("credits"))
        if self.record:
            for r in out["results"]:
                self._write(self.recorded_dir / "extract" / f"{record_key(r['url'])}.json", r)
        return out

    # ---------- internals ----------
    def _post(self, path: str, body: dict) -> dict:
        import httpx

        if not self.api_key:
            raise TavilyError("TAVILY_API_KEY is not set (use CLEARANCE_TAVILY_MODE=offline for recorded responses)")
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        for attempt in range(self.max_retries + 1):
            r = httpx.post(API_BASE + path, json=body, headers=headers, timeout=self.timeout)
            if r.status_code == 429 and attempt < self.max_retries:
                time.sleep(min(30.0, float(r.headers.get("Retry-After", 2 ** attempt))))
                continue
            if r.status_code >= 400:
                raise TavilyError(f"Tavily {path} HTTP {r.status_code}: {r.text[:300]}")
            return r.json()
        raise TavilyError(f"Tavily {path}: rate limited")

    def _log(self, kind: str, n: int, credits):
        self.calls.append({"kind": kind, "n": n, "mode": self.mode, "credits": credits})

    @staticmethod
    def _read(p: Path):
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    @staticmethod
    def _write(p: Path, obj):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
