"""OpenAI-compatible chat client for Nebius Token Factory (Nemotron Nano/Super/Ultra + vision).

CLEARANCE_LLM_MODE=live (default when NEBIUS_API_KEY is set) | offline. Model IDs and base URL come from config/models.yaml.
Features: retry with exponential backoff (429/5xx/timeouts, honours Retry-After), per-call timeout, tool calling,
JSON mode with local JSON-Schema validation and one repair retry, and a usage-table row for every call.
"""
from __future__ import annotations

import clearance_core  # noqa: F401  (loads <repo>/.env)

import datetime as dt
import json
import logging
import math
import os
import random
import re
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

import yaml

from agents.llm import usage

Role = Literal["nano", "super", "ultra", "vision"]
ROLES = ("nano", "super", "ultra", "vision")
log = logging.getLogger("clearance.llm")
REPO = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO / "config" / "models.yaml"


class LLMError(RuntimeError):
    pass


class SchemaValidationError(LLMError):
    def __init__(self, msg: str, errors: list[str], content: str):
        super().__init__(msg)
        self.errors = errors
        self.content = content


class TransportError(LLMError):
    def __init__(self, msg: str, status: int | None = None, retry_after: float | None = None, retryable: bool = True):
        super().__init__(msg)
        self.status = status
        self.retry_after = retry_after
        self.retryable = retryable


# ---------------------------------------------------------------- config

def load_models_config(path: str | Path | None = None) -> dict:
    p = Path(path or os.environ.get("CLEARANCE_MODELS_CONFIG") or DEFAULT_CONFIG)
    with open(p, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    if "roles" not in cfg:
        raise LLMError(f"{p}: missing 'roles'")
    # Optional machine-local additions next to the config (models.local.yaml, gitignored): extra candidates are
    # appended to each role and marked local, so the verify script and Token Factory selection leave them alone.
    local = p.with_name(p.stem + ".local.yaml")
    if local.is_file() and not os.environ.get("CLEARANCE_SKIP_DOTENV"):
        with open(local, encoding="utf-8") as f:
            extra = yaml.safe_load(f) or {}
        for role, rc in (extra.get("roles") or {}).items():
            if role in cfg["roles"]:
                cands = cfg["roles"][role].setdefault("candidates", [])
                cands.extend({**c, "local": True} for c in (rc.get("candidates") or []))
    return cfg


def retires_on(c: dict) -> dt.date | None:
    """Optional `retires: YYYY-MM-DD` on a candidate (YAML may already parse it as a date)."""
    r = c.get("retires")
    if r is None or r == "":
        return None
    if isinstance(r, dt.datetime):
        return r.date()
    if isinstance(r, dt.date):
        return r
    try:
        return dt.date.fromisoformat(str(r).strip())
    except ValueError:
        return None


def retires_invalid(c: dict) -> bool:
    """A `retires` value is present but unreadable: treat the candidate as retired (never pick it silently)."""
    r = c.get("retires")
    return r is not None and r != "" and retires_on(c) is None


def today_for_retirement() -> dt.date:
    """The later of the local and the UTC date, so a model is never used on its retirement day in any zone."""
    return max(dt.date.today(), dt.datetime.now(dt.timezone.utc).date())


def resolve_role(config: dict, role: str, *, live: bool = True, today: dt.date | None = None) -> dict:
    """Pick the candidate for a role. Walk candidates from `active` in config order, skipping ones the verify script
    marked unavailable, ones already past their `retires` date, and self-hosted ones whose base URL env var is unset
    (live mode only). Among usable candidates a non-retiring one is preferred; a retiring pick logs a warning."""
    if role not in (config.get("roles") or {}):
        raise LLMError(f"unknown role {role!r}; known: {sorted(config.get('roles', {}))}")
    rc = config["roles"][role]
    cands = rc.get("candidates") or []
    if not cands:
        raise LLMError(f"role {role!r} has no candidates")
    today = today or today_for_retirement()
    try:
        start = int(rc.get("active", 0) or 0) % len(cands)
    except (TypeError, ValueError):
        start = 0
    usable = []
    for c in cands[start:] + cands[:start]:
        if c.get("available") is False or not c.get("model_id"):
            continue
        if retires_invalid(c):
            log.warning("role %s: skipping %s, unreadable retires value %r", role, c.get("model_id"), c.get("retires"))
            continue
        ret = retires_on(c)
        if ret is not None and ret <= today:
            continue
        base = config.get("base_url")
        key_env = config.get("api_key_env", "NEBIUS_API_KEY")
        if c.get("provider") == "self_hosted_vllm":
            base = os.environ.get(c.get("base_url_env", "CLEARANCE_VISION_BASE_URL"))
            key_env = c.get("api_key_env", "CLEARANCE_VISION_API_KEY")
            if live and not base:
                continue
        elif c.get("base_url"):
            # Candidates with their own endpoint are used only when their key is set.
            base = c["base_url"]
            key_env = c.get("api_key_env", key_env)
            if live and not os.environ.get(key_env or ""):
                continue
        usable.append({"role": role, "model_id": c["model_id"], "provider": c.get("provider", "token_factory"),
                       "base_url": base, "api_key_env": key_env, "price": c.get("price_per_1m_usd") or {},
                       "verified": bool(c.get("verified")), "retires": ret.isoformat() if ret else None,
                       "request_extra": c.get("request_extra") or {}})
    if not usable:
        raise LLMError(f"no usable candidate for role {role!r} (unavailable, retired, or self-hosted endpoint env "
                       "unset)")
    # Prefer candidates whose API key is present (live), then non-retiring ones, then config order.
    def has_key(u):
        return (not live) or u["provider"] == "self_hosted_vllm" or bool(os.environ.get(u["api_key_env"] or ""))

    pick = (next((u for u in usable if u["retires"] is None and has_key(u)), None)
            or next((u for u in usable if has_key(u)), None)
            or next((u for u in usable if u["retires"] is None), usable[0]))
    if pick["retires"]:
        log.warning("role %s uses %s, which Token Factory retires on %s", role, pick["model_id"], pick["retires"])
    return pick


# ---------------------------------------------------------------- responses

@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict
    raw_arguments: str = ""


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCall]
    model_id: str
    role: str
    tokens_in: int = 0
    tokens_out: int = 0
    finish_reason: str | None = None
    parsed: Any = None
    reasoning: str | None = None
    attempts: int = 1
    raw: dict = field(default_factory=dict)
    usage_reported: bool = True

    def assistant_message(self) -> dict:
        msg: dict = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            msg["tool_calls"] = [{"id": t.id, "type": "function",
                                  "function": {"name": t.name, "arguments": t.raw_arguments or json.dumps(t.arguments)}}
                                 for t in self.tool_calls]
        return msg


# ---------------------------------------------------------------- JSON helpers

_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S | re.I)


def strip_reasoning(text: str) -> str:
    text = _THINK.sub("", text or "")
    if "</think>" in text:  # opening tag consumed by the server
        text = text.split("</think>", 1)[1]
    return text.strip()


def extract_json(text: str):
    """Parse the first JSON value in a model reply (tolerates <think> blocks and ``` fences)."""
    t = strip_reasoning(text)
    m = _FENCE.search(t)
    if m:
        t = m.group(1).strip()
    try:
        return json.loads(t, parse_constant=_reject_constant)
    except (ValueError, TypeError, RecursionError):
        pass
    dec = json.JSONDecoder(parse_constant=_reject_constant)
    tries = 0
    for i, ch in enumerate(t):
        if ch in "{[":
            tries += 1
            if tries > 64:  # bounded scan: adversarial text full of brackets must not go quadratic
                break
            try:
                return dec.raw_decode(t, i)[0]
            except (ValueError, RecursionError):
                continue
    raise ValueError("no JSON value found in model output")


@dataclass
class JsonSpec:
    schema: dict | None
    name: str = "response"
    model_cls: Any = None


def json_spec(response_format) -> JsonSpec | None:
    if response_format is None:
        return None
    if isinstance(response_format, type) and hasattr(response_format, "model_json_schema"):
        return JsonSpec(response_format.model_json_schema(), response_format.__name__, response_format)
    if isinstance(response_format, dict):
        t = response_format.get("type")
        if t == "json_schema":
            js = response_format.get("json_schema") or {}
            return JsonSpec(js.get("schema"), js.get("name", "response"))
        if t == "json_object":
            return JsonSpec(None, "response")
        if t in ("object", "array") or "properties" in response_format:
            return JsonSpec(response_format, response_format.get("title", "response"))
    raise LLMError(f"unsupported response_format: {response_format!r}")


def validate_json(data, spec: JsonSpec):
    """Returns (parsed, errors)."""
    errors: list[str] = []
    if spec.schema:
        import jsonschema
        v = jsonschema.Draft202012Validator(spec.schema)
        errors = [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
                  for e in sorted(v.iter_errors(data), key=lambda e: list(map(str, e.path)))]
    if errors:
        return None, errors
    if spec.model_cls is not None:
        try:
            return spec.model_cls.model_validate(data), []
        except Exception as e:  # pydantic.ValidationError
            return None, [str(e)]
    return data, []


def example_from_schema(schema: dict, root: dict | None = None):
    """Deterministic minimal instance of a JSON schema (used by the offline default policy)."""
    root = root or schema
    if "$ref" in schema:
        ref = schema["$ref"]
        node = root
        for part in ref.lstrip("#/").split("/"):
            node = node[part]
        return example_from_schema(node, root)
    if "default" in schema:
        return schema["default"]
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    for k in ("anyOf", "oneOf", "allOf"):
        if k in schema:
            opts = [o for o in schema[k] if o.get("type") != "null"] or schema[k]
            return example_from_schema(opts[0], root)
    t = schema.get("type", "object")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), "null")
    if t == "object":
        props = schema.get("properties", {})
        return {k: example_from_schema(v, root) for k, v in props.items()}
    if t == "array":
        item = schema.get("items", {})
        return [example_from_schema(item, root) for _ in range(int(schema.get("minItems", 0)))]
    if t == "string":
        return "text"
    if t in ("number", "integer"):
        v = schema.get("minimum", schema.get("exclusiveMinimum", 0))
        return int(v) if t == "integer" else float(v)
    if t == "boolean":
        return False
    return None


def normalize_tools(tools) -> list[dict] | None:
    if not tools:
        return None
    out = []
    for t in tools:
        if t.get("type") == "function" and "function" in t:
            out.append(t)
        else:
            out.append({"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                                         "parameters": t.get("parameters", {"type": "object",
                                                                                            "properties": {}})}})
    return out


def _approx_tokens(obj) -> int:
    s = obj if isinstance(obj, str) else json.dumps(obj, default=str)
    return max(1, len(s) // 4)


# ---------------------------------------------------------------- offline backend (deterministic policy)

class OfflineLLM:
    """Deterministic offline backend. Script per role: items are str (content), dict
    {"content", "tool_calls": [{"name", "arguments"}]}, an Exception (raised), or a callable
    f(messages=, tools=, schema=) -> str|dict. When a role's script is empty, `policy` (or default_policy) answers."""

    def __init__(self, script: dict[str, list] | None = None, policy: Callable | None = None):
        self.script: dict[str, deque] = {r: deque(v) for r, v in (script or {}).items()}
        self.policy = policy
        self.calls: list[dict] = []
        self._n = 0

    def add(self, role: str, *responses):
        self.script.setdefault(role, deque()).extend(responses)

    @staticmethod
    def default_policy(role, messages, tools, schema):
        if schema is not None:
            return json.dumps(example_from_schema(schema))
        names = [t["function"]["name"] for t in (tools or [])]
        if "finish" in names:
            return {"content": "", "tool_calls": [{"name": "finish", "arguments": {"reason": "offline default policy"}}]}
        return "Offline response (CLEARANCE_LLM_MODE=offline)."

    def complete(self, role: str, model_id: str, messages: list, tools, schema) -> dict:
        self.calls.append({"role": role, "messages": [dict(m) for m in messages], "tools": tools, "schema": schema})
        q = self.script.get(role)
        item = q.popleft() if q else None
        if item is None:
            item = (self.policy or self.default_policy)(role, messages, tools, schema)
        elif callable(item) and not isinstance(item, BaseException):
            item = item(messages=messages, tools=tools, schema=schema)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, str):
            item = {"content": item}
        tcs = []
        for tc in item.get("tool_calls") or []:
            self._n += 1
            args = tc.get("arguments", {})
            tcs.append({"id": tc.get("id") or f"call_offline_{self._n}", "type": "function",
                        "function": {"name": tc["name"],
                                     "arguments": args if isinstance(args, str) else json.dumps(args)}})
        content = item.get("content") or ""
        msg = {"role": "assistant", "content": content}
        if tcs:
            msg["tool_calls"] = tcs
        return {"id": f"offline-{len(self.calls)}", "model": model_id,
                "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if tcs else "stop"}],
                "usage": {"prompt_tokens": _approx_tokens(messages), "completion_tokens": _approx_tokens(content or tcs)}}


# ---------------------------------------------------------------- HTTP transport

RETRYABLE_STATUS = {408, 409, 425, 429}
MAX_BODY_BYTES = 16 * 1024 * 1024


def parse_retry_after(value, now: float | None = None) -> float | None:
    """Retry-After header -> seconds (delta-seconds or HTTP-date); None if absent or unusable. Never negative."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        import email.utils
        try:
            d = email.utils.parsedate_to_datetime(s)
        except (TypeError, ValueError, IndexError):
            return None
        if d is None:
            return None
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        v = d.timestamp() - (time.time() if now is None else now)
    if v != v:  # NaN
        return None
    return max(0.0, v)


def _decode_json_body(body: bytes):
    """JSON from a response body; invalid UTF-8 bytes are replaced rather than failing the whole call."""
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        text = body.decode("utf-8", errors="replace")
    return json.loads(text)  # NaN/Infinity tolerated here; counts are sanitized and tool arguments re-checked


def _reject_constant(name):
    raise ValueError(f"non-finite JSON constant {name}")


class HttpTransport:
    """POST one chat completion (no streaming) through httpx. `transport` takes any httpx.BaseTransport, so tests
    drive the real request/response handling with a scripted transport. The whole exchange (connect + body) must
    finish within `timeout` seconds; bodies over MAX_BODY_BYTES are refused."""

    def __init__(self, transport=None, *, max_body_bytes: int = MAX_BODY_BYTES, clock: Callable[[], float] = time.monotonic):
        self._transport = transport
        self.max_body_bytes = int(max_body_bytes)
        self.clock = clock
        self._client = None
        self._pid = os.getpid()

    def _get_client(self):
        import httpx
        if self._client is not None and self._pid != os.getpid():  # forked worker: never share parent sockets
            self._client = None
        if self._client is None:
            self._client = httpx.Client(transport=self._transport) if self._transport is not None else httpx.Client()
            self._pid = os.getpid()
        return self._client

    def close(self):
        if self._client is not None:
            self._client.close()
            self._client = None

    def __call__(self, url: str, headers: dict, payload: dict, timeout: float) -> dict:
        import httpx
        t0 = self.clock()
        try:
            with self._get_client().stream("POST", url, headers=headers, json=payload, timeout=timeout) as r:
                chunks, size = [], 0
                for chunk in r.iter_bytes():
                    size += len(chunk)
                    if size > self.max_body_bytes:
                        raise TransportError(f"HTTP {r.status_code}: response body over {self.max_body_bytes} bytes",
                                             status=r.status_code, retryable=False)
                    if self.clock() - t0 > timeout:
                        raise TransportError(f"response not complete within {timeout:g} s", status=r.status_code)
                    chunks.append(chunk)
                status, rheaders = r.status_code, r.headers
        except (httpx.TimeoutException, httpx.TransportError, httpx.StreamError) as e:
            raise TransportError(f"{type(e).__name__}: {e}", retryable=True) from e
        body = b"".join(chunks)
        text = body[:500].decode("utf-8", errors="replace")
        if status in RETRYABLE_STATUS or status >= 500:
            raise TransportError(f"HTTP {status}: {text[:300]}", status=status,
                                 retry_after=parse_retry_after(rheaders.get("retry-after")))
        if status >= 400:
            raise TransportError(f"HTTP {status}: {text}", status=status, retryable=False)
        if "text/event-stream" in (rheaders.get("content-type") or "").lower():
            raise TransportError("server answered with an event stream; the client requests stream=false",
                                 status=status, retryable=False)
        try:
            return _decode_json_body(body)
        except (ValueError, RecursionError) as e:
            # truncated or garbled body: transient on a healthy endpoint, so retry
            raise TransportError(f"HTTP {status}: body is not valid JSON ({type(e).__name__}: {str(e)[:120]}); "
                                 f"{len(body)} bytes", status=status, retryable=True) from e


http_transport = HttpTransport()


# ---------------------------------------------------------------- client

class LLM:
    def __init__(self, mode: str | None = None, *, config: dict | None = None, config_path: str | Path | None = None,
                 offline: OfflineLLM | None = None, transport: Callable | None = None, log: bool = True,
                 sleep: Callable[[float], None] = time.sleep):
        self.mode = (mode or os.environ.get("CLEARANCE_LLM_MODE")
                     or ("live" if os.environ.get("NEBIUS_API_KEY") else "offline")).lower()
        if self.mode not in ("live", "offline"):
            raise LLMError(f"CLEARANCE_LLM_MODE must be live|offline, got {self.mode!r}")
        self.config = config or load_models_config(config_path)
        self.offline = offline or (OfflineLLM() if self.mode == "offline" else None)
        self.transport = transport or http_transport
        self.log = log
        self.sleep = sleep

    def model_id(self, role: str) -> str:
        return resolve_role(self.config, role, live=self.mode == "live")["model_id"]

    # one raw completion with retries
    def _complete(self, target: dict, messages, tools, spec: JsonSpec | None, *, temperature, max_tokens,
                  tool_choice, timeout, extra) -> dict:
        if self.mode == "offline":
            return self.offline.complete(target["role"], target["model_id"], messages, tools,
                                         spec.schema if spec else None), 1
        key = os.environ.get(target["api_key_env"] or "")
        if not key and target["provider"] == "token_factory":
            raise LLMError(f"live mode needs {target['api_key_env']} (or set CLEARANCE_LLM_MODE=offline)")
        payload: dict = {"model": target["model_id"], "messages": messages}
        # Per-candidate request options from models.yaml, e.g. chat_template_kwargs to turn off reasoning traces.
        for k, v in (target.get("request_extra") or {}).items():
            payload.setdefault(k, v)
        if tools:
            payload["tools"] = tools
            if tool_choice:
                payload["tool_choice"] = tool_choice
        if spec is not None and not tools:
            mode = self.config.get("structured_output", "json_schema")
            if mode == "json_schema" and spec.schema:
                payload["response_format"] = {"type": "json_schema",
                                              "json_schema": {"name": re.sub(r"\W", "_", spec.name)[:64],
                                                              "schema": spec.schema}}
            elif mode in ("json_schema", "json_object"):
                payload["response_format"] = {"type": "json_object"}
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        extra = dict(extra or {})
        for k in ("stream", "stream_options"):  # responses are read whole; streaming is never requested
            if extra.pop(k, None):
                log.warning("ignoring %s in extra_body: the client does not stream", k)
        payload.update(extra)
        if not target.get("base_url"):
            raise LLMError(f"role {target['role']}: no base_url configured")
        url = target["base_url"].rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        retries = max(0, int(self.config.get("max_retries", 4)))
        base, cap = float(self.config.get("backoff_base_s", 1.0)), float(self.config.get("backoff_max_s", 30.0))
        ra_cap = float(self.config.get("retry_after_max_s", max(cap, 60.0)))
        tmo = float(timeout or self.config.get("timeout_s", 120))
        for attempt in range(retries + 1):
            try:
                raw = self.transport(url, headers, payload, tmo)
                return raw, attempt + 1
            except TransportError as e:
                e.http_attempts = attempt + 1
                if not e.retryable or attempt == retries:
                    raise
                ra = e.retry_after
                if ra is not None and not (ra <= ra_cap):  # also catches inf
                    stop = TransportError(f"{e} (server asks to retry after {ra:.0f} s, over the {ra_cap:.0f} s "
                                          "limit; giving up)", status=e.status, retry_after=ra, retryable=False)
                    stop.http_attempts = attempt + 1
                    raise stop from e
                delay = max(0.0, ra) if ra is not None else min(cap, base * 2 ** attempt)
                self.sleep(delay * (1.0 + 0.25 * random.random()))
        raise LLMError("unreachable")

    def chat(self, role: Role, messages: list[dict], *, tools=None, response_format=None, tool_choice=None,
             temperature: float | None = None, max_tokens: int | None = None, kind: str | None = None,
             meta: dict | None = None, repair_retries: int = 1, timeout: float | None = None,
             extra_body: dict | None = None) -> LLMResponse:
        target = resolve_role(self.config, role, live=self.mode == "live")
        spec = json_spec(response_format)
        tools_n = normalize_tools(tools)
        msgs = [dict(m) for m in messages]
        if spec is not None:
            note = "Respond with only a JSON value, no prose."
            if spec.schema:
                note += " It must validate against this JSON Schema:\n" + json.dumps(spec.schema)
            if msgs and msgs[0].get("role") == "system" and isinstance(msgs[0].get("content"), str):
                msgs[0] = {**msgs[0], "content": msgs[0]["content"] + "\n\n" + note}
            else:
                msgs = [{"role": "system", "content": note}] + msgs
        attempts = 0
        tin = tout = 0
        while True:
            attempts += 1
            t0 = time.perf_counter()
            err, raw, http_attempts = None, None, 1
            try:
                raw, http_attempts = self._complete(target, msgs, tools_n, spec, temperature=temperature,
                                                    max_tokens=max_tokens, tool_choice=tool_choice, timeout=timeout,
                                                    extra=extra_body)
                resp = self._parse(raw, target, role)
            except Exception as e:
                err = e
            dt = time.perf_counter() - t0
            if err is not None:
                u = _usage_counts(raw)
                self._log(kind or f"llm.{role}", target, u[0], u[1], dt,
                          {**(meta or {}), "role": role, "ok": False, "attempt": attempts,
                           "http_attempts": getattr(err, "http_attempts", http_attempts),
                           "status": getattr(err, "status", None), "error": f"{type(err).__name__}: {err}"[:300],
                           "usage_reported": u[2]})
                raise err
            tin += resp.tokens_in
            tout += resp.tokens_out
            self._log(kind or f"llm.{role}", target, resp.tokens_in, resp.tokens_out, dt,
                      {**(meta or {}), "role": role, "ok": True, "attempt": attempts, "http_attempts": http_attempts,
                       "tool_calls": [t.name for t in resp.tool_calls], "usage_reported": resp.usage_reported})
            resp.attempts = attempts
            if spec is None or resp.tool_calls:
                break
            try:
                data = extract_json(resp.content)
                parsed, errors = validate_json(data, spec)
            except ValueError as e:
                parsed, errors = None, [str(e)]
            if not errors:
                resp.parsed = parsed
                break
            if attempts > repair_retries:
                raise SchemaValidationError(f"{role}: output failed JSON validation after {attempts} attempt(s)",
                                            errors, resp.content)
            msgs = msgs + [{"role": "assistant", "content": resp.content or ""},
                           {"role": "user", "content": "Your previous reply was not valid JSON for the schema. Errors:\n- "
                            + "\n- ".join(errors[:20]) + "\nReturn only the corrected JSON."}]
        resp.tokens_in, resp.tokens_out = tin, tout
        return resp

    def _parse(self, raw: dict, target: dict, role: str) -> LLMResponse:
        try:
            choice = raw["choices"][0]
            msg = choice.get("message") or {}
            if not isinstance(msg, dict):
                raise TypeError("message is not an object")
        except (KeyError, IndexError, TypeError, AttributeError) as e:
            raise LLMError(f"malformed completion: {str(raw)[:300]}") from e
        tcs = []
        raw_tcs = msg.get("tool_calls") or []
        for tc in raw_tcs if isinstance(raw_tcs, list) else []:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
            args, rawargs = parse_tool_arguments(fn.get("arguments"))
            name = fn.get("name")
            tcs.append(ToolCall(id=str(tc.get("id") or f"call_{len(tcs)}"), name=name if isinstance(name, str) else "",
                                arguments=args, raw_arguments=rawargs))
        tin, tout, reported = _usage_counts(raw)
        content = msg.get("content")
        content = content if isinstance(content, str) else ("" if content is None else json.dumps(content))
        reasoning = msg.get("reasoning_content") or msg.get("reasoning")
        return LLMResponse(content=content, tool_calls=tcs,
                           model_id=str(raw.get("model") or target["model_id"]), role=role,
                           tokens_in=tin, tokens_out=tout, finish_reason=choice.get("finish_reason"),
                           reasoning=reasoning if isinstance(reasoning, str) else None, raw=raw,
                           usage_reported=reported)

    def _log(self, kind, target, tin, tout, dt, meta):
        if not self.log:
            return
        model = target["model_id"] if self.mode == "live" else f"offline:{target['model_id']}"
        cost, known = 0.0, True
        p = target.get("price") or {}
        if self.mode == "live":
            pin, pout = price_value(p.get("input")), price_value(p.get("output"))
            known = ((tin == 0 or pin is not None) and (tout == 0 or pout is not None)
                     and meta.get("usage_reported", True) is not False)
            cost = (tin * (pin or 0.0) + tout * (pout or 0.0)) / 1e6 if known else 0.0
        try:
            usage.log_usage(kind, model, tin, tout, dt, cost, {**meta, "mode": self.mode, "cost_known": known,
                                                               "provider": target.get("provider"),
                                                               "price_verified": bool(p.get("verified"))})
        except Exception:  # usage logging must never break a model call
            log.warning("usage logging failed", exc_info=True)


MAX_TOOL_ARG_CHARS = 32_000


def price_value(v) -> float | None:
    """A per-1M-token price from config: a finite non-negative number, else None (unknown)."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and f >= 0 else None


def _count(v) -> int:
    if isinstance(v, bool):
        return 0
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0
    return int(f) if math.isfinite(f) and f > 0 else 0


def _usage_counts(raw) -> tuple[int, int, bool]:
    """(tokens_in, tokens_out, reported) from a completion's usage block."""
    u = raw.get("usage") if isinstance(raw, dict) else None
    if not isinstance(u, dict) or ("prompt_tokens" not in u and "completion_tokens" not in u):
        return 0, 0, False
    return _count(u.get("prompt_tokens")), _count(u.get("completion_tokens")), True


def parse_tool_arguments(rawargs) -> tuple[dict, str]:
    """Tool-call arguments -> (dict, raw string). Never raises: unparseable or oversized arguments come back as
    {"_unparseable_arguments": ...} / {"_oversized_arguments": n_chars} so the tool layer can reject them."""
    if rawargs is None or rawargs == "":
        return {}, "{}"
    if isinstance(rawargs, dict):
        try:
            s = json.dumps(rawargs, allow_nan=False)
        except (TypeError, ValueError, RecursionError):
            return {"_unparseable_arguments": str(rawargs)[:200]}, "{}"
        if len(s) > MAX_TOOL_ARG_CHARS:
            return {"_oversized_arguments": len(s)}, json.dumps({"_oversized_arguments": len(s)})
        return rawargs, s
    if not isinstance(rawargs, str):
        return {"_value": rawargs} if isinstance(rawargs, (int, float, list)) else {}, json.dumps(rawargs, default=str)
    if len(rawargs) > MAX_TOOL_ARG_CHARS:
        return {"_oversized_arguments": len(rawargs)}, json.dumps({"_oversized_arguments": len(rawargs)})
    try:
        args = json.loads(rawargs, parse_constant=_reject_constant) if rawargs.strip() else {}
    except (ValueError, RecursionError):
        try:
            args = extract_json(rawargs)
            json.dumps(args, allow_nan=False)
        except (ValueError, RecursionError):
            args = {"_unparseable_arguments": rawargs[:200]}
    if not isinstance(args, dict):
        args = {"_value": args}
    return args, rawargs


_DEFAULT: dict[str, LLM] = {}


def get_llm(mode: str | None = None) -> LLM:
    """Process-wide default client (one per mode)."""
    m = (mode or os.environ.get("CLEARANCE_LLM_MODE") or ("live" if os.environ.get("NEBIUS_API_KEY") else "offline")).lower()
    if m not in _DEFAULT:
        _DEFAULT[m] = LLM(m)
    return _DEFAULT[m]
