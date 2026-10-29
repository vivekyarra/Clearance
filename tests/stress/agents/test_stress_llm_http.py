"""HTTP-layer stress for agents/llm/client.py: the real HttpTransport (httpx) talks to a scripted transport that
plays a misbehaving Token Factory endpoint."""
import email.utils
import json
import time

import httpx
import pytest

from agents.llm import usage
from agents.llm.client import (LLM, HttpTransport, LLMError, TransportError, load_models_config, parse_retry_after,
                               resolve_role)

from agents_stress_helpers import ScriptedTransport, completion, ok, tool_call

MSG = [{"role": "user", "content": "hi"}]


def make(script, monkeypatch, default=None, clock=None, **cfg_over):
    monkeypatch.setenv("NEBIUS_API_KEY", "test-key")
    cfg = load_models_config()
    cfg.update(cfg_over)
    st = ScriptedTransport(script, default=default)
    kw = {"clock": clock} if clock else {}
    sleeps = []
    llm = LLM("live", config=cfg, transport=HttpTransport(st, **kw), sleep=sleeps.append)
    return llm, st, sleeps


def rows():
    con = usage._connect()
    try:
        return [json.loads(m) for (m,) in con.execute("SELECT meta FROM usage ORDER BY id")]
    finally:
        con.close()


# ---------------- 429 / Retry-After

def test_429_retry_after_seconds_is_honoured(monkeypatch):
    llm, st, sleeps = make([ok({"error": "slow down"}, 429, {"Retry-After": "7"}), ok(completion("hello"))],
                           monkeypatch)
    r = llm.chat("super", MSG)
    assert r.content == "hello" and len(st.requests) == 2
    assert len(sleeps) == 1 and 7.0 <= sleeps[0] <= 7.0 * 1.25
    (m,) = rows()
    assert m["ok"] is True and m["http_attempts"] == 2


def test_retry_after_http_date():
    now = time.time()
    d = email.utils.formatdate(now + 12, usegmt=True)
    assert 10.0 <= parse_retry_after(d, now) <= 13.0
    assert parse_retry_after("-5") == 0.0 and parse_retry_after("soon") is None
    assert parse_retry_after("nan") is None and parse_retry_after("") is None
    assert parse_retry_after(email.utils.formatdate(now - 100, usegmt=True), now) == 0.0


def test_retry_after_over_limit_fails_fast_and_logs(monkeypatch):
    llm, st, sleeps = make([ok({"error": "quota"}, 429, {"Retry-After": "3600"})], monkeypatch)
    with pytest.raises(TransportError, match="3600 s"):
        llm.chat("super", MSG)
    assert sleeps == [] and len(st.requests) == 1
    (m,) = rows()
    assert m["ok"] is False and m["status"] == 429 and m["http_attempts"] == 1


@pytest.mark.parametrize("value", ["inf", "-3", "garbage", "1e400"])
def test_retry_after_garbage_never_sleeps_negative_or_forever(monkeypatch, value):
    llm, st, sleeps = make([ok({}, 429, {"Retry-After": value}), ok(completion("x"))], monkeypatch)
    try:
        llm.chat("nano", MSG)
    except TransportError as e:  # inf / 1e400: refused cleanly
        assert "limit" in str(e)
        assert sleeps == []
        return
    assert all(0.0 <= s <= 30 * 1.25 for s in sleeps)


def test_rate_limit_exhaustion_is_a_clean_error_with_usage(monkeypatch):
    llm, st, sleeps = make([], monkeypatch, default=ok({"error": "rate"}, 429, {"Retry-After": "1"}), max_retries=4)
    with pytest.raises(LLMError) as ei:
        llm.chat("super", MSG, kind="investigate", meta={"analysis_id": "a_rl"})
    assert isinstance(ei.value, TransportError) and ei.value.status == 429
    assert len(st.requests) == 5 and len(sleeps) == 4
    (m,) = rows()
    assert m["ok"] is False and m["http_attempts"] == 5 and "429" in m["error"]
    s = usage.summary("a_rl")
    assert s["totals"]["calls"] == 1 and s["by_kind"][0]["kind"] == "investigate"


# ---------------- 5xx bursts and timeouts

def test_5xx_burst_then_success(monkeypatch):
    script = [ok("<html>bad gateway</html>", 502), ok("", 503), ok("oops", 500), ok(completion("fine", pt=3, ct=2))]
    llm, st, sleeps = make(script, monkeypatch)
    r = llm.chat("nano", MSG)
    assert r.content == "fine" and len(sleeps) == 3 and sleeps[2] > sleeps[0]
    assert usage.summary()["totals"]["tokens_in"] == 3


def test_5xx_exhaustion(monkeypatch):
    llm, st, sleeps = make([], monkeypatch, default=ok("down", 503), max_retries=2)
    with pytest.raises(TransportError, match="HTTP 503"):
        llm.chat("nano", MSG)
    assert len(st.requests) == 3 and rows()[0]["http_attempts"] == 3


def test_timeouts_retry_then_fail_cleanly(monkeypatch):
    def timeout(req):
        raise httpx.ReadTimeout("read timed out", request=req)
    llm, st, sleeps = make([timeout, ok(completion("late but ok"))], monkeypatch)
    assert llm.chat("nano", MSG).content == "late but ok"
    llm2, st2, _ = make([], monkeypatch, default=timeout, max_retries=1)
    with pytest.raises(TransportError, match="ReadTimeout"):
        llm2.chat("nano", MSG)
    assert len(st2.requests) == 2


def test_connect_error_is_retryable(monkeypatch):
    def refuse(req):
        raise httpx.ConnectError("refused", request=req)
    llm, st, _ = make([refuse, ok(completion("up"))], monkeypatch)
    assert llm.chat("nano", MSG).content == "up"


def test_trickling_body_hits_total_deadline(monkeypatch):
    t = {"now": 0.0}

    def clock():
        return t["now"]

    def chunks():
        for _ in range(100):
            t["now"] += 5.0  # each chunk arrives 5 s later: per-read timeouts never fire
            yield b" "
        yield json.dumps(completion("never")).encode()

    llm, st, sleeps = make([], monkeypatch, default=lambda req: httpx.Response(200, content=chunks()), clock=clock,
                           max_retries=0, timeout_s=60)
    with pytest.raises(TransportError, match="not complete within 60 s"):
        llm.chat("nano", MSG)


# ---------------- malformed bodies

def test_truncated_json_is_retried_then_clean_error(monkeypatch):
    full = json.dumps(completion("whole")).encode()
    llm, st, sleeps = make([ok(full[: len(full) // 2]), ok(full)], monkeypatch)
    assert llm.chat("nano", MSG).content == "whole" and len(sleeps) == 1
    llm2, _, _ = make([], monkeypatch, default=ok(full[:-3]), max_retries=1)
    with pytest.raises(TransportError, match="not valid JSON"):
        llm2.chat("nano", MSG)


def test_non_utf8_body_is_decoded_with_replacement(monkeypatch):
    body = json.dumps(completion("café"), ensure_ascii=False).encode("utf-8").replace("café".encode(), b"caf\xe9\xff")
    llm, _, _ = make([ok(body)], monkeypatch)
    r = llm.chat("nano", MSG)
    assert r.content.startswith("caf") and "�" in r.content


def test_deeply_nested_body_is_a_clean_error(monkeypatch):
    llm, _, _ = make([], monkeypatch, default=ok(b"[" * 200_000), max_retries=0)
    with pytest.raises(TransportError):
        llm.chat("nano", MSG)


def test_oversized_body_refused(monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "k")
    st = ScriptedTransport([], default=ok(b"x" * 5000))
    llm = LLM("live", config=load_models_config(), transport=HttpTransport(st, max_body_bytes=1000),
              sleep=lambda s: None)
    with pytest.raises(TransportError, match="over 1000 bytes"):
        llm.chat("nano", MSG)
    assert len(st.requests) == 1  # not retried


@pytest.mark.parametrize("raw", [
    [], "just text", {"choices": "nope"}, {"choices": ["str"]}, {"choices": [{"message": "str"}]},
    {"choices": [{"message": None}]}, 12, None])
def test_malformed_completions_raise_llm_error_and_log(monkeypatch, raw):
    llm, _, _ = make([ok(json.dumps(raw))], monkeypatch)
    try:
        r = llm.chat("nano", MSG)
    except LLMError:
        assert rows()[-1]["ok"] is False
        return
    assert r.content == "" and r.tool_calls == []  # {"message": None}: an empty reply is a valid answer


def test_junk_inside_a_valid_completion(monkeypatch):
    raw = completion(None)
    raw["choices"][0]["message"].update({"content": ["a", "list"], "tool_calls": ["x", 3, {"function": "f"},
                                                                                 {"function": {"name": 5}}]})
    raw["usage"] = {"prompt_tokens": "12", "completion_tokens": float("nan")}
    llm, _, _ = make([ok(json.dumps(raw))], monkeypatch)
    r = llm.chat("nano", MSG)
    assert r.tokens_in == 12 and r.tokens_out == 0
    assert [t.name for t in r.tool_calls] == ["", ""]


# ---------------- no streaming

def test_streaming_never_requested(monkeypatch):
    llm, st, _ = make([ok(completion("a")), ok(completion("b"))], monkeypatch)
    llm.chat("nano", MSG)
    llm.chat("nano", MSG, extra_body={"stream": True, "stream_options": {"include_usage": True}, "top_p": 0.9})
    for p in st.payloads():
        assert "stream" not in p and "stream_options" not in p
    assert st.payloads()[1]["top_p"] == 0.9


def test_event_stream_answer_is_a_clean_error(monkeypatch):
    sse = b'data: {"choices": [{"delta": {"content": "hi"}}]}\n\ndata: [DONE]\n\n'
    llm, st, sleeps = make([ok(sse, headers={"content-type": "text/event-stream"})], monkeypatch)
    with pytest.raises(TransportError, match="event stream"):
        llm.chat("nano", MSG)
    assert sleeps == []


# ---------------- request shape

def test_request_is_valid_json_with_auth(monkeypatch):
    llm, st, _ = make([ok(completion("", [tool_call("finish", {"reason": "r"})]))], monkeypatch)
    llm.chat("super", MSG, tools=[{"name": "finish", "parameters": {"type": "object", "properties": {}}}])
    req = st.requests[0]
    assert req.method == "POST" and str(req.url).endswith("/chat/completions")
    assert req.headers["authorization"] == "Bearer test-key"
    p = json.loads(req.content)
    assert p["model"] == resolve_role(load_models_config(), "super")["model_id"] and p["tools"]
