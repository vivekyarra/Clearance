"""DBSCAN (scipy cKDTree, no sklearn) and voxel helpers."""
from __future__ import annotations

from collections import deque

import numpy as np
from scipy.spatial import cKDTree


def dbscan(points: np.ndarray, eps: float, min_samples: int = 5, weights: np.ndarray | None = None) -> np.ndarray:
    """Classic DBSCAN. Returns labels (N,), -1 = noise. `weights` lets one point count as several
    (e.g. a voxel standing for many points) toward the core-point density test."""
    pts = np.asarray(points, dtype=np.float64)
    n = len(pts)
    labels = np.full(n, -1, dtype=np.int64)
    if n == 0:
        return labels
    tree = cKDTree(pts)
    neigh = tree.query_ball_point(pts, r=eps)
    w = np.ones(n) if weights is None else np.asarray(weights, dtype=np.float64)
    density = np.array([w[nb].sum() for nb in neigh])
    core = density >= min_samples
    cid = 0
    for i in range(n):
        if labels[i] != -1 or not core[i]:
            continue
        labels[i] = cid
        q = deque([i])
        while q:
            j = q.popleft()
            if not core[j]:
                continue
            for k in neigh[j]:
                if labels[k] == -1:
                    labels[k] = cid
                    if core[k]:
                        q.append(k)
        cid += 1
    return labels


def voxel_keys(points: np.ndarray, voxel: float) -> np.ndarray:
    return np.floor(np.asarray(points) / voxel).astype(np.int64)


def voxel_downsample(points: np.ndarray, voxel: float):
    """Returns (centroids (M,3), counts (M,), inverse index (N,))."""
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    if len(pts) == 0:
        return np.zeros((0, 3)), np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64)
    keys = voxel_keys(pts, voxel)
    uniq, inv, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
    inv = inv.reshape(-1)
    sums = np.zeros((len(uniq), 3))
    np.add.at(sums, inv, pts)
    return sums / counts[:, None], counts, inv


def remove_statistical_outliers(points: np.ndarray, k: int = 8, std_ratio: float = 2.0) -> np.ndarray:
    """Open3D-style statistical outlier removal; returns a boolean inlier mask."""
    pts = np.asarray(points, dtype=np.float64)
    if len(pts) <= k + 1:
        return np.ones(len(pts), dtype=bool)
    d, _ = cKDTree(pts).query(pts, k=k + 1)
    mean_d = d[:, 1:].mean(axis=1)
    thr = mean_d.mean() + std_ratio * mean_d.std()
    return mean_d <= thr
