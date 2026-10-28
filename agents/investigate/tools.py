"""Tools for the Nemotron investigation loop (PLAN 7.12, v1.2). Every number returned comes from code and is
registered as a Fact (origin.type="investigation"); the model may cite only those fact IDs.

Each counterfactual probe is recorded as an Experiment (hypothesis -> change -> result -> outcome -> next action).
Sim functions (jobs/sim/api.py) are injected through `SimAPI`, so tests can pass test doubles."""
from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from clearance_core import storage
from clearance_core.grid import Grid
from clearance_core.schemas import (Experiment, Fact, FactOrigin, Finding, Primitive, RobotSpec, SimBatch, Task,
                                    Twin)

DENSITIES = ("none", "quiet", "normal", "peak")
EVENT_TYPES = ("stop", "near_miss", "collision", "fail", "success", "replan")


@dataclass
class SimAPI:
    rasterize_twin: Callable | None = None
    run_batch: Callable | None = None
    check_feasibility: Callable | None = None
    source: str = "injected"

    @classmethod
    def load(cls) -> "SimAPI":
        """Real jobs/sim/api.py if importable; otherwise an empty API (sim-backed tools then return errors)."""
        try:
            from jobs.sim import api  # type: ignore
        except Exception as e:
            return cls(source=f"unavailable: {type(e).__name__}: {e}")
        return cls(getattr(api, "rasterize_twin", None), getattr(api, "run_batch", None),
                   getattr(api, "check_feasibility", None), source="jobs.sim.api")


# ---------------------------------------------------------------- geometry helpers

def primitive_polygon(p: Primitive):
    from shapely.geometry import Point, Polygon
    x, y = p.pose.xyz[0], p.pose.xyz[1]
    if p.type == "cylinder":
        return Point(x, y).buffer(p.size[0] / 2.0, quad_segs=16)
    l, w = p.size[0], p.size[1]
    c, s = math.cos(p.pose.yaw), math.sin(p.pose.yaw)
    pts = [(x + c * dx - s * dy, y + s * dx + c * dy) for dx, dy in
           ((l / 2, w / 2), (-l / 2, w / 2), (-l / 2, -w / 2), (l / 2, -w / 2))]
    return Polygon(pts)


def robot_width_m(robot: RobotSpec) -> float | None:
    if not robot.footprint_polygon_m:
        return None
    ys = [p[1] for p in robot.footprint_polygon_m]
    return float(max(ys) - min(ys))


def _load_npy(uri: str) -> np.ndarray:
    p = Path(uri)
    return np.load(p if p.is_absolute() else storage.DATA / uri)


def _load_episodes(uri: str) -> list[dict]:
    p = Path(uri)
    p = p if p.is_absolute() else storage.DATA / uri
    out = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


# ---------------------------------------------------------------- citation / number checks for model-written text

TAG = re.compile(r"\[(f_[A-Za-z0-9_]+)\]")
# A number starts a word (digits glued to a preceding letter/underscore are identifiers such as T7 or sb_0012) and
# may carry a unit suffix ("18cm", "3x"), an exponent ("1e3"), thousands separators ("1,200") or a leading dot.
_NUM = re.compile(r"(?<![\w.])-?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+(?![\w.]))?%?")
_VERDICT_CI = re.compile(r"\b(?:not\s+cleared|cleared\s+with\s+changes|verdicts?)\b", re.I)
_VERDICT_CS = re.compile(r"\b(?:Cleared|CLEARED)\b")
MAX_TEXT_CHARS = 2000


def _strip_ignored(text: str, ignore_tokens) -> str:
    t = TAG.sub(" ", text)
    for tok in sorted((x for x in ignore_tokens if x), key=lambda x: (-len(x), x)):
        t = re.sub(rf"(?<![\w]){re.escape(tok)}(?![\w])", " ", t)
    return t


def _number_matches(tok: str, nums: list[float]) -> bool:
    pct = tok.endswith("%")
    s = tok.rstrip("%").replace(",", "")
    try:
        v = float(s)
    except ValueError:
        return False
    if not math.isfinite(v):
        return False
    mant = re.split(r"[eE]", s)[0]
    dec = len(mant.split(".")[1]) if "." in mant else 0
    tol = 0.0 if mant != s else 0.5 * 10 ** -dec + 1e-9
    cands = [(v / 100.0, tol / 100.0), (v, tol)] if pct else [(v, tol)]
    return any(abs(c - f) <= tl or (f != 0 and abs(c - f) / abs(f) <= 0.01) for f in nums for c, tl in cands)


def unsupported_numbers(text: str, fact_values: list, ignore_tokens: set[str]) -> list[str]:
    """Numeric tokens in `text` that match no cited fact value (rounding tolerance of the written precision,
    or 1% relative; "71%" matches 0.71)."""
    t = _strip_ignored(text, ignore_tokens)
    nums = [float(v) for v in fact_values if isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(float(v))]
    return [m.group(0) for m in _NUM.finditer(t) if not _number_matches(m.group(0), nums)]


def states_verdict(text: str) -> bool:
    """Verdict vocabulary (Cleared / Cleared with changes / Not cleared / verdict): only code sets verdicts."""
    return bool(_VERDICT_CI.search(text) or re.search(r"\bCleared\b|\bCLEARED\b", text))


# ---------------------------------------------------------------- tool schemas (OpenAI function format)

def _fn(name, desc, props, required=()):
    return {"type": "function", "function": {"name": name, "description": desc,
                                             "parameters": {"type": "object", "properties": props,
                                                            "required": list(required)}}}


_XY = {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2, "description": "[x, y] metres"}
TOOL_SCHEMAS = [
    _fn("get_batch_summary", "Results of the Monte Carlo batches for this robot (all, or one batch_id).",
        {"batch_id": {"type": "string"}}),
    _fn("get_bottlenecks", "Pinch Map: cells ranked by accumulated stop/near-miss/blocked time, with nearby object "
                           "IDs and traffic zone IDs.",
        {"top_k": {"type": "integer", "minimum": 1, "maximum": 10}}),
    _fn("get_gap_at", "Free gap through a point on the nominal grid (distance to nearest obstacle) vs robot width.",
        {"xy": _XY}, ["xy"]),
    _fn("get_episode_events", "Count and sample events of one type in a batch's episodes.",
        {"batch_id": {"type": "string"}, "type": {"type": "string", "enum": list(EVENT_TYPES)},
         "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, ["batch_id", "type"]),
    _fn("get_objects_near", "Twin objects (primitives) within r metres of a point.",
        {"xy": _XY, "r": {"type": "number", "minimum": 0.05, "maximum": 5.0}}, ["xy"]),
    _fn("run_probe", "Run one experiment: a small counterfactual batch on nominal geometry. State the hypothesis. "
                     "Change any of: overrides {\"move\": {obj_id: {\"dx\",\"dy\",\"dyaw\"}}, \"remove\": [obj_id]}; "
                     "zone_id + ped_scale (0..1) to reduce pedestrians in one traffic zone; density; robot_id to swap "
                     "robot. previous_conclusion (optional) closes the previous experiment and marks it dig_further.",
        {"hypothesis": {"type": "string"}, "overrides": {"type": "object"},
         "zone_id": {"type": "string"}, "ped_scale": {"type": "number", "minimum": 0, "maximum": 1},
         "density": {"type": "string", "enum": list(DENSITIES)}, "robot_id": {"type": "string"},
         "n_episodes": {"type": "integer", "minimum": 1, "maximum": 30},
         "previous_conclusion": {"type": "string"}}, ["hypothesis"]),
    _fn("record_finding", "Record a conclusion. Cite only fact IDs returned by tools as [f_xxx] in the claim and in "
                          "evidence_fact_ids. suggested_change = {\"object_id\",\"dx\",\"dy\",\"dyaw\"} or "
                          "{\"remove\": obj_id}. experiment_id = the experiment that tested it.",
        {"claim": {"type": "string"}, "evidence_fact_ids": {"type": "array", "items": {"type": "string"}},
         "object_ids": {"type": "array", "items": {"type": "string"}}, "suggested_change": {"type": "object"},
         "location_xy": _XY, "experiment_id": {"type": "string"}}, ["claim", "evidence_fact_ids"]),
    _fn("finish", "Stop the investigation.", {"reason": {"type": "string"}}, ["reason"]),
]
TOOL_NAMES = [t["function"]["name"] for t in TOOL_SCHEMAS]


def _type_schema(node):
    """Tool schema without numeric range keywords: types, enums, required and array lengths are checked up
    front; out-of-range counts are clamped by the tools themselves."""
    if isinstance(node, dict):
        return {k: _type_schema(v) for k, v in node.items() if k not in ("minimum", "maximum")}
    if isinstance(node, list):
        return [_type_schema(v) for v in node]
    return node


_ARG_VALIDATORS: dict = {}


def arg_errors(name: str, args: dict) -> list[str]:
    import jsonschema
    v = _ARG_VALIDATORS.get(name)
    if v is None:
        schema = next(t["function"]["parameters"] for t in TOOL_SCHEMAS if t["function"]["name"] == name)
        v = _ARG_VALIDATORS[name] = jsonschema.Draft202012Validator(_type_schema(schema))
    out = []
    for e in sorted(v.iter_errors(args), key=lambda e: [str(p) for p in e.absolute_path]):
        where = "/".join(map(str, e.absolute_path)) or "arguments"
        out.append(f"{where}: {e.message[:200]}")
    for k, val in args.items():  # NaN / inf never reach the tools
        if isinstance(val, float) and not math.isfinite(val):
            out.append(f"{k}: must be finite")
    return out[:10]


class ToolError(ValueError):
    pass


@dataclass
class Probe:
    probe_id: str               # sim batch id
    experiment_id: str | None
    overrides: dict
    density: str
    robot_id: str
    n_episodes: int
    success_rate: float
    ci95: tuple[float, float]
    baseline_sr: float
    baseline_ref: str
    delta: float
    half_width: float
    fact_ids: dict = field(default_factory=dict)

    @property
    def improved(self) -> bool:
        return self.delta > self.half_width

    def changed_objects(self) -> set[str]:
        return set((self.overrides.get("move") or {}).keys()) | set(self.overrides.get("remove") or [])


def change_to_overrides(change: dict | None) -> dict | None:
    """Normalized single-object change -> sim overrides dict (Finding.suggested_change format)."""
    if not change:
        return None
    if "remove" in change:
        return {"remove": [change["remove"]]}
    return {"move": {change["object_id"]: {"dx": change["dx"], "dy": change["dy"], "dyaw": change["dyaw"]}}}


def experiment_kind(overrides: dict, density_changed: bool, robot_swapped: bool) -> str:
    if robot_swapped:
        return "swap_robot"
    if overrides.get("ped_zone_scale"):
        return "reduce_pedestrians_zone"
    mv, rm, add = overrides.get("move"), overrides.get("remove"), overrides.get("add_boxes")
    if rm and not mv and not add:
        return "remove_object"
    if mv and not rm and not add:
        return "move_object"
    if density_changed and not (mv or rm or add):
        return "change_density"
    return "other"


class Toolbox:
    def __init__(self, analysis_id: str, investigation_id: str, fact_prefix: str, twin: Twin, robot: RobotSpec,
                 task: Task, batches: list[SimBatch], sim: SimAPI | None = None, *, probe_episodes: int = 30,
                 max_experiments: int = 6, other_robots: list[RobotSpec] | None = None,
                 min_bottleneck_share: float = 0.05, nms_radius_m: float = 0.5, near_radius_m: float = 1.5,
                 probe_seed_base: int = 700_000, bottleneck_loader: Callable[[str], np.ndarray] = _load_npy,
                 episodes_loader: Callable[[str], list[dict]] = _load_episodes, log_jobs: bool = True):
        self.analysis_id, self.investigation_id, self.prefix = analysis_id, investigation_id, fact_prefix
        self.twin, self.robot, self.task = twin, robot, task
        mine = [b for b in batches if b.robot_id == robot.robot_id]
        self.batches = {b.batch_id: b for b in (mine or batches)}
        self.sim = sim or SimAPI()
        self.probe_episodes, self.max_experiments = int(probe_episodes), int(max_experiments)
        self.other_robots = {r.robot_id: r for r in (other_robots or []) if r.robot_id != robot.robot_id}
        self.min_share, self.nms_r, self.near_r = min_bottleneck_share, nms_radius_m, near_radius_m
        self.probe_seed_base = probe_seed_base
        self.load_npy, self.load_episodes = bottleneck_loader, episodes_loader
        self.log_jobs = log_jobs
        self.objects = {p.id: p for p in twin.primitives}
        self.zones = {z.id: z for z in twin.traffic.zones}
        self.facts: dict[str, Fact] = {}
        self.probes: dict[str, Probe] = {}
        self.baseline_probes: dict[tuple[str, str], Probe] = {}
        self.experiments: list[Experiment] = []
        self.findings: list[Finding] = []
        self.finished: str | None = None
        self.no_new_bottleneck = False
        self.sim_runs = 0  # every probe batch (baseline runs included) counts against max_experiments
        self._step = 0
        self._step_facts: list[str] = []
        self._grid: Grid | None = None
        self._edt: np.ndarray | None = None
        self._bn_cache = None

    # ---- facts
    def fact(self, value, unit: str, label: str) -> dict:
        fid = f"{self.prefix}{len(self.facts) + 1:03d}"
        if isinstance(value, (np.floating, np.integer)):
            value = value.item()
        if isinstance(value, float):
            value = round(value, 4)
        self.facts[fid] = Fact(fact_id=fid, value=value, unit=unit, label=label,
                               origin=FactOrigin(type="investigation", ref=f"{self.investigation_id}#step{self._step}"))
        self._step_facts.append(fid)
        return {"value": value, "unit": unit, "fact_id": fid}

    # ---- dispatch
    def call(self, name: str, args: dict | None, step: int) -> tuple[dict, list[str]]:
        """Run one tool. Never raises: any failure comes back as {"ok": False, "error"} and the facts the failed
        call registered are dropped (the model never saw their values, so it can never cite them)."""
        self._step, self._step_facts = step, []
        fn = getattr(self, f"t_{name}", None) if isinstance(name, str) and name in TOOL_NAMES else None
        if fn is None:
            return {"ok": False, "error": f"unknown tool {str(name)[:80]!r}; available: {TOOL_NAMES}"}, []
        if not isinstance(args, dict):
            args = {} if args is None else {"_value": args}
        args = dict(args)
        if "_oversized_arguments" in args:
            return {"ok": False, "error": f"tool arguments too large ({args['_oversized_arguments']} chars)"}, []
        if "_unparseable_arguments" in args or "_value" in args:
            return {"ok": False, "error": "tool arguments were not a valid JSON object"}, []
        errs = arg_errors(name, args)
        if errs:
            return {"ok": False, "error": f"bad arguments for {name}: " + "; ".join(errs)}, []
        try:
            out = fn(**args)
        except ToolError as e:
            return self._fail(str(e))
        except TypeError as e:
            return self._fail(f"bad arguments for {name}: {e}")
        except Exception as e:  # a tool must never take the loop down
            return self._fail(f"{name} failed: {type(e).__name__}: {str(e)[:300]}")
        return {"ok": True, **out}, list(self._step_facts)

    def _fail(self, msg: str) -> tuple[dict, list[str]]:
        for fid in self._step_facts:
            self.facts.pop(fid, None)
        self._step_facts = []
        return {"ok": False, "error": msg[:1000]}, []

    @property
    def experiments_left(self) -> int:
        return max(0, self.max_experiments - self.sim_runs)

    # ---- helpers
    def _batch(self, batch_id: str) -> SimBatch:
        if batch_id not in self.batches:
            raise ToolError(f"unknown batch_id {batch_id!r}; known: {sorted(self.batches)}")
        return self.batches[batch_id]

    def _geom(self, shape=None) -> Grid:
        if self._grid is not None:
            return self._grid
        if self.sim.rasterize_twin is not None:
            try:
                self._grid = self.sim.rasterize_twin(self.twin, "nominal")
                return self._grid
            except Exception:
                pass
        try:
            from clearance_core import grid as cgrid
            self._grid = cgrid.load(self.twin.occupancy)
            return self._grid
        except Exception:
            pass
        if shape is None:
            raise ToolError("no occupancy grid: jobs.sim.api.rasterize_twin unavailable and occupancy PNG missing")
        occ = self.twin.occupancy  # coordinates only (for Pinch Map peaks)
        return Grid(np.full(shape, 255, np.uint8), occ.resolution_m, tuple(occ.origin_xy))

    @staticmethod
    def _xy(xy) -> tuple[float, float]:
        if not isinstance(xy, (list, tuple)) or len(xy) != 2 or any(isinstance(v, (bool, str)) for v in xy):
            raise ToolError(f"xy must be [x, y] numbers, got {str(xy)[:80]}")
        try:
            x, y = float(xy[0]), float(xy[1])
        except Exception as e:
            raise ToolError(f"xy must be [x, y] numbers, got {xy!r}") from e
        if not (math.isfinite(x) and math.isfinite(y)):
            raise ToolError("xy must be finite")
        return x, y

    def _objects_near(self, x, y, r):
        from shapely.geometry import Point
        pt = Point(x, y)
        out = [(primitive_polygon(p).distance(pt), p) for p in self.twin.primitives]
        out = [t for t in out if t[0] <= r]
        out.sort(key=lambda t: (t[0], t[1].id))
        return out

    def _zones_near(self, x, y, r):
        from shapely.geometry import Point, Polygon
        pt = Point(x, y)
        out = []
        for z in self.zones.values():
            if len(z.polygon) >= 3:
                d = Polygon(z.polygon).distance(pt)
                if d <= r:
                    out.append((d, z.id))
        return [zid for _, zid in sorted(out)]

    # ---- observation tools
    def t_get_batch_summary(self, batch_id: str | None = None):
        ids = [batch_id] if batch_id else sorted(self.batches)
        rows = []
        for bid in ids:
            b = self._batch(bid)
            res = b.results
            row = {"batch_id": bid, "density": b.density, "bound": b.geometry_bound}
            if res is None:
                row["results"] = None
                rows.append(row)
                continue
            tag = f"{self.robot.robot_id}, {b.density} traffic, {b.geometry_bound} geometry"
            row["success_rate"] = self.fact(res.success_rate, "ratio", f"Success rate, {tag} ({bid})")
            row["ci95_low"] = self.fact(res.ci95[0], "ratio", f"Success rate 95% CI low, {tag} ({bid})")
            row["ci95_high"] = self.fact(res.ci95[1], "ratio", f"Success rate 95% CI high, {tag} ({bid})")
            row["n_episodes"] = self.fact(res.n_episodes or b.n_episodes, "episodes", f"Episodes, {tag} ({bid})")
            if batch_id:
                if res.median_mission_s is not None:
                    row["median_mission_s"] = self.fact(res.median_mission_s, "s", f"Median mission time, {tag}")
                if res.min_gap_m is not None:
                    row["min_gap_m"] = self.fact(res.min_gap_m, "m", f"Median smallest robot-to-obstacle gap, {tag}")
                row["near_miss_per_mission"] = self.fact(res.near_miss_per_mission, "per mission",
                                                         f"Near-misses per mission, {tag}")
                row["stops_per_mission"] = self.fact(res.stops_per_mission, "per mission",
                                                     f"Safety stops per mission, {tag}")
            row["fail_reasons"] = {k: self.fact(v, "episodes", f"Failures '{k}', {tag} ({bid})")
                                   for k, v in sorted(res.fail_reasons.items()) if v}
            rows.append(row)
        return {"robot_id": self.robot.robot_id, "batches": rows}

    def _bottleneck_sum(self):
        if self._bn_cache is not None:
            return self._bn_cache
        bs = [b for b in self.batches.values() if b.bottleneck_uri]
        nominal = [b for b in bs if b.geometry_bound == "nominal"]
        agg, used = None, []
        for b in sorted(nominal or bs, key=lambda b: b.batch_id):
            try:
                a = np.asarray(self.load_npy(b.bottleneck_uri), dtype=np.float64)
            except Exception:
                continue
            if a.ndim != 2 or (agg is not None and a.shape != agg.shape):
                continue
            agg = np.zeros_like(a) if agg is None else agg
            agg += np.nan_to_num(np.clip(a, 0, None))
            used.append(b.batch_id)
        self._bn_cache = (agg, used)
        return self._bn_cache

    def t_get_bottlenecks(self, top_k: int = 3):
        top_k = max(1, min(int(top_k), 10))
        agg, used = self._bottleneck_sum()
        if agg is None:
            raise ToolError("no Pinch Map (.bottleneck.npy) available for this robot's batches")
        total = float(agg.sum())
        if total <= 0:
            self.no_new_bottleneck = True
            return {"bottlenecks": [], "batches_used": used, "note": "Pinch Map is empty"}
        g = self._geom(agg.shape)
        rad = max(1, int(round(self.nms_r / g.res)))
        yy, xx = np.mgrid[-rad:rad + 1, -rad:rad + 1]
        disc = ((xx ** 2 + yy ** 2) <= rad ** 2).astype(np.float64)
        from scipy.signal import fftconvolve
        region = np.clip(fftconvolve(agg, disc, mode="same"), 0, None)
        region[agg <= 0] = 0  # peaks sit on cells that accumulated events
        H, W = agg.shape
        out = []
        work = region.copy()
        while len(out) < top_k:
            idx = int(np.argmax(work))
            r, c = divmod(idx, W)
            if work[r, c] <= 0 or float(work[r, c]) / total < self.min_share:
                break
            share = float(work[r, c]) / total
            x, y = (round(float(v), 3) for v in g.cell_to_world(r, c))
            examined = any(f.location_xy and math.dist(f.location_xy, (x, y)) <= 2 * self.nms_r
                           for f in self.findings)
            k = len(out) + 1
            out.append({"rank": k, "xy": [x, y],
                        "share_of_total": self.fact(share, "ratio", f"Pinch Map spot #{k}: share of accumulated "
                                                                    f"stop/near-miss/blocked score within "
                                                                    f"{self.nms_r} m, {self.robot.robot_id}"),
                        "nearby_object_ids": [p.id for _, p in self._objects_near(x, y, self.near_r)],
                        "nearby_zone_ids": self._zones_near(x, y, self.near_r), "examined": examined})
            s = 2 * rad
            work[max(0, r - s):min(H, r + s + 1), max(0, c - s):min(W, c + s + 1)] = 0
        if not any(not b["examined"] for b in out):
            self.no_new_bottleneck = True
        return {"bottlenecks": out, "batches_used": used, "threshold_share": self.min_share}

    def t_get_gap_at(self, xy):
        x, y = self._xy(xy)
        g = self._geom()
        if self._edt is None:
            from scipy.ndimage import distance_transform_edt
            self._edt = distance_transform_edt(~g.blocked()) * g.res
        r, c = (int(v) for v in g.world_to_cell(x, y))
        H, W = g.data.shape
        if not (0 <= r < H and 0 <= c < W):
            raise ToolError(f"point {list(xy)} is outside the occupancy grid")
        d = float(self._edt[r, c])
        out = {"xy": [x, y], "in_obstacle": d == 0.0,
               "nearest_obstacle_m": self.fact(d, "m", f"Distance from ({x:.2f}, {y:.2f}) to nearest obstacle "
                                                       "(nominal grid)"),
               "gap_m": self.fact(2 * d, "m", f"Approximate free gap through ({x:.2f}, {y:.2f}) "
                                              "(2 x nearest-obstacle distance, nominal grid)")}
        w = robot_width_m(self.robot)
        out["robot_width_m"] = None
        if w is not None:
            out["robot_width_m"] = self.fact(w, "m", f"Footprint width, {self.robot.robot_id}")
            out["gap_margin_m"] = self.fact(2 * d - w, "m", f"Approximate gap minus robot width at "
                                                            f"({x:.2f}, {y:.2f})")
        return out

    def t_get_episode_events(self, batch_id: str, type: str, limit: int = 20):
        b = self._batch(batch_id)
        if type not in EVENT_TYPES:
            raise ToolError(f"type must be one of {EVENT_TYPES}")
        if not b.episodes_uri:
            raise ToolError(f"batch {batch_id} has no recorded episodes")
        try:
            lines = self.load_episodes(b.episodes_uri)
        except Exception as e:
            raise ToolError(f"could not read episodes for {batch_id}: {e}") from e
        limit = max(1, min(int(limit), 50))
        count, sample, eps = 0, [], set()
        cur_ep, prev_t = 0, None
        for ln in lines:
            ep = ln.get("ep", ln.get("episode", ln.get("episode_idx")))
            if ep is None:  # infer episode boundaries from time resets
                t = ln.get("t")
                if prev_t is not None and t is not None and t < prev_t:
                    cur_ep += 1
                prev_t = t
                ep = cur_ep
            for ev in ln.get("events") or []:
                if ev.get("type") == type:
                    count += 1
                    eps.add(ep)
                    if len(sample) < limit:
                        sample.append({"episode": ep, "t": ln.get("t"), "xy": ev.get("xy"),
                                       "detail": ev.get("detail")})
        n_ep = (b.results.n_episodes if b.results and b.results.n_episodes else b.n_episodes) or 1
        return {"batch_id": batch_id, "type": type,
                "count": self.fact(count, "events", f"'{type}' events in {batch_id}"),
                "episodes_with_event": self.fact(len(eps), "episodes", f"Episodes with a '{type}' event, {batch_id}"),
                "per_mission": self.fact(count / n_ep, "per mission", f"'{type}' events per mission, {batch_id}"),
                "sample": sample}

    def t_get_objects_near(self, xy, r: float = 1.5):
        x, y = self._xy(xy)
        r = float(r)
        if not (0.05 <= r <= 5.0):
            raise ToolError("r must be in [0.05, 5.0] m")
        objs = [{"id": p.id, "label": p.label, "movable": p.movable, "fragile": p.fragile,
                 "center_xy": [round(p.pose.xyz[0], 3), round(p.pose.xyz[1], 3)],
                 "distance_m": self.fact(d, "m", f"Distance from ({x:.2f}, {y:.2f}) to the edge of object {p.id} "
                                                 f"({p.label})"),
                 "confidence": p.confidence} for d, p in self._objects_near(x, y, r)]
        return {"xy": [x, y], "r": r, "objects": objs, "zone_ids": self._zones_near(x, y, r)}

    # ---- experiments
    def _validate_overrides(self, ov) -> dict:
        ov = ov or {}
        if not isinstance(ov, dict):
            raise ToolError("overrides must be an object")
        bad = set(map(str, ov)) - {"move", "remove", "add_boxes", "ped_zone_scale"}
        if bad:
            raise ToolError(f"unknown override keys {sorted(bad)}")
        for k in ("move", "ped_zone_scale"):
            if ov.get(k) is not None and not isinstance(ov[k], dict):
                raise ToolError(f"overrides.{k} must be an object keyed by id")
        clean: dict = {}
        for oid, mv in (ov.get("move") or {}).items():
            if not isinstance(mv, dict):
                raise ToolError(f"move for {oid} must be an object {{dx, dy, dyaw}}")
            p = self.objects.get(oid)
            if p is None:
                raise ToolError(f"unknown object {oid!r}")
            if not p.movable:
                raise ToolError(f"object {oid} is not movable")
            try:
                dx, dy, dyaw = float(mv.get("dx", 0)), float(mv.get("dy", 0)), float(mv.get("dyaw", 0))
            except Exception as e:
                raise ToolError(f"move for {oid} needs numeric dx/dy/dyaw") from e
            if not all(math.isfinite(v) for v in (dx, dy, dyaw)):
                raise ToolError(f"move for {oid} must be finite")
            if max(abs(dx), abs(dy)) > 1.0 or abs(dyaw) > math.pi:
                raise ToolError(f"move for {oid} too large (|dx|,|dy| <= 1.0 m, |dyaw| <= pi)")
            clean.setdefault("move", {})[oid] = {"dx": dx, "dy": dy, "dyaw": dyaw}
        rem = ov.get("remove") or []
        if not isinstance(rem, (str, list)):
            raise ToolError("overrides.remove must be a list of object ids")
        for oid in ([rem] if isinstance(rem, str) else rem):
            if not isinstance(oid, str):
                raise ToolError("overrides.remove must be a list of object ids")
            p = self.objects.get(oid)
            if p is None:
                raise ToolError(f"unknown object {oid!r}")
            if not p.movable:
                raise ToolError(f"object {oid} is not movable; removing it is not an actionable change")
            clean.setdefault("remove", []).append(oid)
        if ov.get("add_boxes"):
            if not isinstance(ov["add_boxes"], list) or len(ov["add_boxes"]) > 20:
                raise ToolError("add_boxes must be a list of at most 20 primitives")
            try:
                clean["add_boxes"] = [Primitive.model_validate(b).model_dump(mode="json") for b in ov["add_boxes"]]
            except Exception as e:
                raise ToolError(f"add_boxes: {e}") from e
        for zid, f in (ov.get("ped_zone_scale") or {}).items():
            self._check_zone(zid, f)
            clean.setdefault("ped_zone_scale", {})[zid] = float(f)
        return clean

    def _check_zone(self, zid, f):
        if not isinstance(zid, str) or zid not in self.zones:
            raise ToolError(f"unknown traffic zone {zid!r}; known: {sorted(self.zones)}")
        try:
            f = float(f)
        except Exception as e:
            raise ToolError("ped_scale must be a number") from e
        if not 0.0 <= f <= 1.0:
            raise ToolError("ped_scale must be in [0, 1] (reduce pedestrians in the zone)")

    def _run(self, robot: RobotSpec, overrides: dict, density: str, n: int) -> SimBatch:
        if self.sim.run_batch is None:
            raise ToolError(f"sim run_batch unavailable ({self.sim.source})")
        t0 = time.perf_counter()
        self.sim_runs += 1  # attempts count against the cap, so a failing sim cannot be called without bound
        try:
            b = self.sim.run_batch(self.twin, robot, self.task, density, "nominal", n, self.probe_seed_base,
                                   overrides=overrides or None, record_episodes=False,
                                   out_prefix=f"analyses/{self.analysis_id}/probes/")
            if isinstance(b, dict):
                b = SimBatch.model_validate(b)
        except Exception as e:
            raise ToolError(f"sim probe failed: {type(e).__name__}: {str(e)[:200]}") from e
        if self.log_jobs:
            try:
                from agents.llm.usage import log_usage
                log_usage("sim.probe", "sim", 0, 0, time.perf_counter() - t0, 0.0,
                          {"analysis_id": self.analysis_id, "robot_id": robot.robot_id, "n_episodes": n,
                           "density": density, "batch_id": b.batch_id})
            except Exception:
                pass
        if b.results is None:
            raise ToolError(f"probe batch {b.batch_id} returned no results")
        return b

    def _baseline(self, density: str, n: int) -> tuple[float, str, bool]:
        """Baseline = this robot's existing nominal batch at that density, else one baseline probe (no changes)."""
        for b in sorted(self.batches.values(), key=lambda b: b.batch_id):
            if b.density == density and b.geometry_bound == "nominal" and b.results is not None:
                return float(b.results.success_rate), b.batch_id, False
        key = (self.robot.robot_id, density)
        if key in self.baseline_probes:
            p = self.baseline_probes[key]
            return p.success_rate, p.probe_id, False
        if self.experiments_left < 2:
            raise ToolError("experiment cap: a baseline probe plus the experiment would exceed the cap")
        b = self._run(self.robot, {}, density, n)
        r = b.results
        self.baseline_probes[key] = Probe(b.batch_id, None, {}, density, self.robot.robot_id, n,
                                          float(r.success_rate), tuple(r.ci95), float(r.success_rate), b.batch_id,
                                          0.0, 0.0)
        return float(r.success_rate), b.batch_id, True

    def _close_previous(self, conclusion: str | None):
        """A new experiment after an open one means the loop chose to dig further."""
        if not self.experiments:
            return
        prev = self.experiments[-1]
        if prev.conclusion:
            return
        prev.next_action = "dig_further"
        prev.conclusion = conclusion or self.auto_conclusion(prev)

    def check_text(self, text: str, cited: list[str]) -> list[str]:
        """Errors for model-written text: unknown fact tags, or numbers not backed by a cited fact."""
        errs = []
        if len(text) > MAX_TEXT_CHARS:
            return [f"text too long ({len(text)} chars, limit {MAX_TEXT_CHARS})"]
        if states_verdict(text):
            errs.append("verdict wording (Cleared / Cleared with changes / Not cleared) is not allowed: verdicts are "
                        "set by code from the simulation results")
        tags = TAG.findall(text)
        unknown = sorted({str(f) for f in list(cited) + tags if f not in self.facts})[:20]
        if unknown:
            errs.append(f"unknown fact ids {unknown}: cite only fact IDs returned by tools in this investigation")
            return errs
        ids = list(dict.fromkeys(list(cited) + tags))
        bad = unsupported_numbers(text, [self.facts[f].value for f in ids], self._ignore_tokens())
        if bad:
            errs.append(f"numbers {bad[:20]} are not values of the cited facts; every number must come from a cited "
                        "tool fact")
        return errs

    def _ignore_tokens(self) -> set[str]:
        return (set(self.objects) | set(self.zones) | {w.id for w in self.twin.waypoints} | set(self.batches)
                  | set(self.probes) | {p.probe_id for p in self.baseline_probes.values()}
                  | {e.experiment_id for e in self.experiments} | {self.robot.robot_id, self.robot.vendor,
                                                                    self.robot.model} | set(self.other_robots))

    def sanitize_text(self, text: str, limit: int = MAX_TEXT_CHARS) -> str:
        """Free model text kept for the trail (thoughts, finish reasons): numbers that match no fact registered in
        this investigation become "[n?]" and verdict wording becomes "[verdict set by code]"."""
        text = str(text or "")[:limit]
        nums = [float(f.value) for f in self.facts.values() if isinstance(f.value, (int, float))
                and not isinstance(f.value, bool) and math.isfinite(float(f.value))]
        ignore = self._ignore_tokens()
        protected = [m.span() for m in TAG.finditer(text)]
        for tok in ignore:
            if tok:
                protected += [m.span() for m in re.finditer(rf"(?<![\w]){re.escape(tok)}(?![\w])", text)]
        spans = [m.span() for m in _NUM.finditer(text)
                 if not any(a <= m.start() < b for a, b in protected) and not _number_matches(m.group(0), nums)]
        for a, b in reversed(spans):
            text = text[:a] + "[n?]" + text[b:]
        text = _VERDICT_CI.sub("[verdict set by code]", text)
        return _VERDICT_CS.sub("[verdict set by code]", text)

    def auto_conclusion(self, e: Experiment) -> str:
        p = self.probes.get(e.batch_id or "")
        if p is None:
            return ""
        d, hw = p.fact_ids["improvement"], p.fact_ids["ci_half_width"]
        word = {"supported": "supported", "refuted": "refuted", "inconclusive": "inconclusive"}[e.outcome]
        return (f"Hypothesis {word}: success-rate change [{d}] vs probe CI half-width [{hw}] "
                f"(probe [{p.fact_ids['success_rate']}] vs baseline [{p.fact_ids['baseline_success_rate']}]).")

    def t_run_probe(self, hypothesis: str = "", overrides: dict | None = None, zone_id: str | None = None,
                    ped_scale: float | None = None, density: str = "normal", robot_id: str | None = None,
                    n_episodes: int | None = None, previous_conclusion: str | None = None):
        if not isinstance(hypothesis, str) or not hypothesis.strip():
            raise ToolError("state the hypothesis this experiment tests")
        errs = self.check_text(hypothesis, [])
        if previous_conclusion:
            errs += self.check_text(previous_conclusion, [])
        if errs:
            raise ToolError("; ".join(errs))
        if self.experiments_left < 1:
            raise ToolError(f"experiment cap reached ({self.max_experiments} per robot)")
        if density not in DENSITIES:
            raise ToolError(f"density must be one of {DENSITIES}")
        n = max(1, min(int(n_episodes or self.probe_episodes), self.probe_episodes))
        ov = self._validate_overrides(overrides)
        if zone_id is not None or ped_scale is not None:
            if zone_id is None or ped_scale is None:
                raise ToolError("zone_id and ped_scale go together")
            self._check_zone(zone_id, ped_scale)
            ov.setdefault("ped_zone_scale", {})[zone_id] = float(ped_scale)
        robot = self.robot
        if robot_id and robot_id != self.robot.robot_id:
            if robot_id not in self.other_robots:
                raise ToolError(f"unknown robot_id {robot_id!r}; available for swap: {sorted(self.other_robots)}")
            robot = self.other_robots[robot_id]
        swapped = robot is not self.robot
        if not ov and density == "normal" and not swapped:
            raise ToolError("an experiment needs a change: overrides, zone_id+ped_scale, density or robot_id")
        self._close_previous(previous_conclusion)
        base_sr, base_ref, ran_base = self._baseline(density, n)
        b = self._run(robot, ov, density, n)
        res = b.results
        lo, hi = float(res.ci95[0]), float(res.ci95[1])
        hw = (hi - lo) / 2.0
        delta = float(res.success_rate) - base_sr
        desc = json.dumps({"overrides": ov, "density": density, "robot_id": robot.robot_id}, sort_keys=True)
        out = {"probe_id": b.batch_id, "overrides": ov, "density": density, "robot_id": robot.robot_id,
               "baseline_ref": base_ref, "ran_baseline_probe": ran_base,
               "success_rate": self.fact(res.success_rate, "ratio", f"Probe {b.batch_id} success rate ({desc}, "
                                                                     "nominal geometry)"),
               "ci95_low": self.fact(lo, "ratio", f"Probe {b.batch_id} 95% CI low"),
               "ci95_high": self.fact(hi, "ratio", f"Probe {b.batch_id} 95% CI high"),
               "n_episodes": self.fact(res.n_episodes or n, "episodes", f"Probe {b.batch_id} episodes"),
               "baseline_success_rate": self.fact(base_sr, "ratio", f"Baseline success rate for probe "
                                                                     f"{b.batch_id} ({base_ref}, "
                                                                     f"{self.robot.robot_id})"),
               "improvement": self.fact(delta, "ratio", f"Probe {b.batch_id} success-rate change vs baseline"),
               "ci_half_width": self.fact(hw, "ratio", f"Probe {b.batch_id} 95% CI half-width")}
        fids = {k: out[k]["fact_id"] for k in ("success_rate", "ci95_low", "ci95_high", "n_episodes",
                                                "baseline_success_rate", "improvement", "ci_half_width")}
        outcome = "supported" if delta > hw else ("refuted" if delta < -hw else "inconclusive")
        eid = f"ex_{self.prefix[2:]}{len(self.experiments) + 1:02d}"
        kind = experiment_kind(ov, density != "normal", swapped)
        # flat: sim override keys (move/remove/add_boxes/ped_zone_scale) + density, swapped robot_id, zone_id/ped_scale
        change = {**ov, **{k: v for k, v in {"density": density, "robot_id": robot.robot_id if swapped else None,
                                             "zone_id": zone_id, "ped_scale": ped_scale}.items() if v is not None}}
        self.experiments.append(Experiment(
            experiment_id=eid, robot_id=self.robot.robot_id, hypothesis=hypothesis.strip(), kind=kind, change=change,
            batch_id=b.batch_id, baseline_fact_ids=[fids["baseline_success_rate"]],
            result_fact_ids=[fids[k] for k in ("success_rate", "ci95_low", "ci95_high", "n_episodes", "improvement",
                                               "ci_half_width")],
            outcome=outcome, next_action="conclude", conclusion=""))
        self.probes[b.batch_id] = Probe(b.batch_id, eid, ov, density, robot.robot_id, n, float(res.success_rate),
                                        (lo, hi), base_sr, base_ref, delta, hw, fids)
        out.update({"experiment_id": eid, "kind": kind, "outcome": outcome, "improved_beyond_ci": delta > hw,
                    "experiments_left": self.experiments_left})
        return out

    # ---- conclusions
    @staticmethod
    def _change_matches(change: dict, p: Probe, robot_id: str) -> bool:
        """The probe applied exactly this counterfactual (same robot, only this object, same move)."""
        if p.robot_id != robot_id or p.overrides.get("add_boxes") or p.overrides.get("ped_zone_scale"):
            return False
        target = change["remove"] if "remove" in change else change.get("object_id")
        if p.changed_objects() != {target}:
            return False
        if "remove" in change:
            return target in (p.overrides.get("remove") or [])
        mv = (p.overrides.get("move") or {}).get(target)
        return mv is not None and (abs(mv["dx"] - change["dx"]) <= 0.051 and abs(mv["dy"] - change["dy"]) <= 0.051
                                   and abs(mv["dyaw"] - change["dyaw"]) <= math.radians(2.0))

    def _validate_change(self, ch) -> dict:
        if not isinstance(ch, dict):
            raise ToolError("suggested_change must be an object")
        if isinstance(ch.get("move"), dict):  # overrides form {"move": {oid: {dx, dy, dyaw}}}
            if len(ch["move"]) != 1 or ch.get("remove"):
                raise ToolError("suggested_change must change exactly one object")
            (oid, mv), = ch["move"].items()
            ch = {"object_id": oid, **(mv or {})}
        elif isinstance(ch.get("remove"), list):
            if len(ch["remove"]) != 1:
                raise ToolError("suggested_change must change exactly one object")
            ch = {"remove": ch["remove"][0]}
        oid = ch.get("remove", ch.get("object_id"))
        if oid not in self.objects:
            raise ToolError(f"suggested_change: unknown object {oid!r}")
        if not self.objects[oid].movable:
            raise ToolError(f"suggested_change: object {oid} is not movable")
        if "remove" in ch:
            return {"remove": oid}
        try:
            return {"object_id": oid, "dx": float(ch.get("dx", 0)), "dy": float(ch.get("dy", 0)),
                    "dyaw": float(ch.get("dyaw", 0))}
        except Exception as e:
            raise ToolError("suggested_change needs numeric dx/dy/dyaw") from e

    def t_record_finding(self, claim: str, evidence_fact_ids: list, object_ids: list | None = None,
                         suggested_change: dict | None = None, location_xy=None, experiment_id: str | None = None,
                         probe_id: str | None = None):
        if not isinstance(claim, str) or not claim.strip():
            raise ToolError("claim must be a non-empty string")
        ev = [str(e) for e in (evidence_fact_ids or [])]
        errors = self.check_text(claim, ev)
        ev = list(dict.fromkeys(ev + TAG.findall(claim)))
        if not ev:
            errors.append("at least one evidence fact id is required")
        obj_ids = [str(o) for o in (object_ids or [])]
        bad_obj = [o for o in obj_ids if o not in self.objects]
        if bad_obj:
            errors.append(f"unknown object ids {bad_obj}")
        change = loc = None
        if suggested_change:
            try:
                change = self._validate_change(suggested_change)
            except ToolError as e:
                errors.append(str(e))
        if location_xy is not None:
            try:
                loc = self._xy(location_xy)
            except ToolError as e:
                errors.append(str(e))
        exp = None
        if experiment_id is not None:
            exp = next((e for e in self.experiments if e.experiment_id == experiment_id), None)
            if exp is None:
                errors.append(f"unknown experiment_id {experiment_id!r}")
        if probe_id is not None and probe_id not in self.probes:
            errors.append(f"unknown probe_id {probe_id!r}")
        if errors:
            raise ToolError("finding rejected: " + "; ".join(errors))
        if exp is None and probe_id is not None:
            exp = next((e for e in self.experiments if e.batch_id == probe_id), None)
        confirmed, used = False, None
        if change is not None:
            pool = [self.probes[exp.batch_id]] if exp is not None else list(self.probes.values())
            matching = [p for p in pool if self._change_matches(change, p, self.robot.robot_id)]
            if matching:
                best = max(matching, key=lambda p: p.delta - p.half_width)
                used, confirmed = best, best.improved
                if confirmed:
                    ev += [f for f in (best.fact_ids["improvement"], best.fact_ids["ci_half_width"]) if f not in ev]
                if exp is None:
                    exp = next((e for e in self.experiments if e.experiment_id == best.experiment_id), None)
            target = change.get("object_id", change.get("remove"))
            if target not in obj_ids:
                obj_ids.append(target)
        if exp is not None and not exp.conclusion:
            exp.conclusion = claim.strip()
            exp.next_action = "conclude"
        fid = f"fd_{self.prefix[2:]}{len(self.findings) + 1:02d}"
        f = Finding(finding_id=fid, robot_id=self.robot.robot_id, claim=claim.strip(), evidence_fact_ids=ev,
                    location_xy=loc, object_ids=obj_ids, confirmed_by_probe=confirmed,
                    suggested_change=change_to_overrides(change))
        self.findings.append(f)
        return {"finding_id": fid, "confirmed_by_probe": confirmed, "probe_id": used.probe_id if used else None,
                "experiment_id": exp.experiment_id if exp else None, "evidence_fact_ids": ev}

    def t_finish(self, reason: str = ""):
        self.finished = str(reason or "finished")
        return {"finished": True, "reason": self.finished}

    def close_open_experiments(self):
        for e in self.experiments:
            if not e.conclusion:
                e.conclusion = self.auto_conclusion(e)
                e.next_action = "conclude"
