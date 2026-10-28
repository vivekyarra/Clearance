"""Model selection (retired models never chosen), the verify script against a scripted /models endpoint, and
determinism of offline mode across processes."""
import datetime as dt
import json
import os
import random
import subprocess
import sys

import pytest

from agents.llm.client import LLM, LLMError, load_models_config, resolve_role, retires_invalid, retires_on

from agents_stress_helpers import HERE, ScriptedTransport, ok

sys.path.insert(0, str(HERE.parents[2] / "scripts"))
import verify_token_factory as vt  # noqa: E402

TODAY = dt.date(2026, 10, 27)


def cand(mid, retires=None, **kw):
    c = {"provider": "token_factory", "model_id": mid, "price_per_1m_usd": {"input": None, "output": None}, **kw}
    if retires is not None:
        c["retires"] = retires
    return c


def role_cfg(cands, active=0):
    return {"base_url": "https://example.invalid/v1", "roles": {"nano": {"active": active, "candidates": cands}}}


def is_retired(c, today):
    r = retires_on(c)
    return retires_invalid(c) or (r is not None and r <= today)


def test_random_configs_never_pick_a_retired_model():
    rng = random.Random(7)
    for trial in range(2000):
        today = TODAY + dt.timedelta(days=rng.randint(-30, 30))
        cands = []
        for i in range(rng.randint(1, 5)):
            kind = rng.random()
            if kind < 0.3:
                ret = None
            elif kind < 0.8:
                ret = (today + dt.timedelta(days=rng.randint(-5, 5))).isoformat()
            elif kind < 0.9:
                ret = rng.choice(["soon", "2026-13-01", "11/09/2026", "", 20261109])
            else:
                ret = today + dt.timedelta(days=rng.randint(-2, 2))
            cands.append(cand(f"m{i}", ret, **({"available": False} if rng.random() < 0.1 else {})))
        cfg = role_cfg(cands, active=rng.randint(-3, 8))
        usable = [c for c in cands if c.get("available") is not False and not is_retired(c, today)]
        if not usable:
            with pytest.raises(LLMError):
                resolve_role(cfg, "nano", today=today)
            continue
        pick = resolve_role(cfg, "nano", today=today)
        chosen = next(c for c in cands if c["model_id"] == pick["model_id"])
        assert not is_retired(chosen, today), (trial, cands, today)
        if any(retires_on(c) is None and not retires_invalid(c) for c in usable):
            assert pick["retires"] is None  # a steady candidate always wins over a retiring one


def test_retirement_day_itself_counts_as_retired():
    cfg = role_cfg([cand("old", "2026-11-09"), cand("new")])
    assert resolve_role(cfg, "nano", today=dt.date(2026, 11, 8))["model_id"] == "new"  # steady first
    cfg = role_cfg([cand("old", "2026-11-09")])
    assert resolve_role(cfg, "nano", today=dt.date(2026, 11, 8))["model_id"] == "old"
    with pytest.raises(LLMError):
        resolve_role(cfg, "nano", today=dt.date(2026, 11, 9))


def test_unreadable_retires_value_is_never_used():
    cfg = role_cfg([cand("odd", "Nov 9th"), cand("fine")])
    assert resolve_role(cfg, "nano", today=TODAY)["model_id"] == "fine"
    with pytest.raises(LLMError):
        resolve_role(role_cfg([cand("odd", "Nov 9th")]), "nano", today=TODAY)


def test_shipped_config_never_resolves_to_a_retired_model_over_the_next_year():
    cfg = load_models_config()
    for days in range(0, 400, 3):
        today = TODAY + dt.timedelta(days=days)
        for role in cfg["roles"]:
            try:
                t = resolve_role(cfg, role, live=False, today=today)
            except LLMError:
                continue
            assert t["retires"] is None or dt.date.fromisoformat(t["retires"]) > today, (role, today)


def test_retired_model_not_chosen_by_the_client_in_a_live_call(monkeypatch):
    from agents.llm.client import HttpTransport
    from agents_stress_helpers import completion
    monkeypatch.setenv("NEBIUS_API_KEY", "k")
    past = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    cfg = role_cfg([cand("gone", past), cand("here")])
    st = ScriptedTransport([ok(completion("x", model="here"))])
    LLM("live", config=cfg, transport=HttpTransport(st)).chat("nano", [{"role": "user", "content": "x"}])
    assert json.loads(st.requests[0].content)["model"] == "here"


# ---------------- verify script

def test_verify_script_never_activates_a_retired_or_unreadable_candidate():
    cfg = {"base_url": "https://x/v1", "roles": {"nano": {"active": 0, "candidates": [
        cand("A", "2026-01-01"), cand("B", "garbage"), cand("C", "2027-01-01"), cand("D")]}}}
    vt.apply_model_list(cfg, ["A", "B", "C", "D"], None, "now", today=TODAY)
    assert cfg["roles"]["nano"]["active"] == 3
    cfg["roles"]["nano"]["candidates"].pop()
    vt.apply_model_list(cfg, ["A", "B", "C"], None, "now", today=TODAY)
    assert cfg["roles"]["nano"]["active"] == 2
    warns = vt.retirement_warnings(cfg, today=TODAY)
    assert any("unreadable" in w and "`B`" in w for w in warns) and any("`A` retired" in w for w in warns)


def test_verify_script_models_listing_tolerates_junk():
    st = ScriptedTransport([ok({"data": [{"id": "nvidia/X"}, {"id": None}, {"name": "no id"}, "plain/id", 5,
                                         {"id": "  "}, None, {"id": "nvidia/X"}]})])
    assert vt.http_get_models("https://x/v1", "k", transport=st) == ["nvidia/X", "plain/id"]
    assert st.requests[0].headers["authorization"] == "Bearer k"
    with pytest.raises(ValueError):
        vt.http_get_models("https://x/v1", "k", transport=ScriptedTransport([ok({"data": "nope"})]))


# ---------------- determinism of offline mode across processes

def _run_in_subprocess(tmp, seed, policy):
    env = {**os.environ, "PYTHONHASHSEED": str(seed), "CLEARANCE_LLM_MODE": "offline",
           "CLEARANCE_TAVILY_MODE": "offline", "PYTHONPATH": str(HERE.parents[2])}
    env.pop("NEBIUS_API_KEY", None)
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import agents_stress_helpers as h; "
            "sys.stdout.write(h.offline_run(sys.argv[2], sys.argv[3]))")
    out = subprocess.run([sys.executable, "-c", code, str(HERE), str(tmp), policy], env=env, capture_output=True,
                         text=True, encoding="utf-8", timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    return out.stdout


@pytest.mark.parametrize("policy", ["offline", "llm"])
def test_offline_mode_is_identical_across_processes_and_hash_seeds(tmp_path, policy):
    a = _run_in_subprocess(tmp_path / "a", 1, policy)
    b = _run_in_subprocess(tmp_path / "b", 4242, policy)
    assert a == b
    inv = json.loads(a)
    assert inv["mode"] == "offline" and inv["steps"]
