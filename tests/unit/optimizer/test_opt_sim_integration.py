"""One end-to-end run against the real simulator (skipped until jobs.sim.api is importable)."""
import numpy as np
import pytest

import opt_fixtures as fx


def test_optimize_layout_with_real_sim(tmp_path, monkeypatch):
    try:
        from jobs.sim import api
    except Exception as e:  # module still being written by the sim owner
        pytest.skip(f"jobs.sim.api not importable: {e}")
    import cv2
    from clearance_core import storage
    from jobs.optimizer.search import search_layout

    monkeypatch.setattr(storage, "DATA", tmp_path)
    twin = fx.make_twin()
    occ = tmp_path / twin.occupancy.uri
    occ.parent.mkdir(parents=True)
    cv2.imwrite(str(occ), fx.walls_grid().data)  # walls only; the sim paints primitives

    grid = api.rasterize_twin(twin, "nominal")
    assert grid.data.shape == (fx.H, fx.W)
    budget = {"screen_episodes": 3, "confirm_episodes": 6, "top_k_confirm": 2, "top_k_combo": 2,
              "max_moves_per_object": 2, "max_objects": 2}
    out = search_layout(twin, fx.make_robot(), fx.make_task(), fx.make_bottleneck(grid), budget=budget, seed=1)
    assert out["stats"]["runner_calls"] >= 1
    assert out["stats"]["episodes"] <= 3 * (1 + 2 * 2) + 6 * (1 + 2 + 1 + 2)
    for r in out["results"]:
        assert r["uplift"] > 0 and set(r["change"]) == {"move"}
        assert r["before"].n_episodes == r["after"].n_episodes == 6
    if out["results"]:
        assert "after_conservative" in out["results"][0]
    assert np.isfinite([p["value"] for p in out["peaks"]]).all()
