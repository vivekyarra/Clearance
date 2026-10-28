"""Tavily Search / Extract for the orchestrator. CLEARANCE_TAVILY_MODE=live|offline (default live when
TAVILY_API_KEY is set). Offline mode returns no web results (never invented URLs). Every call is logged to usage.
Endpoint/parameters follow the tavily-python SDK; the raw HTTP fallback is VERIFY against current Tavily docs."""
from __future__ import annotations

import datetime as dt
import os
import time

from agents.llm.usage import log_usage

API = "https://api.tavily.com"


def mode() -> str:
    m = os.environ.get("CLEARANCE_TAVILY_MODE") or ("live" if os.environ.get("TAVILY_API_KEY") else "offline")
    return m.lower()


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _post(path: str, body: dict) -> dict:
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        raise RuntimeError("TAVILY_API_KEY not set (or use CLEARANCE_TAVILY_MODE=offline)")
    try:
        from tavily import TavilyClient  # type: ignore
        c = TavilyClient(api_key=key)
        if path == "/search":
            return c.search(**body)
        return c.extract(**body)
    except ImportError:
        import httpx
        r = httpx.post(API + path, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=60)
        r.raise_for_status()
        return r.json()


def tavily_search(query: str, max_results: int = 5, *, meta: dict | None = None) -> dict:
    max_results = max(1, min(int(max_results), 10))
    if mode() != "live":
        return {"mode": "offline", "query": query, "results": [], "note": "Tavily offline mode: no web results"}
    t0 = time.perf_counter()
    data = _post("/search", {"query": query, "max_results": max_results})
    log_usage("tavily.search", "tavily", 0, 0, time.perf_counter() - t0, 0.0, {**(meta or {}), "query": query})
    res = [{"url": r.get("url"), "title": r.get("title"), "snippet": (r.get("content") or "")[:500],
            "retrieved_at": _now()} for r in data.get("results", [])]
    return {"mode": "live", "query": query, "results": res}


def tavily_extract(urls: list[str], *, meta: dict | None = None) -> dict:
    urls = [u for u in (urls or []) if isinstance(u, str) and u.startswith("http")][:5]
    if mode() != "live":
        return {"mode": "offline", "results": [], "note": "Tavily offline mode: no page content"}
    t0 = time.perf_counter()
    data = _post("/extract", {"urls": urls})
    log_usage("tavily.extract", "tavily", 0, 0, time.perf_counter() - t0, 0.0, {**(meta or {}), "n_urls": len(urls)})
    res = [{"url": r.get("url"), "text": (r.get("raw_content") or "")[:4000], "retrieved_at": _now()}
           for r in data.get("results", [])]
    return {"mode": "live", "results": res}
