"""3D Gaussian Splatting PLY export (the INRIA 3DGS layout read by common web viewers such as
GaussianSplats3D): x y z nx ny nz f_dc_0..2 [f_rest_*] opacity scale_0..2 rot_0..3, all float32.
opacity is stored as a logit, scales as log, rot as quaternion (w, x, y, z)."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np

SH_C0 = 0.28209479177387814


def rgb_to_sh0(rgb: np.ndarray) -> np.ndarray:
    return (np.asarray(rgb, dtype=np.float64) - 0.5) / SH_C0


def sh0_to_rgb(sh0: np.ndarray) -> np.ndarray:
    return np.asarray(sh0, dtype=np.float64) * SH_C0 + 0.5


def write_gaussian_ply(path: str | Path, means: np.ndarray, log_scales: np.ndarray, quats_wxyz: np.ndarray,
                       opacity_logits: np.ndarray, sh0: np.ndarray, sh_rest: Optional[np.ndarray] = None) -> int:
    means = np.asarray(means, np.float32).reshape(-1, 3)
    n = len(means)
    sh0 = np.asarray(sh0, np.float32).reshape(n, 3)
    rest = np.zeros((n, 0), np.float32) if sh_rest is None else np.asarray(sh_rest, np.float32).reshape(n, -1)
    cols = {"x": means[:, 0], "y": means[:, 1], "z": means[:, 2],
            "nx": np.zeros(n, np.float32), "ny": np.zeros(n, np.float32), "nz": np.zeros(n, np.float32),
            "f_dc_0": sh0[:, 0], "f_dc_1": sh0[:, 1], "f_dc_2": sh0[:, 2]}
    for i in range(rest.shape[1]):
        cols[f"f_rest_{i}"] = rest[:, i]
    cols["opacity"] = np.asarray(opacity_logits, np.float32).reshape(n)
    s = np.asarray(log_scales, np.float32).reshape(n, 3)
    q = np.asarray(quats_wxyz, np.float32).reshape(n, 4)
    for i in range(3):
        cols[f"scale_{i}"] = s[:, i]
    for i in range(4):
        cols[f"rot_{i}"] = q[:, i]
    dt = np.dtype([(k, "<f4") for k in cols])
    rec = np.empty(n, dtype=dt)
    for k, v in cols.items():
        rec[k] = v
    header = ["ply", "format binary_little_endian 1.0", f"element vertex {n}"]
    header += [f"property float {k}" for k in cols] + ["end_header"]
    Path(path).write_bytes(("\n".join(header) + "\n").encode("ascii") + rec.tobytes())
    return n


def read_gaussian_ply(path: str | Path) -> dict[str, np.ndarray]:
    data = Path(path).read_bytes()
    end = data.index(b"end_header\n") + len(b"end_header\n")
    header = data[:end].decode("ascii").splitlines()
    n = int(next(l for l in header if l.startswith("element vertex")).split()[-1])
    names = [l.split()[-1] for l in header if l.startswith("property float")]
    rec = np.frombuffer(data[end:], dtype=np.dtype([(k, "<f4") for k in names]), count=n)
    return {k: rec[k].copy() for k in names}
