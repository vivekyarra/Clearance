"""Layout optimizer (PLAN 7.8, job J7): find small furniture moves that raise robot success.

Pipeline
  1. Peaks: top-3 bottleneck (Pinch Map) cells, non-maximum suppressed (>= 0.5 m apart).
  2. Candidate objects: `hint_object_ids` from the investigation loop first (if movable), then
     movable primitives within 1.5 m of a peak, nearest first.
  3. Candidate moves per object (candidates.generate_moves): 5 cm lattice up to 0.30 m x rotations
     {0, +-15 deg}, pruned to <= 40 admissible moves (see candidates.py for the pruning rules),
     checked against constraints.ConstraintChecker.
  4. Screen: every move with `screen_episodes` (30) at normal density, nominal geometry, all sharing
     one seed_base with the baseline (common random numbers -> paired comparison).
  5. Confirm: top `top_k_confirm` (5) screened moves (at most 2 per object) re-run with
     `confirm_episodes` (200) on a fresh seed_base, independent of the screen, so the selection
     step's optimism does not leak into the reported numbers.
  6. Pairs: best confirmed move of each of the top `top_k_combo` (3) objects; every jointly
     admissible pair runs `confirm_episodes` on the confirm seed_base.
  7. Verdict bound: the best change set and the as-is layout are re-run with `confirm_episodes`
     at the same density on the `verdict_bound` (conservative) geometry; the best entry carries
     "before_conservative" / "after_conservative" for clearance_core/verdict.py.
  8. Output: entries with uplift > 0, sorted by uplift; `significant` = the 95% CIs do not
     overlap (after.ci95 low > before.ci95 high), otherwise status "not significant".

All evaluations go through `runner` (default jobs.sim.api.run_batch), optionally on an `executor`
(any concurrent.futures.Executor; runner and arguments must be picklable for process pools).
Results are collected in submission order, so output is identical with or without an executor.
"""
from __future__ import annotations

import itertools
import math
from concurrent.futures import Executor
from typing import Callable

import numpy as np

from clearance_core.schemas import BatchResults, RobotSpec, SimBatch, Task, Twin

from .candidates import bottleneck_peaks, candidate_objects, describe_move, generate_moves
from .constraints import ConstraintChecker, Rules

# Mirrors the `optimizer` section of config/sim_defaults.yaml.
DEFAULT_BUDGET = {
    "screen_episodes": 30,
    "confirm_episodes": 200,
    "top_k_confirm": 5,
    "top_k_combo": 3,
    "max_moves_per_object": 40,
    "max_confirm_per_object": 2,
    "max_objects": None,        # cap on candidate objects (None = all within radius)
    "max_evaluations": None,    # hard cap on runner calls (None = no cap)
    "max_episodes": None,       # hard cap on total simulated episodes (None = no cap)
    "n_bottlenecks": 3,
    "peak_min_sep_m": 0.5,
    "radius_m": 1.5,
    "step_m": 0.05,
    "max_shift_m": 0.30,
    "rotations_deg": (0.0, 15.0, -15.0),
    "density": "normal",
    "bound": "nominal",
    "verdict_confirm": True,    # re-run best change set + as-is on verdict_bound
    "verdict_bound": "conservative",
}


def _default_runner():
    from jobs.sim.api import run_batch
    return run_batch


def _default_rasterizer():
    try:
        from jobs.sim.api import rasterize_twin
        return rasterize_twin
    except ImportError:
        from .geometry import fallback_rasterize
        return fallback_rasterize


def _merge_budget(budget: dict | None) -> dict:
    b = dict(DEFAULT_BUDGET)
    unknown = set(budget or {}) - set(b)
    if unknown:
        raise ValueError(f"unknown budget keys: {sorted(unknown)}")
    b.update(budget or {})
    for k in ("screen_episodes", "confirm_episodes"):
        if int(b[k]) < 1:
            raise ValueError(f"budget[{k!r}] must be >= 1")
    return b


def _plan_counts(b: dict, n_moves: int) -> tuple[int, int, int, bool]:
    """Fit (screen moves, top_k_confirm, top_k_combo, verdict_confirm) inside max_evaluations /
    max_episodes. Fixed part = 2 baseline runs + confirms + pairs (+2 verdict-bound runs); the
    screen gets what is left. When the caps are tight the verdict-bound runs go first, then the
    pair stage shrinks, then the confirm stage."""
    se, ce = int(b["screen_episodes"]), int(b["confirm_episodes"])
    kc = max(int(b["top_k_confirm"]), 0)
    kp = min(max(int(b["top_k_combo"]), 0), kc)
    vc = bool(b["verdict_confirm"])
    max_calls = b["max_evaluations"] if b["max_evaluations"] is not None else math.inf
    max_eps = b["max_episodes"] if b["max_episodes"] is not None else math.inf

    def room(kc_, kp_, vc_):
        fixed = 1 + kc_ + kp_ * (kp_ - 1) // 2 + (2 if vc_ else 0)
        return min(max_calls - 1 - fixed, (max_eps - se - ce * fixed) // se)

    if kc < 1:
        return 0, 0, 0, False
    while room(kc, kp, vc) < 1 and (vc or kp > 1 or kc > 1):
        if vc:
            vc = False
        elif kp > 1:
            kp -= 1
        else:
            kc -= 1
            kp = min(kp, kc)
    n = int(max(min(room(kc, kp, vc), n_moves), 0))
    return n, kc, kp, vc


def _round_robin(per_obj: list[tuple[str, list[dict]]]) -> list[tuple[str, dict]]:
    """Interleave objects so a truncated budget still screens every object (hints first)."""
    out = []
    for i in range(max((len(m) for _, m in per_obj), default=0)):
        for oid, moves in per_obj:
            if i < len(moves):
                out.append((oid, moves[i]))
    return out


def _overrides(moves: dict[str, tuple[float, float, float]]) -> dict:
    return {"move": {oid: {"dx": round(dx, 4), "dy": round(dy, 4), "dyaw": round(dyaw, 6)}
                     for oid, (dx, dy, dyaw) in sorted(moves.items())}}


def _results(sb: SimBatch) -> BatchResults:
    if sb.results is None:
        raise RuntimeError(f"runner returned batch {sb.batch_id} without results")
    return sb.results


def _pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def _move_tuple(m: dict) -> tuple[float, float, float]:
    return m["dx"], m["dy"], m["dyaw"]


def search_layout(twin: Twin, robot: RobotSpec, task: Task, bottleneck: np.ndarray, *, budget: dict | None = None,
                  seed: int = 0, hint_object_ids: list[str] | None = None, runner: Callable | None = None,
                  rasterizer: Callable | None = None, executor: Executor | None = None,
                  rules: dict | Rules | None = None, out_prefix: str | None = None) -> dict:
    """Full optimizer run. Returns {"results": [...], "peaks", "candidates", "skipped_hints",
    "egress", "baseline", "stats", "budget"}; `results` is what optimize_layout returns."""
    b = _merge_budget(budget)
    runner = runner or _default_runner()
    rasterizer = rasterizer or _default_rasterizer()
    rules = rules if isinstance(rules, Rules) else Rules.from_dict(rules)
    density, bound = b["density"], b["bound"]
    stats = {"runner_calls": 0, "episodes": 0, "screened": 0, "confirmed": 0, "pairs": 0, "pairs_rejected": 0,
             "verdict_runs": 0}

    def run_all(jobs: list[tuple[dict | None, int, int, bool, str]]) -> list[SimBatch]:
        """jobs: (overrides, n_episodes, seed_base, record_episodes, bound)."""
        stats["runner_calls"] += len(jobs)
        stats["episodes"] += sum(j[1] for j in jobs)
        calls = [((twin, robot, task, density, bd, n, sb),
                  dict(overrides=ov, record_episodes=rec, out_prefix=out_prefix if rec else None))
                 for ov, n, sb, rec, bd in jobs]
        if executor is None:
            return [runner(*a, **kw) for a, kw in calls]
        futs = [executor.submit(runner, *a, **kw) for a, kw in calls]
        return [f.result() for f in futs]

    checker = ConstraintChecker(twin, robot, rasterizer, rules, bound=bound)
    peaks = bottleneck_peaks(bottleneck, checker.grid, k=int(b["n_bottlenecks"]), min_sep_m=b["peak_min_sep_m"])
    cands, skipped = candidate_objects(twin, peaks, b["radius_m"], hint_object_ids)
    if b["max_objects"] is not None:
        cands = cands[: int(b["max_objects"])]
    per_obj = []
    for c in cands:
        moves, rejected = generate_moves(checker, c["id"], peaks, step_m=b["step_m"], max_shift_m=b["max_shift_m"],
                                         rotations_deg=b["rotations_deg"], max_moves=int(b["max_moves_per_object"]),
                                         radius_m=b["radius_m"])
        c.update(n_moves=len(moves), rejected=rejected)
        per_obj.append((c["id"], moves))
    out = {"results": [], "budget": b, "stats": stats, "candidates": cands, "skipped_hints": skipped,
           "peaks": [{"xy": [p.x, p.y], "value": p.value, "cell": [p.row, p.col]} for p in peaks],
           "egress": {"baseline": checker.egress_baseline, "min_gap_m": rules.egress_min_gap_m}, "baseline": {}}

    queue = _round_robin(per_obj)
    n_screen, k_confirm, k_combo, verdict_confirm = _plan_counts(b, len(queue))
    queue = queue[:n_screen]
    if not queue:
        return out

    seed_screen = 1000 + 1_000_000 * int(seed)
    seed_confirm = seed_screen + 500_000
    se, ce = int(b["screen_episodes"]), int(b["confirm_episodes"])
    rec = out_prefix is not None

    # ---- stage 1: screen ----
    jobs = [(None, se, seed_screen, False, bound)]
    jobs += [(_overrides({oid: _move_tuple(m)}), se, seed_screen, False, bound) for oid, m in queue]
    batches = run_all(jobs)
    base_s = batches[0]
    out["baseline"]["screen"] = {"batch_id": base_s.batch_id, "results": _results(base_s)}
    stats["screened"] = len(queue)
    screened = [{"oid": oid, "move": m, "batch": sb, "res": _results(sb), "i": i}
                for i, ((oid, m), sb) in enumerate(zip(queue, batches[1:]))]
    base_sr = _results(base_s).success_rate
    if base_sr >= 1.0:
        # nothing can beat 100%: a confirm stage would only turn sampling noise of the fresh
        # seeds into a "change" with an uplift that is not real
        out["baseline"]["note"] = "baseline already at 100% success; no change recommended"
        return out
    screened.sort(key=lambda s: (-s["res"].success_rate, s["res"].near_miss_per_mission,
                                 math.hypot(s["move"]["dx"], s["move"]["dy"]), abs(s["move"]["dyaw"]), s["i"]))
    picked, per = [], {}
    for s in screened:
        if len(picked) >= k_confirm or s["res"].success_rate < base_sr:
            break
        if per.get(s["oid"], 0) >= int(b["max_confirm_per_object"]):
            continue
        per[s["oid"]] = per.get(s["oid"], 0) + 1
        picked.append(s)
    if not picked:
        return out

    # ---- stage 2: confirm ----
    jobs = [(None, ce, seed_confirm, rec, bound)]
    jobs += [(_overrides({s["oid"]: _move_tuple(s["move"])}), ce, seed_confirm, rec, bound) for s in picked]
    batches = run_all(jobs)
    base_c = batches[0]
    before = _results(base_c)
    out["baseline"]["confirm"] = {"batch_id": base_c.batch_id, "results": before}
    stats["confirmed"] = len(picked)
    entries = [{"moves": {s["oid"]: _move_tuple(s["move"])}, "batch": sb, "res": _results(sb),
                "screen_id": s["batch"].batch_id} for s, sb in zip(picked, batches[1:])]

    # ---- stage 3: pairs of the best confirmed move of the top objects ----
    best_per_obj = {}
    for e in sorted(entries, key=lambda e: -e["res"].success_rate):
        oid = next(iter(e["moves"]))
        if e["res"].success_rate > before.success_rate and oid not in best_per_obj:
            best_per_obj[oid] = e
    top = list(best_per_obj.values())[:k_combo]
    pair_moves = []
    for e1, e2 in itertools.combinations(top, 2):
        mv = {**e1["moves"], **e2["moves"]}
        if checker.check(mv).ok:
            pair_moves.append(mv)
        else:
            stats["pairs_rejected"] += 1
    if pair_moves:
        batches = run_all([(_overrides(mv), ce, seed_confirm, rec, bound) for mv in pair_moves])
        stats["pairs"] = len(pair_moves)
        entries += [{"moves": mv, "batch": sb, "res": _results(sb), "screen_id": None}
                    for mv, sb in zip(pair_moves, batches)]

    # ---- output ----
    results = []
    for e in entries:
        after = e["res"]
        uplift = after.success_rate - before.success_rate
        if uplift <= 0:
            continue
        sig = after.ci95[0] > before.ci95[1]
        desc = "; ".join(describe_move(checker, oid, *mv) for oid, mv in sorted(e["moves"].items()))
        desc = desc[:1] + desc[1:].replace("; Move", "; move").replace("; Rotate", "; rotate")
        summary = (f"{desc}: success {_pct(before.success_rate)} → {_pct(after.success_rate)} "
                   f"(n={after.n_episodes or ce}, {density} traffic)" + ("" if sig else " [not significant]"))
        ids = [base_c.batch_id, e["batch"].batch_id] + ([e["screen_id"]] if e["screen_id"] else [])
        results.append({
            "change": _overrides(e["moves"]),
            "description": desc,
            "summary": summary,
            "before": before,
            "after": after,
            "batch_ids": ids,
            "uplift": round(uplift, 6),
            "significant": bool(sig),
            "status": "significant" if sig else "not significant",
            "objects": sorted(e["moves"]),
            "bound": bound,
            "density": density,
        })
    results.sort(key=lambda r: (-r["uplift"], not r["significant"], len(r["objects"]), r["description"]))

    # ---- stage 4: best change set on the verdict bound (conservative) ----
    if results and verdict_confirm:
        vb = b["verdict_bound"]
        best = results[0]
        base_v, after_v = run_all([(None, ce, seed_confirm, rec, vb), (best["change"], ce, seed_confirm, rec, vb)])
        stats["verdict_runs"] = 2
        out["baseline"][vb] = {"batch_id": base_v.batch_id, "results": _results(base_v)}
        best[f"before_{vb}"] = _results(base_v)
        best[f"after_{vb}"] = _results(after_v)
        best["verdict_batch_ids"] = [base_v.batch_id, after_v.batch_id]
    out["results"] = results
    return out


def optimize_layout(twin: Twin, robot: RobotSpec, task: Task, bottleneck: np.ndarray, *, budget: dict | None = None,
                    seed: int = 0, hint_object_ids: list[str] | None = None, runner: Callable | None = None,
                    rasterizer: Callable | None = None, executor: Executor | None = None,
                    rules: dict | Rules | None = None, out_prefix: str | None = None) -> list[dict]:
    """Contract entry point (docs/CONTRACTS.md). Returns, sorted by uplift (best first):
    [{"change": overrides, "description", "summary", "before": BatchResults, "after": BatchResults,
      "batch_ids": [before, after, screen?], "uplift", "significant", "status", "objects", "bound", "density"}]
    The first entry also carries "before_conservative", "after_conservative" (BatchResults) and
    "verdict_batch_ids" when budget["verdict_confirm"] is on (default)."""
    return search_layout(twin, robot, task, bottleneck, budget=budget, seed=seed, hint_object_ids=hint_object_ids,
                         runner=runner, rasterizer=rasterizer, executor=executor, rules=rules,
                         out_prefix=out_prefix)["results"]
