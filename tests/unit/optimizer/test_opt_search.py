import json
import math
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

import opt_fixtures as fx
from jobs.optimizer.search import optimize_layout, search_layout


def _run(twin=None, runner=None, **kw):
    twin = twin or fx.make_twin()
    runner = runner or fx.ScriptedRunner()
    bn = kw.pop("bottleneck", None)
    if bn is None:
        bn = fx.make_bottleneck(fx.walls_grid())
    out = search_layout(twin, fx.make_robot(), fx.make_task(), bn, runner=runner,
                        rasterizer=fx.scripted_rasterize, **kw)
    return out, runner


def _dump(results):
    return json.dumps([{k: (v.model_dump() if hasattr(v, "model_dump") else v) for k, v in r.items()}
                       for r in results], sort_keys=True)


@pytest.fixture(scope="module")
def full_run():
    return _run()


def test_finds_known_good_pair(full_run):
    out, runner = full_run
    res = out["results"]
    assert res, "optimizer returned no changes"
    best = res[0]
    assert best["objects"] == ["C1", "T4"]
    mv = best["change"]["move"]
    assert mv["T4"]["dy"] >= 0.2 - 1e-9 and mv["C1"]["dy"] < 0
    assert best["significant"] and best["status"] == "significant"
    assert best["after"].success_rate > 0.9 > 0.2 > best["before"].success_rate
    assert best["description"].startswith("Move C1 ") and "; move T4 " in best["description"]
    assert "north" in best["description"] and "south" in best["description"]
    assert best["summary"].startswith(best["description"] + ": success ")
    assert len(best["batch_ids"]) == 2
    # sorted by uplift, only uplifts > 0, significance flag consistent with CI overlap
    ups = [r["uplift"] for r in res]
    assert ups == sorted(ups, reverse=True) and min(ups) > 0
    for r in res:
        assert r["significant"] == (r["after"].ci95[0] > r["before"].ci95[1])
        assert r["after"].n_episodes == 200 and r["before"].n_episodes == 200


def test_best_change_confirmed_on_conservative_bound(full_run):
    out, runner = full_run
    best = out["results"][0]
    bc, ac = best["before_conservative"], best["after_conservative"]
    assert bc.n_episodes == ac.n_episodes == 200
    assert ac.success_rate > bc.success_rate
    assert ac.success_rate < best["after"].success_rate  # obstacles grow on the conservative bound
    cons = [c for c in runner.calls if c["bound"] == "conservative"]
    assert len(cons) == 2 and cons[1]["overrides"] == best["change"]
    assert all(c["density"] == "normal" for c in runner.calls)
    assert "after_conservative" not in out["results"][1]


def test_single_pinch_known_good_move():
    twin = fx.make_twin()
    twin.primitives = [p for p in twin.primitives if p.id != "C1"]
    out, _ = _run(twin)
    best = out["results"][0]
    assert best["objects"] == ["T4"]
    mv = best["change"]["move"]["T4"]
    assert mv["dy"] >= 0.2 - 1e-9 and math.hypot(mv["dx"], mv["dy"]) <= 0.30 + 1e-9
    assert best["significant"]
    assert best["description"].startswith("Move T4 ") and "(north" in best["description"]
    assert len(best["batch_ids"]) == 3  # before, after, screen


def test_every_screened_move_is_admissible(full_run):
    out, runner = full_run
    from jobs.optimizer.constraints import ConstraintChecker, Rules
    ch = ConstraintChecker(fx.make_twin(), fx.make_robot(), fx.scripted_rasterize, Rules())
    for c in runner.calls:
        mv = (c["overrides"] or {}).get("move", {})
        assert ch.check({k: (v["dx"], v["dy"], v["dyaw"]) for k, v in mv.items()}).ok
    for c in out["candidates"]:
        assert c["n_moves"] <= 40


def test_deterministic_and_executor_invariant(full_run):
    out1, r1 = full_run
    out2, r2 = _run()
    with ThreadPoolExecutor(max_workers=4) as ex:
        out3, r3 = _run(executor=ex)
    assert _dump(out1["results"]) == _dump(out2["results"]) == _dump(out3["results"])
    key = lambda calls: sorted(json.dumps(c, sort_keys=True) for c in calls)
    assert key(r1.calls) == key(r2.calls) == key(r3.calls)
    out4, _ = _run(seed=7)
    assert out4["results"] and out4["results"][0]["batch_ids"] != out1["results"][0]["batch_ids"]


def test_budget_respected():
    budget = {"screen_episodes": 10, "confirm_episodes": 40, "top_k_confirm": 2, "top_k_combo": 2,
              "max_moves_per_object": 5}
    out, runner = _run(budget=budget)
    n_obj = len(out["candidates"])
    screens = [c for c in runner.calls if c["n"] == 10]
    confirms = [c for c in runner.calls if c["n"] == 40]
    assert len(screens) + len(confirms) == len(runner.calls) == out["stats"]["runner_calls"]
    assert len(screens) <= 1 + 5 * n_obj
    assert len(confirms) <= 1 + 2 + 1 + 2  # baseline, top-2, one pair, two verdict-bound runs
    assert out["stats"]["episodes"] == sum(c["n"] for c in runner.calls)
    assert all(c["n"] for c in out["candidates"] for c in [{"n": c["n_moves"] <= 5}])


@pytest.mark.parametrize("cap", [{"max_evaluations": 9}, {"max_evaluations": 4}, {"max_episodes": 1000}])
def test_hard_caps(cap):
    out, runner = _run(budget=cap)
    if "max_evaluations" in cap:
        assert 0 < len(runner.calls) <= cap["max_evaluations"]
    else:
        assert sum(c["n"] for c in runner.calls) <= cap["max_episodes"]


def test_cap_too_small_runs_nothing():
    out, runner = _run(budget={"max_evaluations": 2})
    assert runner.calls == [] and out["results"] == []


def test_hints_screened_first():
    out, runner = _run(budget={"max_evaluations": 8, "verdict_confirm": False}, hint_object_ids=["S1"])
    assert out["candidates"][0]["id"] == "S1" and out["candidates"][0]["hint"]
    first_move = runner.calls[1]["overrides"]["move"]
    assert list(first_move) == ["S1"]


def test_empty_candidates():
    bn = np.zeros((fx.H, fx.W), np.float32)
    runner = fx.ScriptedRunner()
    res = optimize_layout(fx.make_twin(), fx.make_robot(), fx.make_task(), bn, runner=runner,
                          rasterizer=fx.scripted_rasterize)
    assert res == [] and runner.calls == []
    # non-movable hints are skipped, still nothing to evaluate
    out, runner = _run(bottleneck=bn, hint_object_ids=["CNT"])
    assert out["results"] == [] and runner.calls == [] and out["skipped_hints"] == ["CNT"]


def test_hint_without_bottleneck_is_screened():
    bn = np.zeros((fx.H, fx.W), np.float32)
    out, runner = _run(bottleneck=bn, hint_object_ids=["T4"], budget={"max_moves_per_object": 40})
    assert [c["id"] for c in out["candidates"]] == ["T4"]
    assert out["results"] and out["results"][0]["objects"] == ["T4"]


def test_not_significant_marked():
    out, _ = _run(budget={"confirm_episodes": 15})
    res = out["results"]
    assert any(r["status"] == "not significant" for r in res)
    for r in res:
        assert r["significant"] == (r["after"].ci95[0] > r["before"].ci95[1])
        if not r["significant"]:
            assert r["summary"].endswith("[not significant]")


def test_unknown_budget_key_rejected():
    with pytest.raises(ValueError):
        _run(budget={"screen_episode": 3})
    with pytest.raises(ValueError):
        _run(rules={"egress_width": 1.0})


def test_bottleneck_shape_mismatch_rejected():
    with pytest.raises(ValueError):
        _run(bottleneck=np.ones((5, 5)))
