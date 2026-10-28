"""scripts/release_proof.py: heading rule, per-model table, preflight refusal, key values never printed."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("release_proof", REPO / "scripts" / "release_proof.py")
rp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rp)

KEY_VALUES = ("k-123", "k-456", "t-789")
ALT = "alt_endpoint"  # a second OpenAI-compatible endpoint, generic name for tests


def make_config(alt_first: bool = False) -> dict:
    tf = {"provider": "token_factory", "model_id": "nvidia/nano-a"}
    alt = {"provider": ALT, "model_id": "alt/nano-b", "base_url": "https://alt.example.invalid/v1",
           "api_key_env": "ALT_API_KEY", "local": True}
    return {"base_url": "https://api.tokenfactory.nebius.com/v1/", "api_key_env": "NEBIUS_API_KEY",
            "roles": {"nano": {"active": 0, "candidates": [alt, tf] if alt_first else [tf, alt]},
                      "super": {"active": 0, "candidates": [{"provider": "token_factory",
                                                             "model_id": "nvidia/super-a",
                                                             "price_per_1m_usd": {"input": 0.1, "output": 0.4}}]}}}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for k in ("NEBIUS_API_KEY", "TAVILY_API_KEY", "ALT_API_KEY", "CLEARANCE_VISION_BASE_URL",
              "CLEARANCE_VISION_API_KEY", "CLEARANCE_MODELS_CONFIG"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(rp, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(rp, "PROOF_MD", tmp_path / "PROOF.md")
    monkeypatch.setattr(rp, "SAMPLE_SITES", tmp_path / "sample_sites")
    monkeypatch.setattr(rp, "CHECKS_DATA", tmp_path / "checks")


def make_db(path: Path, rows: list[tuple]) -> Path:
    """rows: (model_id, provider|None, role, tokens_in, tokens_out, duration_s, cost, analysis_id, mode, ok)."""
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE usage (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, model_id TEXT,"
                " tokens_in INTEGER, tokens_out INTEGER, duration_s REAL, cost_usd REAL, analysis_id TEXT, meta TEXT)")
    for mid, prov, role, tin, tout, d, cost, aid, mode, ok in rows:
        meta = {"role": role, "mode": mode, "ok": ok, "cost_known": True, "price_verified": False}
        if prov:
            meta["provider"] = prov
        con.execute("INSERT INTO usage (ts, kind, model_id, tokens_in, tokens_out, duration_s, cost_usd, analysis_id,"
                    " meta) VALUES (0, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (f"llm.{role}", mid, tin, tout, d, cost, aid, json.dumps(meta)))
    con.execute("INSERT INTO usage (ts, kind, model_id, tokens_in, tokens_out, duration_s, cost_usd, analysis_id,"
                " meta) VALUES (0, 'sim.batch', 'sim', 0, 0, 9.0, 0, 'an_ref_cafe_01', '{}')")
    con.commit()
    con.close()
    return path


# ------------------------------------------------------------------------------------------- heading rule
def test_heading_all_token_factory():
    h, line = rp.usage_heading(["token_factory", "token_factory"])
    assert h == "Nemotron on Nebius Token Factory (measured)"
    assert line == "All calls were served by Nebius Token Factory."


@pytest.mark.parametrize("providers", [["token_factory", ALT], [ALT], ["token_factory", "unknown"], []])
def test_heading_mixed_or_empty_names_no_provider(providers):
    h, line = rp.usage_heading(providers)
    assert h == "Nemotron usage (measured)" and line is None


def test_proof_md_heading_mixed_vs_nebius(tmp_path):
    cfg = make_config()
    mixed = make_db(tmp_path / "mixed.db", [
        ("nvidia/nano-a", "token_factory", "nano", 10, 5, 1.0, 0.0, None, "live", True),
        ("alt/nano-b", ALT, "nano", 20, 6, 2.0, 0.0, None, "live", True)])
    md = rp.write_proof(mixed, [], config=cfg)
    assert "## Nemotron usage (measured)" in md
    assert "Nebius" not in md
    assert "served by" not in md
    # the other endpoint is never named; no provider column, no "other" rows; the recorded model id is shown
    assert ALT not in md and "| other |" not in md and "| provider |" not in md
    assert "| alt/nano-b | 1 |" in md and "model outside" not in md

    nebius = make_db(tmp_path / "nebius.db", [
        ("nvidia/nano-a", "token_factory", "nano", 10, 5, 1.0, 0.0, None, "live", True),
        ("nvidia/super-a", "token_factory", "super", 100, 50, 3.0, 0.00003, None, "live", True),
        ("offline:nvidia/super-a", None, "super", 1, 1, 0.0, 0.0, None, "offline", True)])
    md = rp.write_proof(nebius, [], config=cfg)
    assert "## Nemotron on Nebius Token Factory (measured)" in md
    assert "All calls were served by Nebius Token Factory." in md
    assert "1 offline-mode call(s)" in md


def test_provider_derived_from_config_when_not_recorded(tmp_path):
    cfg = make_config()
    db = make_db(tmp_path / "u.db", [("nvidia/super-a", None, "super", 1, 1, 1.0, 0.0, None, "live", True)])
    rows = rp.load_usage_rows(db)
    assert rp.provider_of(rows[0], cfg) == "token_factory"
    assert rp.usage_heading([rp.provider_of(rows[0], cfg)])[0] == rp.HEADING_NEBIUS
    unknown = {**rows[0], "model_id": "x/unlisted", "meta": {"role": "nano"}}
    assert rp.provider_of(unknown, cfg) == "unknown"


# ------------------------------------------------------------------------------------------- table building
def test_model_table_from_usage_db(tmp_path):
    cfg = make_config()
    db = make_db(tmp_path / "u.db", [
        ("nvidia/super-a", "token_factory", "super", 100, 10, 1.0, 0.001, "an_ref_cafe_01", "live", True),
        ("nvidia/super-a", "token_factory", "super", 200, 20, 3.0, 0.002, "an_ref_cafe_01", "live", True),
        ("nvidia/super-a", "token_factory", "super", 300, 30, 2.0, 0.003, "an_ref_cafe_01", "live", False),
        ("nvidia/nano-a", "token_factory", "nano", 7, 3, 0.5, 0.0, "an_ref_cafe_01", "live", True),
        ("offline:nvidia/super-a", None, "super", 9, 9, 0.0, 0.0, "an_ref_cafe_01", "offline", True)])
    table = {t["model_id"]: t for t in rp.model_table(rp.load_usage_rows(db), cfg)}
    assert set(table) == {"nvidia/super-a", "nvidia/nano-a"}  # sim + offline rows excluded
    s = table["nvidia/super-a"]
    assert (s["calls"], s["failed"], s["tokens_in"], s["tokens_out"]) == (3, 1, 600, 60)
    assert s["median_latency_s"] == 2.0 and s["cost_usd"] == pytest.approx(0.006)
    assert table["nvidia/nano-a"]["calls"] == 1


def test_check_section_from_run_record(tmp_path):
    cfg = make_config()
    run = {"analysis_id": "an_ref_cafe_01", "wall_time_s": 12.5, "llm_mode": "live", "tavily_mode": "live",
           "finished_at": "2026-11-01 10:00:00", "factcheck": {"ok": True, "errors": []},
           "investigation": {"experiments_run": 4, "findings": 2, "findings_confirmed": 1},
           "verdicts": {"r1": {"label": "Cleared"}, "r2": {"label": "Not cleared"}}}
    d = tmp_path / "sample_sites" / "ref_cafe" / "analyses" / "an_ref_cafe_01"
    d.mkdir(parents=True)
    (d / "run.json").write_text(json.dumps(run), encoding="utf-8")
    db = make_db(tmp_path / "u.db", [
        ("nvidia/super-a", "token_factory", "super", 1, 1, 1.0, 0.0, "an_ref_cafe_01", "live", True),
        ("nvidia/nano-a", "token_factory", "nano", 1, 1, 1.0, 0.0, "an_ref_cafe_01", "live", True),
        ("nvidia/nano-a", "token_factory", "nano", 1, 1, 1.0, 0.0, "an_ref_cafe_01", "live", True)])
    md = rp.write_proof(db, ["ref_cafe", "ref_corridor"], config=cfg)
    assert "- Wall time: 12.5 s" in md
    assert "- LLM calls by tier: nano 2, super 1" in md
    assert "- Experiments: 4" in md and "- Fact-check: passed" in md
    assert "r1: Cleared; r2: Not cleared" in md
    assert "### ref_corridor" in md and "No run record yet" in md


def test_missing_usage_db(tmp_path):
    md = rp.write_proof(tmp_path / "absent.db", [], config=make_config())
    assert "No live model calls are recorded yet." in md


# ------------------------------------------------------------------------------------------- preflight
def test_preflight_refuses_when_a_role_resolves_elsewhere(monkeypatch):
    monkeypatch.setenv("ALT_API_KEY", "k-123")
    monkeypatch.setenv("TAVILY_API_KEY", "t-789")
    lines: list[str] = []
    pf = rp.preflight(require_provider="token_factory", config=make_config(alt_first=True), out=lines.append)
    assert pf["roles"]["nano"]["provider"] == ALT
    assert pf["refused"] and "nano" in pf["refused"][0]
    assert pf["keys"]["ALT_API_KEY"] == "yes" and pf["keys"]["NEBIUS_API_KEY"] == "no"


def test_preflight_passes_when_all_token_factory(monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "k-456")
    monkeypatch.setenv("TAVILY_API_KEY", "t-789")
    pf = rp.preflight(require_provider="token_factory", config=make_config(), out=lambda s: None)
    assert pf["refused"] == []
    assert {v["provider"] for v in pf["roles"].values()} == {"token_factory"}


def test_preflight_missing_keys_block_checks_only(monkeypatch):
    pf = rp.preflight(config=make_config(), will_run_checks=False, out=lambda s: None)
    assert pf["problems"] and pf["refused"] == []
    pf = rp.preflight(config=make_config(), will_run_checks=True, out=lambda s: None)
    assert pf["refused"]


def _write_cfg(tmp_path, monkeypatch, cfg):
    p = tmp_path / "models.yaml"
    p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    monkeypatch.setenv("CLEARANCE_MODELS_CONFIG", str(p))


def test_main_refuses_and_never_prints_key_values(tmp_path, monkeypatch, capsys):
    _write_cfg(tmp_path, monkeypatch, make_config(alt_first=True))
    monkeypatch.setenv("ALT_API_KEY", "k-123")
    monkeypatch.setenv("NEBIUS_API_KEY", "k-456")
    monkeypatch.setenv("TAVILY_API_KEY", "t-789")
    monkeypatch.setattr(rp.subprocess, "call", lambda *a, **k: pytest.fail("no subprocess on refusal"))
    rc = rp.main(["--require-provider", "token_factory", "--usage-db", str(tmp_path / "u.db")])
    out = capsys.readouterr()
    assert rc == 3
    assert "STOP: --require-provider token_factory" in out.out
    for v in KEY_VALUES:
        assert v not in out.out and v not in out.err


def test_skip_checks_rebuilds_proof_without_printing_keys(tmp_path, monkeypatch, capsys):
    cfg = make_config()
    _write_cfg(tmp_path, monkeypatch, cfg)
    for k, v in zip(("NEBIUS_API_KEY", "ALT_API_KEY", "TAVILY_API_KEY"), KEY_VALUES):
        monkeypatch.setenv(k, v)
    db = make_db(tmp_path / "u.db", [("nvidia/super-a", "token_factory", "super", 5, 5, 1.0, 0.0, None, "live", True)])
    monkeypatch.setattr(rp.subprocess, "call", lambda *a, **k: pytest.fail("--skip-checks runs no subprocess"))
    rc = rp.main(["--skip-checks", "--usage-db", str(db), "--sites", "ref_cafe"])
    out = capsys.readouterr().out
    assert rc == 0 and "heading: Nemotron on Nebius Token Factory (measured)" in out
    assert "NEBIUS_API_KEY=yes" in out
    md = rp.PROOF_MD.read_text(encoding="utf-8")
    for v in KEY_VALUES:
        assert v not in out and v not in md


# ------------------------------------------------------------------------------------------- resumable steps
def test_archive_usage(tmp_path):
    db = tmp_path / "usage.db"
    assert rp.archive_usage(db) is None
    db.write_bytes(b"x")
    a1 = rp.archive_usage(db, stamp="20261101-100000")
    db.write_bytes(b"y")
    a2 = rp.archive_usage(db, stamp="20261101-100000")
    assert not db.exists() and a1.name == "usage.20261101-100000.db" and a2 != a1 and a2.read_bytes() == b"y"


def test_pipeline_resumes_and_skips_completed_sites(tmp_path, monkeypatch, capsys):
    _write_cfg(tmp_path, monkeypatch, make_config())
    monkeypatch.setenv("TAVILY_API_KEY", "t-789")
    monkeypatch.setenv("NEBIUS_API_KEY", "k-456")
    monkeypatch.setattr(rp, "run_verify", lambda *a, **k: 0)
    calls = []

    def call(cmd, **kw):
        calls.append(cmd[2:])
        assert kw["env"]["CLEARANCE_LLM_MODE"] == "live" and kw["env"]["CLEARANCE_TAVILY_MODE"] == "live"
        return 1 if cmd[2:] == ["--sites", "ref_storeroom"] and len(calls) == 2 else 0
    monkeypatch.setattr(rp.subprocess, "call", call)
    db = tmp_path / "usage.db"
    db.write_bytes(b"")
    args = ["--sites", "ref_cafe,ref_storeroom", "--fresh-usage", "--usage-db", str(db)]
    assert rp.main(args) == 5  # storeroom failed
    assert not db.exists() and len(list(tmp_path.glob("usage.*.db"))) == 1
    calls.clear()
    assert rp.main(args) == 0  # resume: no second archive, cafe skipped
    assert calls == [["--sites", "ref_storeroom"], ["--export-only", "--web", "--sites", "ref_cafe", "ref_storeroom"]]
    assert len(list(tmp_path.glob("usage.*.db"))) == 1
    calls.clear()
    assert rp.main(["--sites", "ref_cafe", "--usage-db", str(db)]) == 0  # completed run is not repeated
    assert calls == []
    capsys.readouterr()


# ------------------------------------------------------------------------------------------- presentation rules
def test_provider_column_only_when_every_row_is_token_factory(tmp_path):
    cfg = make_config()
    nebius = make_db(tmp_path / "n.db", [("nvidia/nano-a", "token_factory", "nano", 10, 5, 1.0, 0.0, None, "live", True)])
    md = rp.write_proof(nebius, [], config=cfg)
    assert "| model_id | provider | calls |" in md and "| nvidia/nano-a | token_factory | 1 |" in md
    assert "All calls were served by Nebius Token Factory." in md
    mixed = make_db(tmp_path / "m.db", [
        ("nvidia/nano-a", "token_factory", "nano", 10, 5, 1.0, 0.0, None, "live", True),
        ("nvidia/nano-a", None, "nano", 10, 5, 1.0, 0.0, None, "live", True),
        ("x/unlisted", ALT, "nano", 10, 5, 1.0, 0.0, None, "live", True)])
    md = rp.write_proof(mixed, [], config=cfg)
    assert "| model_id | calls |" in md and "| provider |" not in md and "token_factory" not in md
    assert "| other |" not in md and "| unknown |" not in md and "served by" not in md


def test_model_name_from_row_or_response_without_private_prefix(tmp_path):
    cfg = make_config()
    db = make_db(tmp_path / "u.db", [("private/nvidia/super-x", ALT, "super", 10, 5, 1.0, 0.0, None, "live", True)])
    rows = rp.load_usage_rows(db)
    assert rp.display_model(rows[0], ALT, cfg) == "nvidia/super-x"
    rows[0]["meta"]["response_model"] = "private/nvidia/super-y"
    assert rp.display_model(rows[0], ALT, cfg) == "nvidia/super-y"
    md = rp.write_proof(db, [], config=cfg)
    assert "| nvidia/super-x |" in md and "private/" not in md and "model outside" not in md
    # a model name carrying the name of an endpoint outside the public providers falls back to the tracked id
    named = {**rows[0], "model_id": f"{ALT}/m", "meta": {"role": "super"}}
    assert rp.display_model(named, ALT, cfg) == "nvidia/super-a"


def test_cost_not_metered_when_price_unknown(tmp_path):
    cfg = make_config()
    db = make_db(tmp_path / "u.db", [
        ("nvidia/nano-a", "token_factory", "nano", 100, 50, 1.0, 0.0, None, "live", True),
        ("nvidia/super-a", "token_factory", "super", 1000, 500, 2.0, 0.0003, None, "live", True)])
    md = rp.write_proof(db, [], config=cfg)
    assert "| nvidia/nano-a | token_factory | 1 | 0 | 100 | 50 | 1.000 | not metered |" in md
    assert "0.0003 (price VERIFY)" in md and "0.0000 (price VERIFY)" not in md
    assert "0.0003 USD for the metered rows" in md
    only = make_db(tmp_path / "o.db", [("nvidia/nano-a", "token_factory", "nano", 100, 50, 1.0, 0.0, None, "live", True)])
    assert "cost not metered." in rp.write_proof(only, [], config=cfg)


def test_writer_metrics_line_from_report_json(tmp_path):
    a = tmp_path / "sample_sites" / "ref_cafe" / "analyses" / "an_ref_cafe_01"
    a.mkdir(parents=True)
    (a / "run.json").write_text(json.dumps({"finished_at": "2026-10-28 03:00:00", "wall_time_s": 10,
                                            "factcheck": {"ok": True, "errors": []}}), encoding="utf-8")
    meta = {"writer": "hybrid", "mode": "live", "sections": {"summary": {"first_pass_errors": 2}, "why|r1": {}},
            "totals": {"sections": 2, "llm_calls": 3, "first_pass_errors": 2, "regenerations": 1,
                       "sentences_removed": 0, "fallbacks": []}}
    (a / "report.json").write_text(json.dumps({"writer": meta}), encoding="utf-8")
    md = rp.write_proof(tmp_path / "none.db", ["ref_cafe"], config=make_config())
    assert ("- Report writer (hybrid, live): 2 narrative sections, 3 model calls; first-pass fact-check errors 2 "
            "(summary 2, why r1 0); regenerations 1; sentences removed 0; template fallbacks: none") in md
    assert "- Fact-check: passed" in md
