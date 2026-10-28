import datetime as dt
import importlib.util
import shutil
from pathlib import Path

import yaml

from agents.llm.client import DEFAULT_CONFIG, load_models_config, retires_on

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "verify_token_factory.py"


def load_script():
    spec = importlib.util.spec_from_file_location("verify_token_factory", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tf_ids(cfg):
    return [c["model_id"] for r in cfg["roles"].values() for c in r["candidates"] if c["provider"] == "token_factory"]


def test_apply_model_list_marks_and_selects():
    vt = load_script()
    cfg = load_models_config()
    ids = list(dict.fromkeys(_tf_ids(cfg)))
    missing = ids[-1]
    served = [i.upper() for i in ids if i != missing] + ["meta/llama"]  # case differs from config
    vt.apply_model_list(cfg, served, None, "now", today=dt.date(2026, 10, 27))
    for role, rc in cfg["roles"].items():
        listed = []
        for i, c in enumerate(rc["candidates"]):
            if c["provider"] == "self_hosted_vllm":
                assert c["verified"] is False and "available" not in c  # endpoint not checked
            elif c["model_id"] == missing:
                assert c["available"] is False and c["verify_note"] == "not in GET /v1/models"
            else:
                assert c["verified"] and c["available"] and c["model_id"] in served  # rewritten to served ID
                listed.append(i)
            assert c["price_per_1m_usd"]["verified"] is False  # prices never auto-verified
        steady = [i for i in listed if retires_on(rc["candidates"][i]) is None]
        if listed:
            assert rc["active"] == (steady or listed)[0], role


def test_apply_model_list_prefers_non_retiring_but_keeps_order():
    vt = load_script()
    cfg = load_models_config()
    cfg["roles"] = {"nano": {"active": 0, "candidates": [
        {"provider": "token_factory", "model_id": "a/old", "retires": dt.date(2099, 1, 1),
         "price_per_1m_usd": {"verified": False}},
        {"provider": "token_factory", "model_id": "a/new", "price_per_1m_usd": {"verified": False}},
        {"provider": "token_factory", "model_id": "a/gone", "retires": "2026-01-01",
         "price_per_1m_usd": {"verified": False}}]}}
    vt.apply_model_list(cfg, ["a/old", "a/new", "a/gone"], None, "now", today=dt.date(2026, 10, 27))
    assert cfg["roles"]["nano"]["active"] == 1
    assert [c["model_id"] for c in cfg["roles"]["nano"]["candidates"]] == ["a/old", "a/new", "a/gone"]
    vt.apply_model_list(cfg, ["a/old", "a/gone"], None, "now", today=dt.date(2026, 10, 27))
    assert cfg["roles"]["nano"]["active"] == 0  # only a retiring one is listed: use it
    w = vt.retirement_warnings(cfg, today=dt.date(2026, 10, 27))
    assert any("a/old" in x and "ACTIVE" in x for x in w) and any("a/gone" in x and "retired" in x for x in w)


def test_apply_model_list_vision_endpoint():
    vt = load_script()
    cfg = load_models_config()
    vis = cfg["roles"]["vision"]["candidates"]
    hosted_i = next(i for i, c in enumerate(vis) if c["provider"] == "self_hosted_vllm")
    vt.apply_model_list(cfg, [], [vis[hosted_i]["model_id"]], "now")
    assert cfg["roles"]["vision"]["active"] == hosted_i and vis[hosted_i]["verified"]


def test_replace_section_idempotent():
    vt = load_script()
    md = "# Verified\n\n## Rules\nx\n\n## Token Factory\nold\n\n## Serverless\ny\n"
    out = vt.replace_section(md, "## Token Factory\n\nnew\n")
    assert "old" not in out and "new" in out and "## Serverless\ny" in out and "## Rules\nx" in out
    assert vt.replace_section(out, "## Token Factory\n\nnew\n") == out
    assert vt.replace_section("", "## Token Factory\n\nz\n").startswith("# Verified facts")


def test_main_rewrites_config_and_verified_md(tmp_path, monkeypatch):
    vt = load_script()
    cfg_path = tmp_path / "models.yaml"
    shutil.copy(DEFAULT_CONFIG, cfg_path)
    monkeypatch.setattr(vt, "VERIFIED_MD", tmp_path / "VERIFIED.md")
    monkeypatch.setenv("NEBIUS_API_KEY", "k")
    served = _tf_ids(load_models_config())
    monkeypatch.setattr(vt, "http_get_models", lambda base, key, timeout=30.0: served)
    assert vt.main(["--config", str(cfg_path), "--skip-hello"]) == 0
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert cfg["base_url_verified"] is True
    sup = cfg["roles"]["super"]
    active = sup["candidates"][sup["active"]]
    assert active["verified"] is True
    assert retires_on(active) is None or all(retires_on(c) is not None for c in sup["candidates"])
    md = (tmp_path / "VERIFIED.md").read_text(encoding="utf-8")
    assert "## Token Factory" in md and active["model_id"] in md
    if any(retires_on(c) for r in cfg["roles"].values() for c in r["candidates"]):
        assert "Retirement notices:" in md


def test_main_without_key(monkeypatch, tmp_path):
    vt = load_script()
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)
    assert vt.main(["--config", str(DEFAULT_CONFIG), "--dry-run"]) == 2
