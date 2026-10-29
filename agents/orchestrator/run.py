"""Analysis orchestrator (docs/CONTRACTS.md run_analysis).

Stages: candidates -> feasibility -> batches -> investigation -> optimizer -> verdicts -> report.
analyses/<id>/analysis.json is rewritten after every stage (and after every finished batch).

Code decides everything numeric: candidates come from static filters, sims and the optimizer produce the numbers,
verdicts come only from clearance_core.verdict.decide_verdict. Nemotron Super orders and explains the candidates via
tool calls (list_candidates, run_feasibility, tavily_search, tavily_extract) and drives the investigation loop; it
never writes numbers or verdicts. Modules built in parallel (optimizer, report, factcheck) are imported lazily; a
missing one marks its stage "skipped: <reason>"."""
from __future__ import annotations

import copy
import importlib
import json
import re
import time
import traceback
from pathlib import Path
from typing import Callable

import numpy as np
import yaml

from clearance_core import storage
from clearance_core.schemas import (Fact, Feasibility, Investigation, LegFeasibility, Primitive, RobotSpec,
                                    SimBatch, Task, Twin)

from agents.investigate.tools import SimAPI
from agents.orchestrator import tavily_tools
from agents.orchestrator.candidates import static_filter
from agents.orchestrator.dispatch import inline_dispatcher, make_spec, process_pool_dispatcher

STAGES = ["candidates", "feasibility", "batches", "investigation", "optimizer", "verdicts", "report"]
REPO = Path(__file__).resolve().parents[2]
DENSITIES = ("none", "quiet", "normal", "peak")
BOUNDS = ("optimistic", "nominal", "conservative")


def sim_defaults() -> dict:
    try:
        return yaml.safe_load((REPO / "config" / "sim_defaults.yaml").read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def normalize_budget(budget: dict | None) -> tuple[dict, list[str]]:
    """Budget = robots (<= 6) x densities x bounds x episodes, plus loop/optimizer caps."""
    cfg = sim_defaults()
    base = {"max_robots": 6, "densities": ["quiet", "normal", "peak"], "bounds": list(BOUNDS), "episodes": 100,
            **(cfg.get("analysis_budget") or {})}
    inv = {"max_steps": 8, "max_experiments": 6, "probe_episodes": 30}
    icfg = cfg.get("investigation") or {}
    inv.update({"max_steps": icfg.get("max_steps", 8), "max_experiments": icfg.get("max_experiments_per_robot", 6),
                "probe_episodes": icfg.get("probe_episodes", 30)})
    b = {**base, "seed_base": 1000, "max_total_episodes": None, "investigate_max_robots": 2,
         "optimizer_max_robots": 2, "confirm_episodes": None, "llm_rounds": 6, "max_workers": None,
         "optimizer": None, "investigation": inv, "include_awaiting_review": False, **(budget or {})}
    b["investigation"] = {**inv, **((budget or {}).get("investigation") or {})}
    notes = []
    b["max_robots"] = max(1, min(int(b["max_robots"]), 6))
    b["densities"] = [d for d in dict.fromkeys(b["densities"]) if d in DENSITIES]
    b["bounds"] = [x for x in dict.fromkeys(b["bounds"]) if x in BOUNDS]
    vcfg = cfg.get("verdict") or {}
    vd, vb = vcfg.get("density", "normal"), vcfg.get("bound", "conservative")
    if vd not in b["densities"]:
        b["densities"].append(vd)
        notes.append(f"added density '{vd}': the verdict rule needs it")
    if vb not in b["bounds"]:
        b["bounds"].append(vb)
        notes.append(f"added bound '{vb}': the verdict rule needs it")
    b["include_awaiting_review"] = bool(b["include_awaiting_review"])
    b["episodes"] = max(1, int(b["episodes"]))
    b["confirm_episodes"] = int(b["confirm_episodes"] or b["episodes"])
    b["investigation"]["max_experiments"] = min(6, int(b["investigation"]["max_experiments"]))
    b["investigation"]["probe_episodes"] = min(30, int(b["investigation"]["probe_episodes"]))
    return b, notes


def _lazy(module: str, attr: str):
    try:
        return getattr(importlib.import_module(module), attr), None
    except Exception as e:
        return None, f"{module}.{attr} not available ({type(e).__name__}: {str(e)[:120]})"


MODULES = {
    "optimize_layout": ("jobs.optimizer.search", "optimize_layout"),
    "build_facts": ("agents.report.facts", "build_facts"),
    "build_citations": ("agents.report.facts", "build_citations"),
    "write_report": ("agents.report.writer", "write_report"),
    "check_report": ("agents.factcheck.check", "check_report"),
    "compute_roi": ("agents.report.roi", "compute_roi"),
    "decide_verdict": ("clearance_core.verdict", "decide_verdict"),
    "run_investigation": ("agents.investigate.loop", "run_investigation"),
}


def _dump(x):
    if hasattr(x, "model_dump"):
        return x.model_dump(mode="json", by_alias=True)
    if isinstance(x, dict):
        return {k: _dump(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_dump(v) for v in x]
    if isinstance(x, (np.floating, np.integer)):
        return x.item()
    return x


def apply_overrides(twin: Twin, overrides: dict | None) -> Twin:
    """Twin copy with object moves/removals/additions applied (for feasibility of a layout change)."""
    t = twin.model_copy(deep=True)
    ov = overrides or {}
    rem = set(ov.get("remove") or [])
    prims = []
    for p in t.primitives:
        if p.id in rem:
            continue
        mv = (ov.get("move") or {}).get(p.id)
        if mv:
            x, y, z = p.pose.xyz
            p = p.model_copy(update={"pose": p.pose.model_copy(update={
                "xyz": (x + float(mv.get("dx", 0)), y + float(mv.get("dy", 0)), z),
                "yaw": p.pose.yaw + float(mv.get("dyaw", 0))})})
        prims.append(p)
    prims += [Primitive.model_validate(b) for b in ov.get("add_boxes") or []]
    t.primitives = prims
    return t


ORDER_SCHEMA = {"type": "object", "properties": {
    "order": {"type": "array", "items": {"type": "string"}},
    "explanations": {"type": "object", "additionalProperties": {"type": "string"}}}, "required": ["order"]}

ORDER_PROMPT = """You are the Clearance orchestrator. Code has already filtered robots with static checks. Order the \
candidate robots for simulation (most promising first) and explain each in one sentence. Use list_candidates first; \
you may call run_feasibility, tavily_search and tavily_extract. Do not write any numbers in explanations (numbers \
come only from tools and are added by code). Reply with JSON {"order": [robot_id, ...], "explanations": \
{robot_id: text}}."""


def _tool(name, desc, props, required=()):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": list(required)}}}


ORCH_TOOL_SCHEMAS = {
    "list_candidates": _tool("list_candidates", "Candidates and exclusions from the code static filters.", {}),
    "run_feasibility": _tool("run_feasibility", "Static feasibility per task leg for one robot.",
                             {"robot_id": {"type": "string"}, "bound": {"type": "string", "enum": list(BOUNDS)}},
                             ["robot_id"]),
    "tavily_search": _tool("tavily_search", "Web search (vendor pages, deployment reviews, safety standards).",
                           {"query": {"type": "string"}, "max_results": {"type": "integer"}}, ["query"]),
    "tavily_extract": _tool("tavily_extract", "Extract page text from URLs.",
                            {"urls": {"type": "array", "items": {"type": "string"}}}, ["urls"]),
    "run_batch": _tool("run_batch", "Monte Carlo batch for one robot (budget-checked).",
                       {"robot_id": {"type": "string"}, "density": {"type": "string"}, "bound": {"type": "string"}},
                       ["robot_id", "density", "bound"]),
    "run_optimizer": _tool("run_optimizer", "Layout optimizer for one robot.", {"robot_id": {"type": "string"}},
                           ["robot_id"]),
    "compute_roi": _tool("compute_roi", "Payback from the user's business inputs.", {"robot_id": {"type": "string"}},
                         ["robot_id"]),
    "investigate": _tool("investigate", "Run the investigation loop for one robot.", {"robot_id": {"type": "string"}},
                         ["robot_id"]),
}
ORDERING_TOOLS = ["list_candidates", "run_feasibility", "tavily_search", "tavily_extract"]


def offline_order_policy(role, messages, tools, schema):
    """Deterministic orchestrator policy (offline mode): list candidates, keep the code ranking."""
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    if not tool_msgs and tools:
        return {"content": "", "tool_calls": [{"name": "list_candidates", "arguments": {}}]}
    ids = []
    for m in tool_msgs:
        try:
            data = json.loads(m.get("content") or "{}")
        except json.JSONDecodeError:
            continue
        if "candidates" in data:
            ids = [c["robot_id"] for c in data["candidates"]]
    return json.dumps({"order": ids, "explanations": {i: "Passed the static checks; order follows the code ranking "
                                                         "by gap margin." for i in ids}})


class Orchestrator:
    def __init__(self, analysis_id: str, twin: Twin, robots: list[RobotSpec], task: Task, business: dict,
                 budget: dict | None, *, dispatcher: Callable | None = None, llm=None, sim: SimAPI | None = None,
                 investigate_fn: Callable | None = None, progress: Callable[[dict], None] | None = None,
                 modules: dict[str, Callable | None] | None = None):
        self.aid = analysis_id
        self.modules = dict(modules or {})
        if investigate_fn is not None:
            self.modules["run_investigation"] = investigate_fn
        self.twin = twin if isinstance(twin, Twin) else Twin.model_validate(twin)
        self.robots = [r if isinstance(r, RobotSpec) else RobotSpec.model_validate(r) for r in robots]
        self.robot_by_id = {r.robot_id: r for r in self.robots}
        self.task = task if isinstance(task, Task) else Task.model_validate(task)
        self.business = dict(business or {})
        self.budget, budget_notes = normalize_budget(budget)
        self.injected_sim = sim is not None
        self.sim = sim or SimAPI.load()
        if dispatcher is None and self.sim.run_batch is not None:
            dispatcher = (inline_dispatcher(self.sim.run_batch) if self.injected_sim
                          else process_pool_dispatcher(self.budget["max_workers"]))
        self.dispatcher = dispatcher
        self.llm = llm
        self.progress = progress
        self.batches: dict[str, SimBatch] = {}
        self.investigations: dict[str, Investigation] = {}
        self.inv_facts: list[Fact] = []
        self.feas: dict[str, dict[str, Feasibility]] = {}
        vcfg = sim_defaults().get("verdict") or {}
        self.success_min = float(vcfg.get("success_min", 0.90))
        self.verdict_density, self.verdict_bound = vcfg.get("density", "normal"), vcfg.get("bound", "conservative")
        self.cands: list[dict] = []
        self.key = f"analyses/{analysis_id}/analysis.json"
        self.doc: dict = {
            "analysis_id": analysis_id, "status": "running", "started_at": time.time(),
            "stages": {s: {"state": "pending", "progress": 0.0} for s in STAGES},
            "inputs": {"site_id": self.twin.site_id, "twin_id": self.twin.twin_id, "twin_version": self.twin.version,
                       "robot_ids": [r.robot_id for r in self.robots], "task": self.task.model_dump(mode="json"),
                       "business": self.business, "budget": self.budget,
                       "include_awaiting_review": self.budget["include_awaiting_review"]},
            "twin": self.twin.model_dump(mode="json", by_alias=True),
            "notes": budget_notes, "candidates": [], "candidate_details": [], "robots": {}, "excluded": [],
            "candidate_explanations": {}, "feasibility": {}, "feasibility_by_bound": {}, "batches": [],
            "batch_index": {}, "skipped_batches": [], "investigation_id": None, "investigation_ids": {},
            "investigations": {}, "optimizer": {}, "optimizer_hints": {}, "verdicts": [], "confirmation_batches": {},
            "roi": {}, "citations": [], "tool_calls": [], "report": None, "sim_source": self.sim.source}

    def _fn(self, name: str):
        """Module entry point: injected override (None = disabled) or lazy import."""
        if name in self.modules:
            f = self.modules[name]
            return (f, None) if f is not None else (None, f"{name} disabled for this run")
        return _lazy(*MODULES[name])

    # ---------------------------------------------------------------- bookkeeping
    def save(self):
        storage.write_json(self.key, self.doc)
        if self.progress:
            self.progress(copy.deepcopy(self.doc))

    def set_stage(self, name, state, progress=None, detail=None):
        st = self.doc["stages"][name]
        st["state"] = state
        if progress is not None:
            st["progress"] = round(float(progress), 4)
        if detail is not None:
            st["detail"] = detail
        self.save()

    def _trail(self, caller, tool, args, result_summary):
        self.doc["tool_calls"].append({"caller": caller, "tool": tool, "args": args, "result": result_summary,
                                       "t": round(time.time(), 3)})

    def _set_candidates(self, cands: list[dict]):
        self.cands = list(cands)
        self.doc["candidates"] = [c["robot_id"] for c in self.cands]
        self.doc["candidate_details"] = self.cands
        self.doc["robots"] = {c["robot_id"]: self.robot_by_id[c["robot_id"]].model_dump(mode="json", by_alias=True)
                              for c in self.cands}

    # ---------------------------------------------------------------- tools (shared by code and Super)
    def tool_list_candidates(self):
        return {"candidates": self.cands, "excluded": self.doc["excluded"]}

    def tool_run_feasibility(self, robot_id: str, bound: str = "nominal"):
        if robot_id not in self.robot_by_id:
            return {"ok": False, "error": f"unknown robot_id {robot_id!r}"}
        if bound not in BOUNDS:
            return {"ok": False, "error": f"bound must be one of {BOUNDS}"}
        f = self._feasibility(self.robot_by_id[robot_id], bound)
        if f is None:
            return {"ok": False, "error": f"check_feasibility unavailable ({self.sim.source})"}
        return {"ok": True, "feasibility": f.model_dump(mode="json")}

    def tool_tavily_search(self, query: str, max_results: int = 5):
        r = tavily_tools.tavily_search(query, max_results, meta={"analysis_id": self.aid})
        for x in r.get("results", []):
            self.doc["citations"].append({"used_for": f"market check: {query}", **x})
        return r

    def tool_tavily_extract(self, urls: list):
        r = tavily_tools.tavily_extract(urls, meta={"analysis_id": self.aid})
        for x in r.get("results", []):
            self.doc["citations"].append({"used_for": "market check", "url": x.get("url"),
                                          "retrieved_at": x.get("retrieved_at")})
        return {**r, "results": [{**x, "text": x.get("text", "")[:1500]} for x in r.get("results", [])]}

    def _feasibility(self, robot: RobotSpec, bound: str, twin: Twin | None = None) -> Feasibility | None:
        if self.sim.check_feasibility is None:
            return None
        if twin is None and bound in self.feas.get(robot.robot_id, {}):
            return self.feas[robot.robot_id][bound]
        f = self.sim.check_feasibility(twin or self.twin, robot, self.task, bound)
        f = f if isinstance(f, Feasibility) else Feasibility.model_validate(f)
        if twin is None:
            self.feas.setdefault(robot.robot_id, {})[bound] = f
            self.doc["feasibility_by_bound"].setdefault(robot.robot_id, {})[bound] = f.model_dump(mode="json")
            if bound == self.verdict_bound:
                self.doc["feasibility"][robot.robot_id] = f.model_dump(mode="json")
        return f

    def call_tool(self, name: str, args: dict, caller: str) -> dict:
        fn = getattr(self, f"tool_{name}", None)
        if fn is None:
            out = {"ok": False, "error": f"unknown tool {name!r}"}
        else:
            try:
                out = fn(**(args or {}))
            except TypeError as e:
                out = {"ok": False, "error": f"bad arguments: {e}"}
            except Exception as e:
                out = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
        self._trail(caller, name, args, {k: v for k, v in out.items() if k in ("ok", "error", "mode")})
        return out

    # ---------------------------------------------------------------- stages
    def _grid(self):
        if getattr(self, "_grid_cache", None) is not None:
            return self._grid_cache
        self._grid_cache = self._grid_uncached()
        return self._grid_cache

    def _grid_uncached(self):
        if self.sim.rasterize_twin is not None:
            try:
                return self.sim.rasterize_twin(self.twin, "nominal")
            except Exception as e:
                self.doc["notes"].append(f"rasterize_twin failed: {type(e).__name__}: {e}")
        try:
            from clearance_core import grid as cgrid
            return cgrid.load(self.twin.occupancy)
        except Exception:
            return None

    def stage_candidates(self):
        res = static_filter(self.twin, self.robots, self.task, self.business, grid=self._grid(),
                            max_robots=self.budget["max_robots"],
                            include_awaiting_review=self.budget["include_awaiting_review"])
        pending = [c["robot_id"] for c in res["candidates"] if c.get("spec_status") == "awaiting review"]
        if pending:
            res["notes"].append("simulated with catalog specs awaiting review (explicit option): " + ", ".join(pending))
        self._set_candidates(res["candidates"])
        self.doc["excluded"] = res["excluded"]
        self.doc["route_gaps"] = res["route_gaps"]
        self.doc["notes"] += res["notes"]
        self.save()
        if not res["candidates"]:
            return "done", "no robot passed the static checks"
        order, expl, how = self._llm_order(res["candidates"])
        by = {c["robot_id"]: c for c in res["candidates"]}
        self._set_candidates([by[i] for i in order])
        self.doc["candidate_explanations"] = expl
        return "done", f"{len(order)} candidate(s); ordering by {how}"

    def _get_llm(self):
        if self.llm is not None:
            return self.llm
        from agents.llm.client import LLM, OfflineLLM, get_llm
        import os
        mode = (os.environ.get("CLEARANCE_LLM_MODE") or ("live" if os.environ.get("NEBIUS_API_KEY")
                                                          else "offline")).lower()
        self.llm = get_llm("live") if mode == "live" else LLM("offline", offline=OfflineLLM(
            policy=offline_order_policy))
        return self.llm

    def _llm_order(self, cands: list[dict]):
        from agents.llm.client import LLMError
        code_order = [c["robot_id"] for c in cands]
        llm = self._get_llm()
        tools = [ORCH_TOOL_SCHEMAS[n] for n in ORDERING_TOOLS]
        summary = {"site_id": self.twin.site_id, "twin_id": self.twin.twin_id, "task": self.task.sequence,
                   "n_objects": len(self.twin.primitives), "traffic_source": self.twin.traffic.source,
                   "candidate_ids": code_order, "business_keys": sorted(self.business)}
        messages = [{"role": "system", "content": ORDER_PROMPT}, {"role": "user", "content": json.dumps(summary)}]
        meta = {"analysis_id": self.aid, "stage": "candidates"}
        parsed = None
        try:
            for _ in range(int(self.budget["llm_rounds"])):
                resp = llm.chat("super", messages, tools=tools, response_format=ORDER_SCHEMA, kind="orchestrate",
                                meta=meta, temperature=0.2)
                if resp.tool_calls:
                    messages.append(resp.assistant_message())
                    for tc in resp.tool_calls:
                        allowed = tc.name in ORDERING_TOOLS
                        out = (self.call_tool(tc.name, tc.arguments, "super") if allowed
                               else {"ok": False, "error": f"{tc.name} is not available while ordering candidates"})
                        messages.append({"role": "tool", "tool_call_id": tc.id,
                                         "content": json.dumps(out, default=str)[:12000]})
                    continue
                parsed = resp.parsed
                break
        except LLMError as e:
            self.doc["notes"].append(f"candidate ordering by Super failed ({type(e).__name__}); code order kept")
        if not isinstance(parsed, dict):
            return code_order, {}, "code ranking"
        order = [i for i in dict.fromkeys(parsed.get("order") or []) if i in code_order]
        if sorted(order) != sorted(code_order):
            self.doc["notes"].append("Super's order was not a permutation of the candidates; code order kept")
            order, how = code_order, "code ranking"
        else:
            how = "Super" if getattr(llm, "mode", "") == "live" else "offline deterministic policy"
        expl = {}
        for rid, text in (parsed.get("explanations") or {}).items():
            if rid not in code_order or not isinstance(text, str):
                continue
            r = self.robot_by_id[rid]
            scrub = text
            for tok in (rid, r.vendor, r.model):
                scrub = scrub.replace(tok, "")
            if re.search(r"\d", scrub):
                self.doc["notes"].append(f"explanation for {rid} dropped: it contained numbers")
                continue
            expl[rid] = text.strip()[:400]
        return order, expl, how

    def stage_feasibility(self):
        if self.sim.check_feasibility is None:
            return f"skipped: jobs.sim.api.check_feasibility not available ({self.sim.source})", None
        cands = self.cands
        bounds = list(dict.fromkeys(self.budget["bounds"] + ["conservative"]))
        n, k = len(cands) * len(bounds), 0
        for c in cands:
            for b in bounds:
                self.call_tool("run_feasibility", {"robot_id": c["robot_id"], "bound": b}, "code")
                k += 1
                self.doc["stages"]["feasibility"]["progress"] = round(k / max(1, n), 4)
            self.save()
        return "done", None

    def _plan_batches(self) -> list[tuple[str, str, str]]:
        plan = []
        for c in self.cands:
            rid = c["robot_id"]
            for d in self.budget["densities"]:
                for b in self.budget["bounds"]:
                    f = self.feas.get(rid, {}).get(b)
                    if f is not None and f.all_reachable is False:
                        self.doc["skipped_batches"].append({"robot_id": rid, "density": d, "bound": b,
                                                            "reason": "static feasibility: a task leg has no path"})
                        continue
                    plan.append((rid, d, b))
        return plan

    def _batch_spec(self, robot: RobotSpec, density, bound, n, overrides=None, out_prefix=None):
        return make_spec(self.twin, robot, self.task, density, bound, n, self.budget["seed_base"],
                         overrides=overrides, record_episodes=True,
                         out_prefix=out_prefix or f"analyses/{self.aid}/batches/")

    def _register_batch(self, b: SimBatch, kind="sim.batch"):
        self.batches[b.batch_id] = b
        key = f"analyses/{self.aid}/batches/{b.batch_id}.json"
        if not (storage.DATA / key).exists():
            storage.write_json(key, b)
        try:
            from agents.llm.usage import log_usage
            log_usage(kind, "sim", 0, 0, float(b.wall_time_s or 0.0), 0.0,
                      {"analysis_id": self.aid, "robot_id": b.robot_id, "batch_id": b.batch_id,
                       "n_episodes": b.n_episodes, "density": b.density, "bound": b.geometry_bound})
        except Exception:
            pass

    def stage_batches(self):
        if self.dispatcher is None:
            return f"skipped: no batch dispatcher (jobs.sim.api.run_batch not available: {self.sim.source})", None
        plan = self._plan_batches()
        if not plan:
            return "done", "no batch to run"
        n = self.budget["episodes"]
        cap = self.budget.get("max_total_episodes")
        if cap and n * len(plan) > int(cap):
            n = max(1, int(cap) // len(plan))
            self.doc["notes"].append(f"episodes per batch reduced to fit max_total_episodes")
        self.doc["episodes_per_batch"] = n
        specs = [self._batch_spec(self.robot_by_id[r], d, b, n) for r, d, b in plan]
        done = [0]

        def on_result(i, b: SimBatch):
            self._register_batch(b)
            self.doc["batches"].append(b.batch_id)
            self.doc["batch_index"][b.batch_id] = {"robot_id": b.robot_id, "density": b.density,
                                                   "bound": b.geometry_bound, "n_episodes": b.n_episodes,
                                                   "results": _dump(b.results)}
            self._trail("code", "run_batch", {"robot_id": b.robot_id, "density": b.density,
                                              "bound": b.geometry_bound}, {"ok": True, "batch_id": b.batch_id})
            done[0] += 1
            self.doc["stages"]["batches"]["progress"] = round(done[0] / len(specs), 4)
            self.save()

        out = self.dispatcher(specs, on_result)
        for b in out:  # dispatchers that ignore on_result
            b = b if isinstance(b, SimBatch) else SimBatch.model_validate(b)
            if b.batch_id not in self.batches:
                on_result(0, b)
        return "done", f"{len(out)} batch(es), {n} episodes each"

    def _batch_for(self, rid, density, bound) -> SimBatch | None:
        for b in self.batches.values():
            if b.robot_id == rid and b.density == density and b.geometry_bound == bound and b.results is not None:
                return b
        return None

    def _sr(self, rid, density="normal", bound="nominal"):
        b = self._batch_for(rid, density, bound)
        return None if b is None else float(b.results.success_rate)

    def stage_investigation(self):
        fn, why = self._fn("run_investigation")
        if fn is None:
            return f"skipped: {why}", None
        if not self.batches:
            return "skipped: no batch results to investigate", None
        targets = []
        for c in self.cands:
            rid = c["robot_id"]
            sr = self._sr(rid)
            if sr is None:
                srs = [b.results.success_rate for b in self.batches.values() if b.robot_id == rid and b.results]
                sr = min(srs) if srs else None
            if sr is not None and sr < self.success_min:
                targets.append(rid)
        targets = targets[: int(self.budget["investigate_max_robots"])]
        if not targets:
            return "done", "every simulated robot met the success threshold; nothing to investigate"
        icfg = self.budget["investigation"]
        cand_robots = [self.robot_by_id[c["robot_id"]] for c in self.cands]
        for k, rid in enumerate(targets):
            robot = self.robot_by_id[rid]
            res = fn(self.aid, self.twin, robot, self.task, [b for b in self.batches.values() if b.robot_id == rid],
                     max_steps=icfg["max_steps"], max_experiments=icfg["max_experiments"],
                     probe_episodes=icfg["probe_episodes"], other_robots=[r for r in cand_robots if r is not robot],
                     llm=self.llm if getattr(self.llm, "mode", None) == "live" else None, sim=self.sim,
                     out_key=f"analyses/{self.aid}/investigations/{rid}.json")
            inv = getattr(res, "investigation", res)
            self.investigations[rid] = inv
            self.inv_facts += list(getattr(res, "facts", []) or [])
            self.doc["investigation_ids"][rid] = inv.investigation_id
            src = storage.DATA / f"analyses/{self.aid}/investigations/{rid}.json"
            self.doc["investigations"][rid] = (json.loads(src.read_text(encoding="utf-8")) if src.exists()
                                               else inv.model_dump(mode="json"))
            self._trail("code", "investigate", {"robot_id": rid}, {"ok": True, "investigation_id":
                                                                   inv.investigation_id, "mode": inv.mode})
            if k == 0:
                self.doc["investigation_id"] = inv.investigation_id
                storage.write_json(f"analyses/{self.aid}/investigation.json", self.doc["investigations"][rid])
            self.doc["stages"]["investigation"]["progress"] = round((k + 1) / len(targets), 4)
            self.save()
        return "done", f"investigated {', '.join(targets)}"

    def _bottleneck(self, rid) -> np.ndarray | None:
        agg = None
        bs = [b for b in self.batches.values() if b.robot_id == rid and b.bottleneck_uri]
        nominal = [b for b in bs if b.geometry_bound == "nominal"] or bs
        for b in nominal:
            p = Path(b.bottleneck_uri)
            p = p if p.is_absolute() else storage.DATA / b.bottleneck_uri
            try:
                a = np.load(p).astype(np.float64)
            except Exception:
                continue
            agg = a if agg is None else (agg + a if a.shape == agg.shape else agg)
        if agg is None or not np.any(agg > 0):
            agg = self._static_pinch(rid, agg)
        return agg

    def _blocked_legs(self, rid) -> list[LegFeasibility]:
        """Legs with no path on the nominal bound for geometric reasons (not a threshold above the limit)."""
        f = self.feas.get(rid, {}).get("nominal") or self.feas.get(rid, {}).get(self.verdict_bound)
        return [l for l in (f.legs if f is not None else []) if l.reachable is False and not l.blocking_threshold
                and l.narrowest_xy is not None]

    def _static_pinch(self, rid, like: np.ndarray | None) -> np.ndarray | None:
        """Pinch Map seed for a robot that static feasibility stops before any episode moves: the narrowest point
        of every blocked leg, so the optimizer can search moves around it."""
        legs = self._blocked_legs(rid)
        grid = self._grid() if legs else None
        if grid is None:
            return like
        out = np.zeros(grid.data.shape, np.float64) if like is None or like.shape != grid.data.shape else like.copy()
        for l in legs:
            r, c = (int(v) for v in grid.world_to_cell(*l.narrowest_xy))
            if 0 <= r < out.shape[0] and 0 <= c < out.shape[1]:
                out[r, c] += 1.0
        self.doc.setdefault("pinch_seed", {})[rid] = [{"from": l.from_wp, "to": l.to_wp, "xy": list(l.narrowest_xy),
                                                       "gap_m": l.narrowest_gap_m} for l in legs]
        return out

    def tool_run_optimizer(self, robot_id: str):
        opt, why = self._fn("optimize_layout")
        if opt is None:
            return {"ok": False, "error": why}
        bn = self._bottleneck(robot_id)
        if bn is None:
            return {"ok": False, "error": "no Pinch Map for this robot"}
        inv = self.investigations.get(robot_id)
        hints = []
        if inv is not None:
            for f in sorted(inv.findings, key=lambda f: not f.confirmed_by_probe):
                hints += [o for o in f.object_ids if o not in hints]
        shared = getattr(self, "_opt_executor", None)
        ex = shared if shared is not None else self._make_opt_executor(opt)
        kw = {"executor": ex} if ex is not None else {}
        try:
            res = opt(self.twin, self.robot_by_id[robot_id], self.task, bn, budget=self.budget.get("optimizer"),
                      seed=self.budget["seed_base"], hint_object_ids=hints or None, **kw)
        finally:
            if ex is not None and ex is not shared:
                ex.shutdown()
        res = _dump(res)
        self.doc["optimizer"][robot_id] = res
        self.doc["optimizer_hints"][robot_id] = hints
        return {"ok": True, "n_changes": len(res)}

    def _make_opt_executor(self, opt):
        """Process pool for the optimizer's sim runs (real sim only; injected test doubles run inline)."""
        workers = int(self.budget.get("max_workers") or 1)
        if workers <= 1 or self.injected_sim or "optimize_layout" in self.modules:
            return None
        import inspect
        if "executor" not in inspect.signature(opt).parameters:
            return None
        from concurrent.futures import ProcessPoolExecutor
        return ProcessPoolExecutor(max_workers=workers)

    def stage_optimizer(self):
        _, why = self._fn("optimize_layout")
        if why:
            return f"skipped: {why}", None
        targets = [rid for rid in [c["robot_id"] for c in self.cands]
                   if rid in self.investigations or (self._sr(rid) is not None and self._sr(rid) < self.success_min)
                   or (self._sr(rid) is None and self._blocked_legs(rid))]
        targets = targets[: int(self.budget["optimizer_max_robots"])]
        if not targets:
            return "done", "no robot needed layout changes"
        msgs = []
        opt, _ = self._fn("optimize_layout")
        self._grid()  # cache before any worker thread needs it
        self._opt_executor = self._make_opt_executor(opt) if len(targets) > 1 else None
        try:
            if self._opt_executor is not None:
                # robots run concurrently and share one process pool, so small confirm stages overlap
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=len(targets)) as tp:
                    outs = list(tp.map(lambda r: self.call_tool("run_optimizer", {"robot_id": r}, "code"), targets))
            else:
                outs = []
                for k, rid in enumerate(targets):
                    outs.append(self.call_tool("run_optimizer", {"robot_id": rid}, "code"))
                    self.doc["stages"]["optimizer"]["progress"] = round((k + 1) / len(targets), 4)
                    self.save()
        finally:
            if self._opt_executor is not None:
                self._opt_executor.shutdown()
            self._opt_executor = None
        for rid, out in zip(targets, outs):
            if not out.get("ok"):
                msgs.append(f"{rid}: {out.get('error')}")
        self.doc["optimizer"] = {rid: self.doc["optimizer"][rid] for rid in targets if rid in self.doc["optimizer"]}
        self.doc["stages"]["optimizer"]["progress"] = 1.0
        self.save()
        return "done", "; ".join(msgs) or None

    # ---------------------------------------------------------------- verdicts (code only)
    def _top_change(self, rid) -> dict | None:
        res = self.doc["optimizer"].get(rid) or []

        def gain(r):
            try:
                return float(r["after"]["success_rate"]) - float(r["before"]["success_rate"])
            except Exception:
                return float("-inf")
        res = [r for r in res if r.get("change")]
        return max(res, key=gain) if res else None

    def _flagged_on_route(self, legs) -> list[str]:
        ids = [leg.blocking_threshold for leg in legs if leg.reachable is None and leg.blocking_threshold]
        return list(dict.fromkeys(ids))

    def _verdict_inputs(self, rid, facts_by_ref: dict | None = None):
        from clearance_core.verdict import VerdictInputs
        vcfg = sim_defaults().get("verdict") or {}
        vd, vb = vcfg.get("density", "normal"), vcfg.get("bound", "conservative")
        f = self.feas.get(rid, {}).get(vb)
        legs = list(f.legs) if f is not None else []
        b = self._batch_for(rid, vd, vb)
        fid = {}
        if facts_by_ref is not None:
            if b is not None:
                fid["success"] = [x.fact_id for x in facts_by_ref.get(b.batch_id, [])
                                  if "success" in x.label.lower() and "ci" not in x.label.lower()][:1]
            fid["legs"] = [x.fact_id for ref, xs in facts_by_ref.items() if rid in ref and "feasib" in ref.lower()
                           for x in xs][:6]
        as_is = VerdictInputs(legs=legs, flagged_thresholds=self._flagged_on_route(legs),
                              success_rate=None if b is None else float(b.results.success_rate), fact_ids=fid)
        wc = None
        conf = self.doc["confirmation_batches"].get(rid)
        if conf:
            cb = self.batches.get(conf["batch_id"])
            wlegs = [LegFeasibility.model_validate(x) for x in conf["legs"]] if conf.get("legs") else legs
            wfid = {}
            if facts_by_ref is not None and cb is not None:
                wfid["success"] = [x.fact_id for x in facts_by_ref.get(cb.batch_id, [])
                                   if "success" in x.label.lower() and "ci" not in x.label.lower()][:1]
            wc = VerdictInputs(legs=wlegs, flagged_thresholds=self._flagged_on_route(wlegs),
                               success_rate=None if cb is None or cb.results is None else float(cb.results.success_rate),
                               fact_ids=wfid, changes=[{"overrides": conf["change"], "description":
                                                        conf.get("description", ""), "batch_id": conf["batch_id"]}])
        return as_is, wc

    def stage_verdicts(self):
        decide, why = self._fn("decide_verdict")
        if decide is None:
            return f"skipped: {why}", None
        vcfg = sim_defaults().get("verdict") or {}
        vd, vb = vcfg.get("density", "normal"), vcfg.get("bound", "conservative")
        pending_confirm = []
        for c in self.cands:
            rid = c["robot_id"]
            top = self._top_change(rid)
            if top is None or self.dispatcher is None or rid in self.doc["confirmation_batches"]:
                continue
            spec = self._batch_spec(self.robot_by_id[rid], vd, vb, self.budget["confirm_episodes"],
                                    overrides=top["change"], out_prefix=f"analyses/{self.aid}/batches/")
            pending_confirm.append((rid, top, spec))
        # one dispatch, so the confirmation batches of all robots run in parallel
        done = self.dispatcher([t[2] for t in pending_confirm]) if pending_confirm else []
        for (rid, top, _), cb in zip(pending_confirm, done):
            robot = self.robot_by_id[rid]
            cb = cb if isinstance(cb, SimBatch) else SimBatch.model_validate(cb)
            self._register_batch(cb, kind="sim.confirm")
            fw = None
            if self.sim.check_feasibility is not None:
                fw = self._feasibility(robot, vb, twin=apply_overrides(self.twin, top["change"]))
            self.doc["confirmation_batches"][rid] = {
                "batch_id": cb.batch_id, "change": top["change"], "description": top.get("description", ""),
                "results": _dump(cb.results), "legs": [l.model_dump(mode="json") for l in fw.legs] if fw else None}
            self._trail("code", "run_batch", {"robot_id": rid, "density": vd, "bound": vb, "overrides": top["change"]},
                        {"ok": True, "batch_id": cb.batch_id, "purpose": "confirm top optimizer change set"})
        self._compute_verdicts(decide)
        return "done", None

    def _compute_verdicts(self, decide, facts_by_ref=None):
        verdicts = []
        for c in self.cands:
            as_is, wc = self._verdict_inputs(c["robot_id"], facts_by_ref)
            verdicts.append(decide(c["robot_id"], as_is, wc))
        self.verdicts = verdicts
        self.doc["verdicts"] = [v.model_dump(mode="json") for v in verdicts]

    # ---------------------------------------------------------------- report
    def tool_compute_roi(self, robot_id: str):
        roi, why = self._fn("compute_roi")
        if roi is None:
            return {"ok": False, "error": why}
        sr = self._sr(robot_id)
        f = self.feas.get(robot_id, {}).get("nominal")
        if sr is None or f is None or not f.legs:
            return {"ok": False, "error": "needs nominal/normal success and nominal feasibility"}
        frac = sum(1 for leg in f.legs if leg.reachable) / len(f.legs)
        out = roi(self.business.get("roi_inputs", self.business), sr, frac)
        self.doc["roi"][robot_id] = _dump(out)
        return {"ok": True}

    def _citations(self) -> list[dict]:
        cites = []
        for c in self.cands:
            r = self.robot_by_id[c["robot_id"]]
            for field, src in r.fields_sources.items():
                cites.append({"robot_id": r.robot_id, "field": field, "url": src.url, "quote": src.quote,
                              "retrieved_at": src.retrieved_at})
        build, _ = self._fn("build_citations")
        if build is not None:
            return build(self.doc)
        return cites + [s for s in self.doc["citations"] if s.get("url")]

    def stage_report(self):
        build_facts, why_f = self._fn("build_facts")
        roi_fn, _ = self._fn("compute_roi")
        if build_facts is None and roi_fn is not None and self.business:
            for c in self.cands:
                self.call_tool("compute_roi", {"robot_id": c["robot_id"]}, "code")
        facts: list[Fact] = []
        if build_facts is not None:
            facts = [x if isinstance(x, Fact) else Fact.model_validate(x) for x in build_facts(self.doc)]
        have = {f.fact_id for f in facts}
        facts += [f for f in self.inv_facts if f.fact_id not in have]
        storage.write_json(f"analyses/{self.aid}/facts.json", [f.model_dump(mode="json") for f in facts])
        citations = self._citations()
        storage.write_json(f"analyses/{self.aid}/citations.json", citations)
        decide, _ = self._fn("decide_verdict")
        if decide is not None and self.doc["verdicts"]:
            by_ref: dict[str, list[Fact]] = {}
            for f in facts:
                by_ref.setdefault(f.origin.ref, []).append(f)
            self._compute_verdicts(decide, by_ref)  # same labels, now with fact ids
            self.save()
        if build_facts is None:
            return f"skipped: {why_f} (investigation facts written)", None
        write_report, why_w = self._fn("write_report")
        if write_report is None:
            return f"skipped: {why_w} (facts written)", None
        findings = [f for inv in self.investigations.values() for f in inv.findings]
        primary = self.investigations.get(next(iter(self.doc["investigation_ids"]), ""), None)
        verdicts = getattr(self, "verdicts", [])
        md = write_report(facts, citations, findings, verdicts, primary)
        report = {"md_key": f"analyses/{self.aid}/report.md", "facts_key": f"analyses/{self.aid}/facts.json",
                  "citations_key": f"analyses/{self.aid}/citations.json", "factcheck": None}
        check, why_c = self._fn("check_report")
        if check is not None:
            res = check(md, facts)
            report["factcheck"] = {"ok": bool(res.get("ok")), "errors": res.get("errors", [])}
            if not res.get("ok"):
                md = res.get("clean_md", md)
        else:
            report["factcheck"] = f"skipped: {why_c}"
        storage.path(report["md_key"]).write_text(md, encoding="utf-8")
        self.doc["report"] = report
        return "done", None

    # ---------------------------------------------------------------- driver
    def run(self) -> dict:
        self.save()
        critical = {"candidates", "feasibility", "batches"}
        for name in STAGES:
            self.set_stage(name, "running")
            try:
                state, detail = getattr(self, f"stage_{name}")()
            except Exception as e:
                state, detail = f"failed: {type(e).__name__}: {str(e)[:200]}", traceback.format_exc(limit=4)
            prog = 1.0 if state == "done" else self.doc["stages"][name].get("progress", 0.0)
            self.set_stage(name, state, prog, detail)
            if state.startswith("failed") and name in critical:
                for rest in STAGES[STAGES.index(name) + 1:]:
                    self.doc["stages"][rest]["state"] = f"skipped: stage '{name}' failed"
                self.doc["status"] = "failed"
                break
        else:
            self.doc["status"] = "done"
        self.doc["finished_at"] = time.time()
        self.save()
        return self.doc


def load_catalog(directory: Path | None = None) -> list[RobotSpec]:
    out = []
    for p in sorted((directory or REPO / "catalog" / "robots").glob("*.json")):
        try:
            out.append(RobotSpec.model_validate(json.loads(p.read_text(encoding="utf-8"))))
        except Exception:
            continue
    return out


def run_analysis(analysis_id, twin, robots: list[RobotSpec], task, business: dict, *, budget: dict,
                 dispatcher: Callable | None = None, llm=None, sim: SimAPI | None = None,
                 investigate_fn: Callable | None = None, progress: Callable[[dict], None] | None = None,
                 modules: dict[str, Callable | None] | None = None, include_awaiting_review: bool | None = None) -> dict:
    """Contract entry point. `dispatcher(specs, on_result)` lets services/api run batches on Nebius Serverless Jobs;
    default = local ProcessPool over jobs.sim.api.run_batch (inline when a sim is injected). With no robots given,
    reviewed specs are read from catalog/robots/*.json. include_awaiting_review=True (or the budget key of the same
    name) also simulates robots whose catalog specs await human review; the report marks them "awaiting review"."""
    if include_awaiting_review is not None:
        budget = {**(budget or {}), "include_awaiting_review": bool(include_awaiting_review)}
    if not robots:
        robots = load_catalog()
    return Orchestrator(analysis_id, twin, robots, task, business, budget, dispatcher=dispatcher, llm=llm, sim=sim,
                        investigate_fn=investigate_fn, progress=progress, modules=modules).run()
