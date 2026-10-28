"""Usage DB under a process pool, summary correctness, and token/cost accounting with null or broken prices."""
import json
import math
import sqlite3
from concurrent.futures import ProcessPoolExecutor

import pytest

from agents.llm import usage
from agents.llm.client import LLM, HttpTransport, price_value

from agents_stress_helpers import ScriptedTransport, completion, ok, pool_log_rows

MSG = [{"role": "user", "content": "x"}]


def test_process_pool_concurrent_writes_lose_nothing():
    workers, n = 6, 120
    with ProcessPoolExecutor(max_workers=workers) as ex:
        pids = list(ex.map(pool_log_rows, [(w, n) for w in range(workers)]))
    assert len(pids) == workers
    s = usage.summary()
    total = workers * n
    assert s["totals"]["calls"] == total
    assert s["totals"]["tokens_in"] == 10 * total and s["totals"]["tokens_out"] == 3 * total
    assert abs(s["totals"]["cost_usd"] - round(0.001 * total, 6)) < 1e-9
    assert sum(r["calls"] for r in s["by_model"]) == total
    per = {r["analysis_id"]: r["calls"] for r in s["per_analysis"]}
    assert per == {"a0": n * 3, "a1": n * 3}
    con = sqlite3.connect(str(usage.db_path()))
    seen = {(json.loads(m)["worker"], json.loads(m)["i"]) for (m,) in con.execute("SELECT meta FROM usage")}
    con.close()
    assert len(seen) == total


def test_summary_groups_and_filters_exactly():
    usage.log_usage("a", "m1", 100, 10, 1.0, 0.5, {"analysis_id": "x"})
    usage.log_usage("a", "m2", 1, 2, 2.0, 0.25, {"analysis_id": "y"})
    usage.log_usage("b", "m1", 5, 5, 0.5, 0.125, {})
    s = usage.summary()
    assert s["by_model"] == [
        {"model_id": "m1", "calls": 2, "tokens_in": 105, "tokens_out": 15, "cost_usd": 0.625, "duration_s": 1.5},
        {"model_id": "m2", "calls": 1, "tokens_in": 1, "tokens_out": 2, "cost_usd": 0.25, "duration_s": 2.0}]
    assert [k["kind"] for k in s["by_kind"]] == ["a", "b"]
    sx = usage.summary("x")
    assert sx["totals"]["calls"] == 1 and sx["per_analysis"] == [{"analysis_id": "x", "calls": 1, "cost_usd": 0.5}]
    assert usage.summary("nope")["totals"]["calls"] == 0


@pytest.mark.parametrize("tin,tout,cost", [(float("nan"), 3, float("nan")), (-5, -1, -0.2),
                                           (10, 2, float("inf")), ("12", None, "0.5"), (None, None, None)])
def test_hostile_numbers_never_break_the_summary(tin, tout, cost):
    usage.log_usage("k", "m", tin, tout, float("nan"), cost, {"analysis_id": 7, "obj": object()})
    s = usage.summary()
    json.dumps(s, allow_nan=False)  # GET /usage/summary must stay valid JSON
    t = s["totals"]
    assert t["calls"] == 1 and t["tokens_in"] >= 0 and t["tokens_out"] >= 0 and t["cost_usd"] >= 0
    assert math.isfinite(t["duration_s"])


def test_db_file_removed_between_calls_is_recreated(tmp_path, monkeypatch):
    p = tmp_path / "again.db"
    monkeypatch.setenv("CLEARANCE_USAGE_DB", str(p))
    usage.log_usage("k", "m")
    p.unlink()
    usage.log_usage("k", "m")
    assert usage.summary()["totals"]["calls"] == 1


# ---------------- cost accounting in the client

def cfg_with(price, model="pm"):
    return {"base_url": "https://example.invalid/v1", "api_key_env": "NEBIUS_API_KEY",
            "max_retries": 0, "roles": {"nano": {"active": 0, "candidates": [
                {"provider": "token_factory", "model_id": model, "price_per_1m_usd": price}]}}}


def call(monkeypatch, price, body):
    monkeypatch.setenv("NEBIUS_API_KEY", "k")
    llm = LLM("live", config=cfg_with(price), transport=HttpTransport(ScriptedTransport([ok(body)])),
              sleep=lambda s: None)
    return llm.chat("nano", MSG)


def test_known_prices_are_charged(monkeypatch):
    call(monkeypatch, {"input": 1.0, "output": 2.0, "verified": True}, completion("a", pt=1_000_000, ct=500_000))
    s = usage.summary()
    assert s["totals"]["cost_usd"] == 2.0 and s["unpriced_calls"] == 0 and s["cost_complete"] is True


@pytest.mark.parametrize("price", [{"input": None, "output": None}, {"input": 0.3, "output": None}, None,
                                   {"input": "n/a", "output": "0.9"}, {"input": float("nan"), "output": 1},
                                   {"input": -1, "output": 1}, {"input": True, "output": 1}])
def test_null_or_broken_prices_are_reported_as_unpriced(monkeypatch, price):
    r = call(monkeypatch, price, completion("a", pt=1000, ct=1000))
    assert r.content == "a"  # a bad price never breaks the call
    s = usage.summary()
    assert s["totals"]["tokens_in"] == 1000 and s["totals"]["cost_usd"] == 0
    assert s["unpriced_calls"] == 1 and s["cost_complete"] is False


def test_missing_usage_block_is_unpriced_not_free(monkeypatch):
    call(monkeypatch, {"input": 1.0, "output": 1.0}, completion("a", usage=False))
    s = usage.summary()
    assert s["unpriced_calls"] == 1 and s["totals"]["tokens_in"] == 0


def test_zero_output_tokens_with_null_output_price_is_still_priced(monkeypatch):
    call(monkeypatch, {"input": 1.0, "output": None}, completion("", pt=1_000_000, ct=0))
    s = usage.summary()
    assert s["totals"]["cost_usd"] == 1.0 and s["unpriced_calls"] == 0


def test_offline_calls_are_free_and_priced():
    LLM("offline").chat("nano", MSG)
    s = usage.summary()
    assert s["totals"]["calls"] == 1 and s["totals"]["cost_usd"] == 0 and s["unpriced_calls"] == 0
    assert s["by_model"][0]["model_id"].startswith("offline:")


def test_price_value():
    assert price_value(None) is None and price_value("x") is None and price_value(float("inf")) is None
    assert price_value(-0.1) is None and price_value(True) is None
    assert price_value("0.30") == 0.3 and price_value(0) == 0.0


def test_estimate_cost_tolerates_broken_prices():
    cfg = cfg_with({"input": "n/a", "output": None})
    assert usage.estimate_cost("pm", 10, 10, cfg) == 0.0
    assert usage.estimate_cost("pm", float("nan"), None, cfg_with({"input": 1, "output": 1})) == 0.0
