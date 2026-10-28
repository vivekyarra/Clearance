"""Submission proof from real runs: one command for the day the Nebius credits arrive (also works before then).

    python scripts/release_proof.py [--sites ref_cafe,ref_storeroom,ref_corridor] [--require-provider token_factory]
                                    [--fresh-usage] [--skip-checks] [--force]

Steps (each one idempotent; a re-run resumes where the last one stopped, state in data/release_proof.state.json):
1. Preflight: provider + model_id each LLM role resolves to (agents.llm.client.resolve_role, live mode), Tavily mode,
   which keys are present (yes/no only). With --require-provider, stop when any role resolves elsewhere.
2. NEBIUS_API_KEY set: scripts/verify_token_factory.py (rewrites config/models.yaml + docs/VERIFIED.md).
3. --fresh-usage: archive data/usage.db to data/usage.<timestamp>.db; the checks start a new one.
4. scripts/run_reference_checks.py per site in live mode (CLEARANCE_LLM_MODE=live, CLEARANCE_TAVILY_MODE=live),
   then once with --export-only --web to refresh the web offline-mode fixtures.
5. docs/PROOF.md from the usage table + each check's run.json.
6. Short summary and the files changed.
--skip-checks runs only steps 1 and 5 (rebuild docs/PROOF.md from the usage already recorded).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Iterable

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from agents.llm.client import ROLES, LLMError, load_models_config, resolve_role  # noqa: E402

DEFAULT_SITES = ("ref_cafe", "ref_storeroom", "ref_corridor")
DEFAULT_USAGE_DB = REPO / "data" / "usage.db"
STATE_FILE = REPO / "data" / "release_proof.state.json"
PROOF_MD = REPO / "docs" / "PROOF.md"
SAMPLE_SITES = REPO / "sample_sites"
CHECKS_DATA = Path(os.environ.get("CLEARANCE_DATA") or (Path.home() / ".cache" / "pg" / "refchecks"))
ANALYSIS_IDS = {"ref_cafe": "an_ref_cafe_01", "ref_storeroom": "an_ref_storeroom_01",
                "ref_corridor": "an_ref_corridor_01"}
# Providers PROOF.md may name. The provider column is printed only when every row is Nebius Token Factory.
PUBLIC_PROVIDERS = ("token_factory", "self_hosted_vllm")
NEBIUS = "token_factory"
HEADING_MEASURED = "Nemotron usage (measured)"
HEADING_NEBIUS = "Nemotron on Nebius Token Factory (measured)"
NEBIUS_LINE = "All calls were served by Nebius Token Factory."


class StepError(RuntimeError):
    pass


# ----------------------------------------------------------------------------------------------- step 1: preflight
def _yes(env, name: str | None) -> str:
    return "yes" if name and env.get(name) else "no"


def preflight(*, require_provider: str | None = None, tavily_mode: str = "live", will_run_checks: bool = True,
              config: dict | None = None, out: Callable[[str], None] = print) -> dict:
    """Print the role -> provider/model resolution and key presence. Never prints a key value.
    Returns {"roles", "keys", "problems", "refused"}; `refused` lists the reasons the run must stop."""
    env = os.environ
    config = config if config is not None else load_models_config()
    roles, problems, refused = {}, [], []
    key_names = {config.get("api_key_env", "NEBIUS_API_KEY"), "NEBIUS_API_KEY", "TAVILY_API_KEY"}
    out("Preflight (live-mode resolution)")
    for role in ROLES:
        if role not in (config.get("roles") or {}):
            continue
        try:
            t = resolve_role(config, role, live=True)
        except LLMError as e:
            roles[role] = {"provider": None, "model_id": None}
            out(f"  {role:<7} unresolved: {e}")
            problems.append(f"role {role}: {e}")
            continue
        roles[role] = {"provider": t["provider"], "model_id": t["model_id"]}
        if t.get("api_key_env"):
            key_names.add(t["api_key_env"])
        out(f"  {role:<7} provider={t['provider']}  model_id={t['model_id']}")
        if t["provider"] != "self_hosted_vllm" and not env.get(t.get("api_key_env") or ""):
            problems.append(f"role {role}: {t.get('api_key_env')} not set (live calls would fail)")
    out(f"  Tavily mode for the checks: {tavily_mode}")
    keys = {k: _yes(env, k) for k in sorted(key_names)}
    out("  keys present: " + ", ".join(f"{k}={v}" for k, v in keys.items()))
    if tavily_mode == "live" and keys.get("TAVILY_API_KEY") != "yes":
        problems.append("TAVILY_API_KEY not set (live web search would fail; --tavily-mode offline uses recorded "
                        "responses)")
    if require_provider:
        off = [f"{r} -> {v['provider'] or 'unresolved'}" for r, v in roles.items() if v["provider"] != require_provider]
        if off:
            refused.append(f"--require-provider {require_provider}: these roles resolve elsewhere: " + "; ".join(off))
    if will_run_checks:
        refused.extend(problems)
    for p in problems:
        out(f"  note: {p}")
    return {"roles": roles, "keys": keys, "problems": problems, "refused": refused}


# ------------------------------------------------------------------------------------------- state (resumable)
def load_state(path: Path | None = None) -> dict:
    path = path or STATE_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict, path: Path | None = None) -> None:
    path = path or STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# ------------------------------------------------------------------------------------- step 2: verify models
def run_verify(python: str, env: dict, changed: list[str]) -> int:
    """scripts/verify_token_factory.py with machine-local settings disabled, so the rewritten config/models.yaml
    holds only the tracked candidates. Keys already in `env` are passed through."""
    cfg = REPO / "config" / "models.yaml"
    backup = cfg.read_bytes() if cfg.exists() else None
    venv = {**env, "CLEARANCE_SKIP_DOTENV": "1", "PYTHONPATH": str(REPO)}
    rc = subprocess.call([python, str(REPO / "scripts" / "verify_token_factory.py")], cwd=REPO, env=venv)
    import yaml
    try:
        written = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        written = {}
    if any(c.get("local") for r in (written.get("roles") or {}).values() for c in (r.get("candidates") or [])):
        if backup is not None:
            cfg.write_bytes(backup)
        raise StepError("verify_token_factory.py wrote machine-local candidates into config/models.yaml; restored")
    if rc in (0, 1):
        changed.extend(["config/models.yaml", "docs/VERIFIED.md"])
    return rc


# -------------------------------------------------------------------------------------- step 3: fresh usage
def archive_usage(db: Path, stamp: str | None = None) -> Path | None:
    """Move the usage DB aside (data/usage.<timestamp>.db); the next logged call creates a new one."""
    if not db.exists():
        return None
    stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
    dst = db.with_name(f"{db.stem}.{stamp}{db.suffix}")
    n = 1
    while dst.exists():
        dst = db.with_name(f"{db.stem}.{stamp}-{n}{db.suffix}")
        n += 1
    os.replace(db, dst)
    for side in ("-journal", "-wal", "-shm"):
        s = Path(str(db) + side)
        if s.exists():
            os.replace(s, Path(str(dst) + side))
    return dst


# ------------------------------------------------------------------------------------ step 4: reference checks
def checks_env(base: dict, usage_db: Path, tavily_mode: str) -> dict:
    return {**base, "CLEARANCE_LLM_MODE": "live", "CLEARANCE_TAVILY_MODE": tavily_mode,
            "CLEARANCE_USAGE_DB": str(usage_db), "PYTHONPATH": str(REPO)}


def run_checks(python: str, sites: list[str], env: dict, state: dict, *, force: bool,
               changed: list[str], out: Callable[[str], None] = print) -> None:
    script = str(REPO / "scripts" / "run_reference_checks.py")
    done = state.setdefault("sites_done", {})
    for site in sites:
        if done.get(site) and not force:
            out(f"  {site}: done at {done[site]} (skipped; --force to re-run)")
            continue
        out(f"  {site}: running live Clearance check ...")
        rc = subprocess.call([python, script, "--sites", site], cwd=REPO, env=env)
        if rc != 0:
            save_state(state)
            raise StepError(f"run_reference_checks.py --sites {site} exited with {rc}; re-run to resume")
        done[site] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_state(state)
        changed.append(f"sample_sites/{site}/analyses/")
    out("  refreshing web offline-mode fixtures ...")
    rc = subprocess.call([python, script, "--export-only", "--web", "--sites", *sites], cwd=REPO, env=env)
    if rc != 0:
        raise StepError(f"run_reference_checks.py --export-only --web exited with {rc}; re-run to resume")
    changed.append("apps/web/src/offline/fixtures/")


# -------------------------------------------------------------------------------------- step 5: PROOF.md
def load_usage_rows(db: Path) -> list[dict]:
    if not Path(db).exists():
        return []
    con = sqlite3.connect(str(db))
    try:
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='usage'").fetchone() is None:
            return []
        cur = con.execute("SELECT ts, kind, model_id, tokens_in, tokens_out, duration_s, cost_usd, analysis_id, meta"
                          " FROM usage ORDER BY id")
        rows = []
        for r in cur.fetchall():
            try:
                meta = json.loads(r[8] or "{}")
            except ValueError:
                meta = {}
            rows.append({"ts": r[0], "kind": r[1], "model_id": r[2], "tokens_in": r[3] or 0,
                         "tokens_out": r[4] or 0, "duration_s": r[5] or 0.0, "cost_usd": r[6] or 0.0,
                         "analysis_id": r[7], "meta": meta if isinstance(meta, dict) else {}})
        return rows
    finally:
        con.close()


def is_llm_row(row: dict) -> bool:
    return row["meta"].get("role") in ROLES


def is_live(row: dict) -> bool:
    return row["meta"].get("mode") != "offline" and not str(row["model_id"]).startswith("offline:")


def _candidates(config: dict) -> list[dict]:
    return [c for r in (config.get("roles") or {}).values() for c in (r.get("candidates") or [])]


def provider_of(row: dict, config: dict) -> str:
    """Provider recorded in the usage row, else the config candidate with that model_id, else 'unknown'."""
    p = row["meta"].get("provider")
    if p:
        return str(p)
    mid = str(row["model_id"]).removeprefix("offline:").lower()
    for c in _candidates(config):
        if str(c.get("model_id") or "").lower() == mid:
            return str(c.get("provider") or NEBIUS)
    return "unknown"


def display_model(row_or_id, provider: str, config: dict) -> str:
    """The model name to print: the name the response reported (meta "response_model") when recorded, else the
    model_id of the usage row, without a leading "private/". A name that carries the name of an endpoint outside
    PUBLIC_PROVIDERS is replaced by the tracked model id of the same role."""
    row = row_or_id if isinstance(row_or_id, dict) else {"model_id": row_or_id, "meta": {}}
    meta = row.get("meta") or {}
    name = str(meta.get("response_model") or row.get("model_id") or "").strip()
    name = name.removeprefix("offline:")
    while name.lower().startswith("private/"):
        name = name[len("private/"):]
    if provider and provider not in PUBLIC_PROVIDERS and provider.lower() in name.lower():
        role = meta.get("role")
        tracked = [c for c in ((config.get("roles") or {}).get(role) or {}).get("candidates") or []
                   if not c.get("local") and c.get("provider") in PUBLIC_PROVIDERS]
        name = str(tracked[0]["model_id"]) if tracked else f"{role or 'model'} (name not recorded)"
    return name


def usage_heading(providers: Iterable[str]) -> tuple[str, str | None]:
    """'Nemotron on Nebius Token Factory (measured)' plus a statement line only when every row is token_factory."""
    ps = list(providers)
    if ps and all(p == NEBIUS for p in ps):
        return HEADING_NEBIUS, NEBIUS_LINE
    return HEADING_MEASURED, None


def model_table(rows: list[dict], config: dict) -> list[dict]:
    """Per (displayed model name, provider): calls, failed, tokens in/out, median latency, cost; live LLM rows only."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        if is_llm_row(r) and is_live(r):
            prov = provider_of(r, config)
            groups.setdefault((display_model(r, prov, config), prov), []).append(r)
    out = []
    for (mid, prov), rs in sorted(groups.items()):
        cost = round(sum(float(r["cost_usd"]) for r in rs), 6)
        unpriced = sum(1 for r in rs if r["meta"].get("cost_known") is False)
        tokens = sum(int(r["tokens_in"]) + int(r["tokens_out"]) for r in rs)
        out.append({"model_id": mid, "provider": prov, "calls": len(rs),
                    "failed": sum(1 for r in rs if r["meta"].get("ok") is False),
                    "tokens_in": sum(int(r["tokens_in"]) for r in rs),
                    "tokens_out": sum(int(r["tokens_out"]) for r in rs),
                    "median_latency_s": round(statistics.median(float(r["duration_s"]) for r in rs), 3),
                    "cost_usd": cost, "unpriced": unpriced,
                    "metered": not (unpriced == len(rs) or (cost == 0 and tokens > 0)),
                    "price_verified": all(r["meta"].get("price_verified") for r in rs)})
    return out


def load_run_record(site: str, analysis_id: str, *, sample_dir: Path | None = None,
                    data_dir: Path | None = None) -> dict | None:
    """The newer of the exported run.json (sample_sites/) and the one under the checks' data folder."""
    sample_dir, data_dir = Path(sample_dir or SAMPLE_SITES), Path(data_dir or CHECKS_DATA)
    best = None
    for p in (sample_dir / site / "analyses" / analysis_id / "run.json", data_dir / "analyses" / analysis_id / "run.json"):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if best is None or str(rec.get("finished_at") or "") > str(best.get("finished_at") or ""):
            best = rec
    return best


def load_writer_metrics(site: str, analysis_id: str, *, sample_dir: Path | None = None,
                        data_dir: Path | None = None) -> dict | None:
    """Report writer metrics: report.json "writer" (sample_sites/) or report_meta.json, newest file first."""
    sample_dir, data_dir = Path(sample_dir or SAMPLE_SITES), Path(data_dir or CHECKS_DATA)
    paths = [sample_dir / site / "analyses" / analysis_id / "report.json",
             sample_dir / site / "analyses" / analysis_id / "report_meta.json",
             data_dir / "analyses" / analysis_id / "report_meta.json"]
    found = []
    for p in paths:
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        meta = doc.get("writer") if p.name == "report.json" else doc
        if isinstance(meta, dict) and meta.get("totals"):
            found.append((p.stat().st_mtime, meta))
    return max(found, key=lambda x: x[0])[1] if found else None


def writer_line(meta: dict | None) -> str | None:
    if not meta:
        return None
    t = meta.get("totals") or {}
    per = ", ".join(f"{k.replace('|', ' ')} {v.get('first_pass_errors') or 0}"
                    for k, v in (meta.get("sections") or {}).items())
    fb = ", ".join(t.get("fallbacks") or []) or "none"
    return (f"- Report writer ({meta.get('writer', 'hybrid')}, {meta.get('mode')}): {t.get('sections', 0)} narrative "
            f"sections, {t.get('llm_calls', 0)} model calls; first-pass fact-check errors {t.get('first_pass_errors', 0)}"
            + (f" ({per})" if per else "") + f"; regenerations {t.get('regenerations', 0)}; sentences removed "
            f"{t.get('sentences_removed', 0)}; template fallbacks: {fb}")


def check_rows(sites: list[str], rows: list[dict], **kw) -> list[dict]:
    out = []
    for site in sites:
        aid = ANALYSIS_IDS.get(site, site)
        rec = load_run_record(site, aid, **kw)
        tiers: dict[str, int] = {}
        for r in rows:
            if r["analysis_id"] == aid and is_llm_row(r):
                key = r["meta"]["role"] + ("" if is_live(r) else " (offline)")
                tiers[key] = tiers.get(key, 0) + 1
        if rec is None:
            out.append({"site": site, "analysis_id": aid, "missing": True, "llm_calls": tiers})
            continue
        fc = rec.get("factcheck") or {}
        inv = rec.get("investigation") or {}
        out.append({"site": site, "analysis_id": aid, "missing": False, "wall_time_s": rec.get("wall_time_s"),
                    "llm_mode": rec.get("llm_mode"), "tavily_mode": rec.get("tavily_mode"),
                    "finished_at": rec.get("finished_at"), "llm_calls": tiers,
                    "experiments": inv.get("experiments_run", 0), "findings": inv.get("findings", 0),
                    "findings_confirmed": inv.get("findings_confirmed", 0),
                    "factcheck": ("not run" if not fc else "passed" if fc.get("ok")
                                  else f"{len(fc.get('errors') or [])} error(s)"),
                    "verdicts": {k: (v or {}).get("label") for k, v in (rec.get("verdicts") or {}).items()},
                    "writer": writer_line(load_writer_metrics(site, aid, **kw))})
    return out


def _fmt_int(n) -> str:
    return f"{int(n):,}"


def build_proof_md(rows: list[dict], config: dict, checks: list[dict], *, usage_db_label: str,
                   generated_at: str | None = None) -> str:
    table = model_table(rows, config)
    heading, line = usage_heading(t["provider"] for t in table for _ in range(t["calls"]))
    offline_n = sum(1 for r in rows if is_llm_row(r) and not is_live(r))
    gen = generated_at or dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    md = ["# Clearance proof of real runs", "",
          f"Generated by `scripts/release_proof.py` on {gen} from the usage table (`{usage_db_label}`) and each "
          "Clearance check's `run.json`. Regenerate with `python scripts/release_proof.py --skip-checks`.", "",
          f"## {heading}", ""]
    if line:
        md += [line, ""]
    if table:
        show_provider = all(t["provider"] == NEBIUS for t in table)
        md += ["| model_id |" + (" provider |" if show_provider else "")
               + " calls | failed | tokens in | tokens out | median latency (s) | cost (USD) |",
               "|---|" + ("---|" if show_provider else "") + "---:|---:|---:|---:|---:|---:|"]
        for t in table:
            if not t["metered"]:
                cost = "not metered"
            else:
                cost = f"{t['cost_usd']:.4f}"
                if t["unpriced"]:
                    cost += f" (lower bound, {t['unpriced']} unpriced)"
                elif not t["price_verified"]:
                    cost += " (list-price estimate)"
            md.append(f"| {t['model_id']} |" + (f" {t['provider']} |" if show_provider else "")
                      + f" {t['calls']} | {t['failed']} | {_fmt_int(t['tokens_in'])} | {_fmt_int(t['tokens_out'])} "
                      f"| {t['median_latency_s']:.3f} | {cost} |")
        tot_calls = sum(t["calls"] for t in table)
        metered = [t for t in table if t["metered"]]
        cost_txt = (f"{sum(t['cost_usd'] for t in metered):.4f} USD" + ("" if len(metered) == len(table) else
                    " for the metered rows") if metered else "cost not metered")
        md += ["", f"Total: {tot_calls} live model calls, {_fmt_int(sum(t['tokens_in'] for t in table))} tokens in, "
               f"{_fmt_int(sum(t['tokens_out'] for t in table))} tokens out, {cost_txt}."]
    else:
        md.append("No live model calls are recorded yet.")
    if offline_n:
        md += ["", f"{offline_n} offline-mode call(s) in the same table are not counted above."]
    md += ["", "Latency is wall time per call as measured by `agents/llm/client.py`; cost uses the prices in "
           "`config/models.yaml`.", "", "## Clearance checks", ""]
    if not checks:
        md.append("No Clearance checks selected.")
    for c in checks:
        md.append(f"### {c['site']} (`{c['analysis_id']}`)")
        md.append("")
        tiers = ", ".join(f"{k} {v}" for k, v in sorted(c["llm_calls"].items())) or "none recorded"
        if c["missing"]:
            md += ["No run record yet (run `python scripts/release_proof.py` without `--skip-checks`).",
                   f"- LLM calls by tier: {tiers}", ""]
            continue
        wall = c["wall_time_s"]
        md += [f"- Run: {c['finished_at']}, LLM mode {c['llm_mode']}, Tavily mode {c['tavily_mode']}",
               f"- Wall time: {wall if wall is not None else 'n/a'} s",
               f"- LLM calls by tier: {tiers}",
               f"- Experiments: {c['experiments']} (findings {c['findings']}, confirmed by probe "
               f"{c['findings_confirmed']})",
               f"- Fact-check: {c['factcheck']}"]
        if c.get("writer"):
            md.append(c["writer"])
        if c["verdicts"]:
            md.append("- Verdicts: " + "; ".join(f"{k}: {v}" for k, v in sorted(c["verdicts"].items())))
        md.append("")
    return "\n".join(md).rstrip() + "\n"


def write_proof(usage_db: Path, sites: list[str], *, config: dict | None = None, path: Path | None = None,
                **kw) -> str:
    path = Path(path or PROOF_MD)
    config = config if config is not None else load_models_config()
    rows = load_usage_rows(usage_db)
    try:
        label = Path(usage_db).resolve().relative_to(REPO).as_posix()
    except ValueError:
        label = Path(usage_db).name
    md = build_proof_md(rows, config, check_rows(sites, rows, **kw), usage_db_label=label)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(md, encoding="utf-8")
    return md


# ------------------------------------------------------------------------------------------------- main
def parse_sites(s: str) -> list[str]:
    sites = [x.strip() for x in s.replace(" ", ",").split(",") if x.strip()]
    bad = [x for x in sites if x not in ANALYSIS_IDS]
    if bad:
        raise argparse.ArgumentTypeError(f"unknown site(s) {bad}; known: {sorted(ANALYSIS_IDS)}")
    return sites


def _stop(reasons: list[str], out) -> int:
    for r in reasons:
        out(f"STOP: {r}")
    return 3


def run_pipeline(a, usage_db: Path, changed: list[str], out) -> int:
    """Steps 2-4. A proof run stays `in_progress` in the state file until every site is done, so a re-run resumes
    (no second archive, completed sites skipped). A completed proof run is not repeated without --force or
    --fresh-usage."""
    state = load_state()
    if not state.get("in_progress"):
        if state.get("completed_at") and not (a.force or a.fresh_usage):
            out(f"== 2-4. proof run already completed at {state['completed_at']} (skipped; --force or --fresh-usage "
                "to run again)")
            return 0
        state = {"in_progress": True, "started_at": time.strftime("%Y-%m-%d %H:%M:%S"), "sites_done": {},
                 "fresh_usage": bool(a.fresh_usage)}
        new_run = True
    else:
        out(f"  resuming the proof run started at {state.get('started_at')}")
        new_run = False
        if a.force:
            state["sites_done"] = {}
    save_state(state)

    out("== 2. verify Token Factory model IDs")
    if os.environ.get("NEBIUS_API_KEY"):
        if state.get("verified_at") and not a.force:
            out(f"  already verified at {state['verified_at']} in this proof run (skipped)")
        else:
            try:
                rc = run_verify(a.python, dict(os.environ), changed)
            except StepError as e:
                return _stop([str(e)], out)
            out(f"  verify_token_factory.py exit code {rc}")
            if rc not in (0, 1):
                return 4
            state["verified_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            save_state(state)
            pf = preflight(require_provider=a.require_provider, tavily_mode=a.tavily_mode, out=out)
            if pf["refused"]:
                return _stop(pf["refused"], out)
    else:
        out("  NEBIUS_API_KEY not set (skipped)")

    out("== 3. usage table")
    if a.fresh_usage and new_run:
        arch = archive_usage(usage_db)
        out(f"  archived to {_rel(arch)}" if arch else "  no usage DB yet; a new one starts with the first call")
        if arch:
            changed.append(_rel(arch))
    else:
        out("  keeping the current usage DB" + (" (archived when this proof run started)" if a.fresh_usage else ""))

    out("== 4. live Clearance checks")
    try:
        run_checks(a.python, a.sites, checks_env(dict(os.environ), usage_db, a.tavily_mode), state,
                   force=False, changed=changed, out=out)
    except StepError as e:
        out(f"STOP: {e}")
        return 5
    state.update(in_progress=False, completed_at=time.strftime("%Y-%m-%d %H:%M:%S"), sites=a.sites)
    save_state(state)
    return 0


def _rel(p: Path | None) -> str:
    if p is None:
        return ""
    try:
        return Path(p).resolve().relative_to(REPO).as_posix()
    except ValueError:
        return str(p)


def main(argv=None, *, out: Callable[[str], None] = print) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sites", type=parse_sites, default=list(DEFAULT_SITES), help="comma-separated reference sites")
    ap.add_argument("--require-provider", default=None, help="stop unless every LLM role resolves to this provider")
    ap.add_argument("--fresh-usage", action="store_true", help="archive the usage DB and start a new one")
    ap.add_argument("--skip-checks", action="store_true", help="only rebuild docs/PROOF.md from recorded usage")
    ap.add_argument("--force", action="store_true", help="re-run sites already completed in this proof run")
    ap.add_argument("--tavily-mode", choices=("live", "offline"), default="live")
    ap.add_argument("--usage-db", default=str(DEFAULT_USAGE_DB))
    ap.add_argument("--python", default=sys.executable)
    a = ap.parse_args(argv)
    usage_db = Path(a.usage_db)
    changed: list[str] = []

    out("== 1. preflight")
    pf = preflight(require_provider=a.require_provider, tavily_mode=a.tavily_mode, will_run_checks=not a.skip_checks,
                   out=out)
    if pf["refused"]:
        return _stop(pf["refused"], out)

    if not a.skip_checks:
        rc = run_pipeline(a, usage_db, changed, out)
        if rc:
            return rc

    out("== 5. docs/PROOF.md")
    md = write_proof(usage_db, a.sites)
    changed.append("docs/PROOF.md")
    heading = next((ln[3:] for ln in md.splitlines() if ln.startswith("## ")), "")
    out(f"  heading: {heading}")

    out("== 6. summary")
    rows = load_usage_rows(usage_db)
    live = [r for r in rows if is_llm_row(r) and is_live(r)]
    out(f"  live model calls recorded: {len(live)}; checks: {', '.join(a.sites)}")
    out("  files changed: " + ", ".join(dict.fromkeys(changed)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
