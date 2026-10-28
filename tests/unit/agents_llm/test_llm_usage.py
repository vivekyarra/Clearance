import threading

from agents.llm import usage
from agents.llm.client import load_models_config


def test_log_and_summary_groups():
    usage.log_usage("llm.nano", "m1", 100, 20, 0.5, 0.001, {"analysis_id": "a1"})
    usage.log_usage("llm.nano", "m1", 50, 10, 0.25, 0.0005, {"analysis_id": "a1"})
    usage.log_usage("llm.super", "m2", 10, 1, 1.0, 0.01, {"analysis_id": "a2"})
    usage.log_usage("sim.batch", "sim", 0, 0, 3.0, 0.0)
    s = usage.summary()
    m1 = next(r for r in s["by_model"] if r["model_id"] == "m1")
    assert m1["calls"] == 2 and m1["tokens_in"] == 150 and m1["tokens_out"] == 30
    assert abs(m1["cost_usd"] - 0.0015) < 1e-9
    assert {r["kind"] for r in s["by_kind"]} == {"llm.nano", "llm.super", "sim.batch"}
    assert s["totals"]["calls"] == 4 and abs(s["totals"]["duration_s"] - 4.75) < 1e-9
    per = {r["analysis_id"]: r for r in s["per_analysis"]}
    assert per["a1"]["calls"] == 2 and "a2" in per
    s1 = usage.summary("a1")
    assert s1["totals"]["calls"] == 2 and [r["analysis_id"] for r in s1["per_analysis"]] == ["a1"]
    assert s["prices_verified"] is False


def test_empty_summary():
    s = usage.summary()
    assert s["totals"] == {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "duration_s": 0.0}
    assert s["by_model"] == [] and s["per_analysis"] == []


def test_estimate_cost_from_price_table():
    cfg = load_models_config()
    c = usage.estimate_cost("nvidia/nemotron-3-super-120b-a12b", 1_000_000, 1_000_000, cfg)
    assert abs(c - 1.20) < 1e-9
    assert usage.estimate_cost("unknown/model", 10, 10, cfg) == 0.0


def test_concurrent_logging_from_threads():
    def work(k):
        for i in range(25):
            usage.log_usage("llm.nano", f"m{k % 3}", 1, 1, 0.01, 0.0, {"i": i})
    ts = [threading.Thread(target=work, args=(k,)) for k in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert usage.summary()["totals"]["calls"] == 200
