import numpy as np
import pytest

from jobs.recon.depth import _poly_basis, fit_local_scale, fit_scale_shift_ransac


def _data(n, s, t, outlier_frac, rng, noise=0.005):
    depth = rng.uniform(0.6, 5.0, n)
    rel = (1.0 / depth - t) / s
    depth_obs = depth * (1 + rng.normal(0, noise, n))
    k = int(outlier_frac * n)
    idx = rng.choice(n, k, replace=False)
    depth_obs[idx] *= rng.uniform(0.4, 2.0, k)
    return rel, depth_obs, idx


@pytest.mark.parametrize("s,t,frac", [(0.8, 0.05, 0.0), (2.5, -0.1, 0.2), (0.03, 0.4, 0.35), (120.0, 0.0, 0.45)])
def test_recovers_scale_shift_with_outliers(s, t, frac):
    rng = np.random.default_rng(1)
    rel, d, out_idx = _data(300, s, t, frac, rng)
    fit = fit_scale_shift_ransac(rel, d, rng=np.random.default_rng(2))
    assert fit is not None and fit["ok"]
    assert fit["s"] == pytest.approx(s, rel=0.02)
    assert fit["t"] == pytest.approx(t, abs=0.02 * max(abs(t), 1 / 5.0) + 0.002)
    assert fit["inlier_ratio"] >= 1 - frac - 0.08
    # the planted outliers are (mostly) rejected
    gross = out_idx[np.abs(d[out_idx] - 1 / (s * rel[out_idx] + t)) / d[out_idx] > 0.15]
    if gross.size:
        assert fit["inliers"][gross].mean() < 0.05


def test_mostly_outliers_gives_low_inlier_ratio():
    rng = np.random.default_rng(3)
    rel, d, _ = _data(200, 1.0, 0.1, 0.0, rng)
    d = rng.permutation(d)  # destroy the relation
    fit = fit_scale_shift_ransac(rel, d, rng=np.random.default_rng(4))
    assert fit is None or fit["inlier_ratio"] < 0.5


def test_too_few_points_and_invalid_values():
    assert fit_scale_shift_ransac(np.array([1.0, 2.0]), np.array([1.0, 0.5])) is None
    rel = np.array([np.nan, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10.0])
    d = 1 / (0.1 * rel + 0.05)
    d[3] = -1
    fit = fit_scale_shift_ransac(rel, d)
    assert fit["ok"] and fit["s"] == pytest.approx(0.1, rel=1e-6)
    assert fit["inliers"].shape == rel.shape and not fit["inliers"][0] and not fit["inliers"][3]


def test_constant_relative_depth_has_no_model():
    assert fit_scale_shift_ransac(np.ones(50), np.linspace(1, 3, 50)) is None


def test_local_scale_recovers_smooth_field():
    rng = np.random.default_rng(5)
    w, h = 256, 192
    u, v = rng.uniform(0, w - 1, 250), rng.uniform(0, h - 1, 250)
    coef_true = np.zeros(_poly_basis(0, 0, w, h, 2).shape[-1])
    coef_true[:3] = [0.04, -0.03, 0.02]
    field = _poly_basis(u, v, w, h, 2) @ coef_true
    pred_inv = rng.uniform(0.2, 1.5, 250)
    depth = 1 / (pred_inv * (1 + field)) * (1 + rng.normal(0, 0.003, 250))
    coef = fit_local_scale(u, v, pred_inv, depth, w, h, degree=2)
    est = _poly_basis(u, v, w, h, 2) @ coef
    assert np.abs(est - field).max() < 0.01
    assert fit_local_scale(u[:5], v[:5], pred_inv[:5], depth[:5], w, h, degree=2) is None
