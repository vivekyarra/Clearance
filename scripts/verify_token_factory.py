"""Day-1 verification of Token Factory model IDs (PLAN 13 Phase 0 / 21 step 3).

    NEBIUS_API_KEY=... python scripts/verify_token_factory.py [--dry-run] [--skip-hello]

1. GET {base_url}/models -> list of model IDs actually served.
2. For every role candidate in config/models.yaml: mark verified/available (case-insensitive match is
   rewritten to the exact served ID). Self-hosted vision (CLEARANCE_VISION_BASE_URL) is checked via its own /models.
3. Each role's `active` = first available candidate; one hello-world chat per tier (latency, tokens, ok).
4. Rewrite config/models.yaml and the "Token Factory" section of docs/VERIFIED.md.
Prices are never verified by this script (no pricing API); they stay `verified: false` until checked by hand.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from agents.llm.client import (DEFAULT_CONFIG, LLM, load_models_config, retires_invalid,  # noqa: E402
                               today_for_retirement,
                               retires_on)

VERIFIED_MD = REPO / "docs" / "VERIFIED.md"
SECTION = "## Token Factory"


def http_get_models(base_url: str, api_key: str | None, timeout: float = 30.0, transport=None) -> list[str]:
    """IDs served by GET {base_url}/models. `transport` takes an httpx.BaseTransport (tests)."""
    import httpx
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    with httpx.Client(transport=transport, timeout=timeout) as client:
        r = client.get(base_url.rstrip("/") + "/models", headers=headers)
    r.raise_for_status()
    data = r.json()
    items = data.get("data", data) if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError(f"unexpected /models payload: {str(data)[:200]}")
    ids = ((m.get("id") if isinstance(m, dict) else m) for m in items if m)
    return sorted({i.strip() for i in ids if isinstance(i, str) and i.strip()})


def apply_model_list(cfg: dict, served: list[str] | None, vision_served: list[str] | None, now: str,
                     today: dt.date | None = None) -> dict:
    """Mark candidates verified/available against served IDs and pick each role's `active` candidate: the first
    listed one (config order) without a `retires` date, else the first listed retiring one. Candidates past their
    retirement date are never picked. Returns {role: active_index}. Pure function."""
    today = today or today_for_retirement()
    lower = {s.lower(): s for s in (served or [])}
    vlower = {s.lower(): s for s in (vision_served or [])}
    for role, rc in cfg["roles"].items():
        listed: list[int] = []
        unchecked: list[int] = []
        for i, c in enumerate(rc.get("candidates", [])):
            pool = vlower if c.get("provider") == "self_hosted_vllm" else lower
            listing = vision_served if c.get("provider") == "self_hosted_vllm" else served
            if listing is None:  # could not check this provider
                c["verified"] = False
                c.pop("available", None)
                c["verify_note"] = "endpoint not checked (env unset or unreachable)"
                unchecked.append(i)
                continue
            exact = pool.get(str(c.get("model_id") or "").lower())
            if exact:
                c["model_id"] = exact
                c["verified"] = True
                c["available"] = True
                c["verified_at"] = now
                c.pop("verify_note", None)
                ret = retires_on(c)
                if not retires_invalid(c) and (ret is None or ret > today):
                    listed.append(i)
            else:
                c["verified"] = False
                c["available"] = False
                c["verified_at"] = now
                c["verify_note"] = "not in GET /v1/models"
        cands = rc.get("candidates", [])
        steady = [i for i in listed if retires_on(cands[i]) is None]
        active = (steady or listed or [None])[0]
        prev = int(rc.get("active", 0) or 0)
        if prev in unchecked:  # a self-hosted primary (Cosmos Reason 2) stays primary; the client falls back when unset
            active = prev
        rc["active"] = active if active is not None else int(rc.get("active", 0) or 0)
    return {r: rc["active"] for r, rc in cfg["roles"].items()}


def retirement_warnings(cfg: dict, today: dt.date | None = None) -> list[str]:
    """One line per candidate with a `retires` date (flagging active ones and dates already passed)."""
    today = today or today_for_retirement()
    out = []
    for role, rc in cfg["roles"].items():
        for i, c in enumerate(rc.get("candidates", [])):
            if retires_invalid(c):
                out.append(f"{role}: `{c.get('model_id')}` has an unreadable retires value {c.get('retires')!r}; "
                           "treated as retired")
                continue
            ret = retires_on(c)
            if ret is None:
                continue
            state = "retired" if ret <= today else "retires"
            act = " (ACTIVE: switch before then)" if i == rc.get("active") and ret > today else ""
            out.append(f"{role}: `{c['model_id']}` {state} {ret.isoformat()}{act}")
    return out


def hello_world(llm: LLM, role: str) -> dict:
    t0 = time.perf_counter()
    try:
        r = llm.chat(role, [{"role": "user", "content": "Reply with the single word: ok"}], max_tokens=64,
                     kind="verify.hello", meta={"script": "verify_token_factory"})
        return {"ok": True, "model": r.model_id, "latency_s": round(time.perf_counter() - t0, 2),
                "tokens_in": r.tokens_in, "tokens_out": r.tokens_out, "reply": (r.content or "").strip()[:60]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "latency_s": round(time.perf_counter() - t0, 2)}


def render_section(cfg: dict, served: list[str] | None, hello: dict, now: str, vision_url: str | None) -> str:
    lines = [SECTION, "", f"Checked {now} by `scripts/verify_token_factory.py` against `{cfg['base_url']}`.", ""]
    if served is None:
        lines += ["GET /v1/models: FAILED (see console). Nothing below is verified.", ""]
    else:
        nv = [s for s in served if "nemotron" in s.lower() or "nvidia" in s.lower() or "cosmos" in s.lower()]
        lines += [f"GET /v1/models returned {len(served)} models; NVIDIA-related: "
                  + (", ".join(f"`{s}`" for s in nv) or "none"), ""]
    lines += ["| Role | Candidate | Provider | Listed | Retires | Active | Hello-world |",
              "|---|---|---|---|---|---|---|"]
    for role, rc in cfg["roles"].items():
        for i, c in enumerate(rc.get("candidates", [])):
            listed = {True: "yes", False: "no"}.get(c.get("available"), "not checked")
            h = hello.get(role) if i == rc.get("active") else None
            hs = "" if h is None else (f"ok, {h['latency_s']} s, {h['tokens_in']}+{h['tokens_out']} tok"
                                       if h.get("ok") else f"FAILED: {h.get('error', '')}")
            ret = retires_on(c)
            lines.append(f"| {role} | `{c['model_id']}` | {c.get('provider')} | {listed} | "
                         f"{ret.isoformat() if ret else ''} | {'yes' if i == rc.get('active') else ''} | {hs} |")
    warns = retirement_warnings(cfg)
    if warns:
        lines += ["", "Retirement notices:"] + [f"- {w}" for w in warns]
    lines += ["", f"Self-hosted vision endpoint (CLEARANCE_VISION_BASE_URL): {vision_url or 'unset, not checked'}.",
              "Prices in config/models.yaml are not checked by this script (no pricing API): confirm with account.",
              ""]
    return "\n".join(lines)


def replace_section(md: str, section: str) -> str:
    if SECTION not in md:
        return (md.rstrip() + "\n\n" if md.strip() else "# Verified facts\n\n") + section
    head, rest = md.split(SECTION, 1)
    nxt = rest.find("\n## ")
    tail = rest[nxt + 1:] if nxt >= 0 else ""
    return head + section + ("\n" + tail if tail else "")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--dry-run", action="store_true", help="print results, do not write files")
    ap.add_argument("--skip-hello", action="store_true")
    a = ap.parse_args(argv)
    cfg = load_models_config(a.config)
    key = os.environ.get(cfg.get("api_key_env", "NEBIUS_API_KEY"))
    if not key:
        print("NEBIUS_API_KEY is not set; nothing verified.", file=sys.stderr)
        return 2
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    try:
        served = http_get_models(cfg["base_url"], key)
        cfg["base_url_verified"] = True
    except Exception as e:
        print(f"GET /models failed: {e}", file=sys.stderr)
        served = None
    vision_url = os.environ.get("CLEARANCE_VISION_BASE_URL")
    vision_served = None
    if vision_url:
        try:
            vision_served = http_get_models(vision_url, os.environ.get("CLEARANCE_VISION_API_KEY"))
        except Exception as e:
            print(f"vision endpoint /models failed: {e}", file=sys.stderr)
    apply_model_list(cfg, served, vision_served, now)
    for w in retirement_warnings(cfg):
        print("WARNING:", w, file=sys.stderr)
    hello = {}
    if not a.skip_hello and served is not None:
        llm = LLM("live", config=cfg)
        for role in cfg["roles"]:
            rc = cfg["roles"][role]
            if rc["candidates"][rc["active"]].get("available"):
                hello[role] = hello_world(llm, role)
                print(role, hello[role])
    section = render_section(cfg, served, hello, now, vision_url)
    print(section)
    if a.dry_run:
        return 0
    header = ("# Token Factory model configuration. Rewritten by scripts/verify_token_factory.py on " + now + ".\n"
              "# `verified: true` = listed by GET /v1/models. Prices: confirm with account (no pricing API).\n")
    Path(a.config).write_text(header + yaml.safe_dump(cfg, sort_keys=False, width=120), encoding="utf-8")
    VERIFIED_MD.parent.mkdir(parents=True, exist_ok=True)
    old = VERIFIED_MD.read_text(encoding="utf-8") if VERIFIED_MD.exists() else ""
    VERIFIED_MD.write_text(replace_section(old, section), encoding="utf-8")
    return 0 if served is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
