import datetime as dt
import json

import pytest
from pydantic import BaseModel

from agents.llm import usage
from agents.llm.client import (LLM, LLMError, OfflineLLM, SchemaValidationError, TransportError, example_from_schema,
                               extract_json, load_models_config, resolve_role, retires_on)

SCHEMA = {"type": "object", "properties": {"speed": {"type": "number"}, "quote": {"type": "string"}},
          "required": ["speed", "quote"], "additionalProperties": False}


def ok_completion(content="ok", tool_calls=None, model="m", pt=10, ct=5):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"model": model, "choices": [{"message": msg, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": pt, "completion_tokens": ct}}


def _cands():
    return [c for r in load_models_config()["roles"].values() for c in r["candidates"]]


def test_config_has_all_roles_with_sources():
    cfg = load_models_config()
    assert set(cfg["roles"]) == {"nano", "super", "ultra", "vision"}
    for c in _cands():
        assert c["model_id"] and isinstance(c["verified"], bool)
        assert c["source"].startswith(("https://", "docs/"))  # URL or in-repo reference
        price = c["price_per_1m_usd"]
        assert price["verified"] is False and price.get("note")  # prices are never auto-verified
        if "retires" in c:
            assert retires_on(c) is not None
    providers = {c["provider"] for c in cfg["roles"]["vision"]["candidates"]}
    assert providers == {"token_factory", "self_hosted_vllm"}


def test_default_resolution_prefers_non_retiring_in_config_order(monkeypatch):
    cfg = load_models_config()
    monkeypatch.setenv("CLEARANCE_VISION_BASE_URL", "http://localhost:8001/v1")
    for role, rc in cfg["roles"].items():
        cands = rc["candidates"]
        start = rc.get("active", 0)
        order = cands[start:] + cands[:start]
        expected = next((c for c in order if retires_on(c) is None), order[0])
        assert resolve_role(cfg, role)["model_id"] == expected["model_id"], role


def test_default_mode_is_offline_without_key():
    llm = LLM()
    assert llm.mode == "offline"
    r = llm.chat("nano", [{"role": "user", "content": "hi"}])
    assert r.content.startswith("Offline response")
    s = usage.summary()
    assert s["totals"]["calls"] == 1
    assert s["by_model"][0]["model_id"].startswith("offline:")
    assert s["totals"]["cost_usd"] == 0


def test_invalid_mode_rejected():
    with pytest.raises(LLMError):
        LLM("sandbox")


def test_scripted_tool_calls_parsed():
    off = OfflineLLM({"super": [{"content": "thinking", "tool_calls": [
        {"name": "get_bottlenecks", "arguments": {"top_k": 2}}]}]})
    llm = LLM("offline", offline=off)
    r = llm.chat("super", [{"role": "user", "content": "go"}],
                 tools=[{"name": "get_bottlenecks", "parameters": {"type": "object", "properties": {}}}])
    assert r.tool_calls[0].name == "get_bottlenecks" and r.tool_calls[0].arguments == {"top_k": 2}
    msg = r.assistant_message()
    assert msg["tool_calls"][0]["function"]["name"] == "get_bottlenecks"
    assert off.calls[0]["tools"][0]["type"] == "function"


def test_json_mode_valid_first_try():
    llm = LLM("offline", offline=OfflineLLM({"nano": ['{"speed": 1.2, "quote": "Max speed 1.2 m/s"}']}))
    r = llm.chat("nano", [{"role": "user", "content": "extract"}], response_format=SCHEMA)
    assert r.parsed == {"speed": 1.2, "quote": "Max speed 1.2 m/s"} and r.attempts == 1


def test_json_repair_retry_succeeds_and_both_calls_logged():
    off = OfflineLLM({"nano": ['{"speed": "fast"}', '```json\n{"speed": 1.0, "quote": "1.0 m/s"}\n```']})
    llm = LLM("offline", offline=off)
    r = llm.chat("nano", [{"role": "system", "content": "be strict"}, {"role": "user", "content": "x"}],
                 response_format={"type": "json_schema", "json_schema": {"name": "spec", "schema": SCHEMA}})
    assert r.parsed["speed"] == 1.0 and r.attempts == 2
    second = off.calls[1]["messages"]
    assert second[0]["role"] == "system" and "JSON Schema" in second[0]["content"]
    assert sum(1 for m in second if m["role"] == "system") == 1
    assert "not valid JSON" in second[-1]["content"] and second[-2]["content"] == '{"speed": "fast"}'
    assert usage.summary()["totals"]["calls"] == 2
    assert r.tokens_in > 0 and r.tokens_out > 0


def test_json_repair_retry_exhausted():
    llm = LLM("offline", offline=OfflineLLM({"nano": ["not json at all", '{"speed": 2}']}))
    with pytest.raises(SchemaValidationError) as ei:
        llm.chat("nano", [{"role": "user", "content": "x"}], response_format=SCHEMA)
    assert any("quote" in e for e in ei.value.errors)


def test_json_object_mode_and_pydantic_model():
    class Spec(BaseModel):
        speed: float

    llm = LLM("offline", offline=OfflineLLM({"nano": ['<think>hmm {no}</think> {"speed": 3}', '[1, 2]']}))
    r = llm.chat("nano", [{"role": "user", "content": "x"}], response_format=Spec)
    assert isinstance(r.parsed, Spec) and r.parsed.speed == 3
    r2 = llm.chat("nano", [{"role": "user", "content": "x"}], response_format={"type": "json_object"})
    assert r2.parsed == [1, 2]


def test_offline_default_policy_produces_schema_instance():
    llm = LLM("offline")
    schema = {"type": "object", "properties": {"order": {"type": "array", "items": {"type": "string"}},
                                               "kind": {"enum": ["a", "b"]}, "n": {"type": "integer", "minimum": 2}},
              "required": ["order", "kind", "n"]}
    r = llm.chat("super", [{"role": "user", "content": "x"}], response_format=schema)
    assert r.parsed == {"order": [], "kind": "a", "n": 2}


def test_offline_default_policy_calls_finish_when_available():
    llm = LLM("offline")
    r = llm.chat("super", [{"role": "user", "content": "x"}],
                 tools=[{"name": "finish", "parameters": {"type": "object", "properties": {}}}])
    assert r.tool_calls[0].name == "finish"


def test_extract_json_variants():
    assert extract_json('Sure! {"a": 1} trailing') == {"a": 1}
    assert extract_json("reasoning...</think>\n{\"a\": 2}") == {"a": 2}
    assert extract_json("```\n[1]\n```") == [1]
    with pytest.raises(ValueError):
        extract_json("no braces here")


def test_example_from_schema_refs():
    schema = {"type": "object", "properties": {"x": {"$ref": "#/$defs/X"}}, "$defs": {"X": {"type": "boolean"}}}
    assert example_from_schema(schema) == {"x": False}


# ---------------- live mode with a transport test double

class Transport:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def __call__(self, url, headers, payload, timeout):
        self.requests.append({"url": url, "headers": headers, "payload": payload, "timeout": timeout})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def live(transport, monkeypatch, **cfg_over):
    monkeypatch.setenv("NEBIUS_API_KEY", "test-key")
    cfg = load_models_config()
    cfg.update(cfg_over)
    sleeps = []
    return LLM("live", config=cfg, transport=transport, sleep=sleeps.append), sleeps


def test_live_retries_then_succeeds(monkeypatch):
    cfg = load_models_config()
    target = resolve_role(cfg, "super")
    t = Transport([TransportError("429", status=429, retry_after=2.0), TransportError("boom", status=503),
                   ok_completion("hello", model=target["model_id"], pt=11, ct=3)])
    llm, sleeps = live(t, monkeypatch)
    r = llm.chat("super", [{"role": "user", "content": "hi"}], max_tokens=8)
    assert r.content == "hello" and len(t.requests) == 3
    assert sleeps[0] >= 2.0 and len(sleeps) == 2
    req = t.requests[0]
    assert req["url"] == cfg["base_url"].rstrip("/") + "/chat/completions"
    assert req["headers"]["Authorization"] == "Bearer test-key"
    assert req["payload"]["model"] == target["model_id"] and req["payload"]["max_tokens"] == 8
    s = usage.summary()
    assert s["by_model"][0]["model_id"] == target["model_id"]
    p = target["price"]
    expected = (11 * float(p.get("input") or 0) + 3 * float(p.get("output") or 0)) / 1e6
    assert s["totals"]["tokens_in"] == 11 and abs(s["totals"]["cost_usd"] - round(expected, 6)) < 1e-9


def test_live_non_retryable_error_raises_immediately(monkeypatch):
    t = Transport([TransportError("bad request", status=400, retryable=False)])
    llm, sleeps = live(t, monkeypatch)
    with pytest.raises(TransportError):
        llm.chat("nano", [{"role": "user", "content": "x"}])
    assert len(t.requests) == 1 and sleeps == []
    assert usage.summary()["totals"]["calls"] == 1  # failures are logged too


def test_live_retry_budget_exhausted(monkeypatch):
    t = Transport([TransportError("timeout")] * 3)
    llm, sleeps = live(t, monkeypatch, max_retries=2)
    with pytest.raises(TransportError):
        llm.chat("nano", [{"role": "user", "content": "x"}])
    assert len(t.requests) == 3 and len(sleeps) == 2 and sleeps[1] > sleeps[0] * 1.2


def test_live_json_schema_payload_and_tools(monkeypatch):
    t = Transport([ok_completion('{"speed": 1, "quote": "q"}'),
                   ok_completion("", tool_calls=[{"id": "c1", "type": "function", "function": {
                       "name": "finish", "arguments": "{\"reason\": \"done\"}"}}])])
    llm, _ = live(t, monkeypatch)
    llm.chat("nano", [{"role": "user", "content": "x"}], response_format=SCHEMA)
    assert t.requests[0]["payload"]["response_format"]["type"] == "json_schema"
    r = llm.chat("super", [{"role": "user", "content": "x"}],
                 tools=[{"name": "finish", "parameters": {"type": "object", "properties": {}}}], tool_choice="auto")
    assert "response_format" not in t.requests[1]["payload"] and t.requests[1]["payload"]["tool_choice"] == "auto"
    assert r.tool_calls[0].arguments == {"reason": "done"}


def test_live_bad_tool_arguments_kept_raw(monkeypatch):
    t = Transport([ok_completion("", tool_calls=[{"id": "c1", "function": {"name": "x", "arguments": "{oops"}}])])
    llm, _ = live(t, monkeypatch)
    r = llm.chat("super", [{"role": "user", "content": "x"}], tools=[{"name": "x"}])
    assert r.tool_calls[0].arguments == {"_unparseable_arguments": "{oops"}


def test_live_requires_key(monkeypatch):
    llm = LLM("live", transport=Transport([]))
    with pytest.raises(LLMError, match="NEBIUS_API_KEY"):
        llm.chat("nano", [{"role": "user", "content": "x"}])


def test_malformed_completion(monkeypatch):
    llm, _ = live(Transport([{"choices": []}]), monkeypatch)
    with pytest.raises(LLMError, match="malformed"):
        llm.chat("nano", [{"role": "user", "content": "x"}])


def test_vision_role_resolution(monkeypatch):
    cfg = load_models_config()
    vis = cfg["roles"]["vision"]["candidates"]
    hosted = next(c for c in vis if c["provider"] == "self_hosted_vllm")
    tf = next(c for c in vis if c["provider"] == "token_factory")
    # self-hosted endpoint env unset: live mode falls through to the Token Factory candidate
    assert resolve_role(cfg, "vision")["model_id"] == tf["model_id"]
    tf["available"] = False
    with pytest.raises(LLMError):
        resolve_role(cfg, "vision")
    monkeypatch.setenv(hosted["base_url_env"], "http://localhost:8001/v1")
    t = resolve_role(cfg, "vision")
    assert t["provider"] == "self_hosted_vllm" and t["base_url"] == "http://localhost:8001/v1"
    assert t["model_id"] == hosted["model_id"]
    tr = Transport([ok_completion("hazards: none")])
    llm = LLM("live", config=cfg, transport=tr)
    llm.chat("vision", [{"role": "user", "content": "x"}])  # no NEBIUS key needed for self-hosted
    assert tr.requests[0]["url"] == "http://localhost:8001/v1/chat/completions"
    assert "Authorization" not in tr.requests[0]["headers"]


def test_unknown_role():
    with pytest.raises(LLMError):
        LLM("offline").chat("mega", [{"role": "user", "content": "x"}])


def test_usage_logging_never_breaks_calls(monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(usage, "log_usage", boom)
    assert LLM("offline").chat("nano", [{"role": "user", "content": "x"}]).content


def test_many_offline_calls_are_deterministic():
    a = [LLM("offline").chat("nano", [{"role": "user", "content": str(i)}]).content for i in range(50)]
    assert len(set(a)) == 1
    assert usage.summary()["totals"]["calls"] == 50
    json.dumps(usage.summary())


# ---------------- retirement dates

def _role_cfg(cands, active=0):
    cfg = load_models_config()
    cfg["roles"] = {"nano": {"active": active, "candidates": cands}}
    return cfg


def _c(mid, retires=None):
    c = {"provider": "token_factory", "model_id": mid, "verified": False, "source": "docs/VERIFIED.md",
         "price_per_1m_usd": {"input": None, "output": None, "verified": False, "note": "confirm with account"}}
    if retires is not None:
        c["retires"] = retires
    return c


def test_retiring_active_candidate_yields_to_steady_one():
    cfg = _role_cfg([_c("old", "2099-01-01"), _c("new")], active=0)
    assert resolve_role(cfg, "nano")["model_id"] == "new"


def test_only_retiring_candidate_is_used_with_warning(caplog):
    cfg = _role_cfg([_c("old", dt.date(2099, 1, 1))])
    with caplog.at_level("WARNING", logger="clearance.llm"):
        t = resolve_role(cfg, "nano")
    assert t["model_id"] == "old" and t["retires"] == "2099-01-01"
    assert "retires on 2099-01-01" in caplog.text


def test_retired_candidate_is_never_used():
    cfg = _role_cfg([_c("gone", "2026-01-01"), _c("next", "2099-01-01")])
    assert resolve_role(cfg, "nano", today=dt.date(2026, 10, 27))["model_id"] == "next"
    with pytest.raises(LLMError, match="retired"):
        resolve_role(_role_cfg([_c("gone", "2026-01-01")]), "nano", today=dt.date(2026, 10, 27))


def test_retires_on_parsing():
    assert retires_on({"retires": "2026-11-09"}) == dt.date(2026, 11, 9)
    assert retires_on({"retires": dt.date(2026, 11, 9)}) == dt.date(2026, 11, 9)
    assert retires_on({}) is None and retires_on({"retires": "soon"}) is None


def test_null_prices_cost_nothing(monkeypatch):
    cfg = _role_cfg([_c("free")])
    monkeypatch.setenv("NEBIUS_API_KEY", "k")
    LLM("live", config=cfg, transport=Transport([ok_completion("x", model="free")])).chat(
        "nano", [{"role": "user", "content": "x"}])
    assert usage.summary()["totals"]["cost_usd"] == 0
