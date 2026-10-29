"""J3: Gaussian splat training from posed keyframes with gsplat. GPU only.

    python -m jobs.splat.train --capture <capture_dir> --out <dir> [--iters 7000] [--prepare-only]

Always prepares the dataset (transforms.json + COLMAP text model, see dataset.py). Training runs when
torch with CUDA and gsplat are importable; otherwise the command exits with code 2 and says what is
missing, leaving the dataset ready for a GPU job.

Install on the GPU job image (Apache-2.0; VERIFY the versions against the CUDA toolkit):
    pip install torch --index-url https://download.pytorch.org/whl/cu121
    pip install gsplat
The loop below uses gsplat's public API (`gsplat.rasterization`, `gsplat.strategy.DefaultStrategy`,
as in gsplat 1.x examples; VERIFY against the installed version). Poses stay fixed (ARKit), so the
splat is in the site frame like the twin.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional

import numpy as np

from jobs.splat.dataset import prepare_dataset

log = logging.getLogger(__name__)
EXIT_NO_GPU = 2


def gpu_backend_status() -> tuple[bool, str]:
    missing = [m for m in ("torch", "gsplat") if importlib.util.find_spec(m) is None]
    if missing:
        return False, f"missing python packages: {', '.join(missing)} (see jobs/splat/train.py for install)"
    import torch
    if not torch.cuda.is_available():
        return False, "torch is installed but no CUDA device is visible; gsplat needs a GPU"
    return True, "ok"


def _ssim(img1, img2, window: int = 11):
    import torch
    import torch.nn.functional as F

    sigma = 1.5
    g = torch.exp(-((torch.arange(window, device=img1.device) - window // 2) ** 2) / (2 * sigma ** 2))
    g = (g / g.sum()).float()
    k = (g[:, None] @ g[None, :])[None, None].repeat(3, 1, 1, 1)
    x, y = img1.permute(2, 0, 1)[None], img2.permute(2, 0, 1)[None]
    mu_x, mu_y = F.conv2d(x, k, padding=window // 2, groups=3), F.conv2d(y, k, padding=window // 2, groups=3)
    sxx = F.conv2d(x * x, k, padding=window // 2, groups=3) - mu_x ** 2
    syy = F.conv2d(y * y, k, padding=window // 2, groups=3) - mu_y ** 2
    sxy = F.conv2d(x * y, k, padding=window // 2, groups=3) - mu_x * mu_y
    c1, c2 = 0.01 ** 2, 0.03 ** 2
    s = ((2 * mu_x * mu_y + c1) * (2 * sxy + c2)) / ((mu_x ** 2 + mu_y ** 2 + c1) * (sxx + syy + c2))
    return s.mean()


def train(dataset_dir: str | Path, out_ply: str | Path, *, iters: int = 7000, downscale: int = 2,
          ssim_weight: float = 0.2, seed: int = 0) -> dict:
    """Train with gsplat on the prepared dataset; writes a 3DGS PLY. Requires CUDA."""
    import torch
    from gsplat import rasterization
    from gsplat.strategy import DefaultStrategy
    from PIL import Image
    from scipy.spatial import cKDTree

    from jobs.semantics.camera import arkit_to_opencv_c2w, invert_rigid
    from jobs.semantics.capture_io import read_ply_xyz
    from jobs.splat.ply import rgb_to_sh0, write_gaussian_ply

    torch.manual_seed(seed)
    dev = torch.device("cuda")
    ds = Path(dataset_dir)
    meta = json.loads((ds / "transforms.json").read_text(encoding="utf-8"))
    images, viewmats, Ks = [], [], []
    for f in meta["frames"]:
        p = Path(f["file_path"])
        p = p if p.is_absolute() else ds / p
        im = Image.open(p).convert("RGB")
        w, h = im.size[0] // downscale, im.size[1] // downscale
        images.append(torch.from_numpy(np.asarray(im.resize((w, h)), dtype=np.float32) / 255.0))
        T = invert_rigid(arkit_to_opencv_c2w(np.asarray(f["transform_matrix"])))
        viewmats.append(torch.from_numpy(T).float())
        sx, sy = w / f["w"], h / f["h"]
        Ks.append(torch.tensor([[f["fl_x"] * sx, 0, f["cx"] * sx], [0, f["fl_y"] * sy, f["cy"] * sy], [0, 0, 1]]).float())

    pts = read_ply_xyz(ds / "points.ply")
    centers = np.array([np.linalg.inv(v.numpy())[:3, 3] for v in viewmats])
    scene_scale = float(np.linalg.norm(centers - centers.mean(axis=0), axis=1).max() * 1.1) or 1.0
    d, _ = cKDTree(pts).query(pts, k=4)
    dist = np.clip(d[:, 1:].mean(axis=1), 1e-3, 0.5)
    n = len(pts)
    params = torch.nn.ParameterDict({
        "means": torch.nn.Parameter(torch.from_numpy(pts).float().to(dev)),
        "scales": torch.nn.Parameter(torch.log(torch.from_numpy(dist).float()).unsqueeze(1).repeat(1, 3).to(dev)),
        "quats": torch.nn.Parameter(torch.nn.functional.normalize(torch.randn(n, 4), dim=-1).to(dev)),
        "opacities": torch.nn.Parameter(torch.logit(torch.full((n,), 0.1)).to(dev)),
        "sh0": torch.nn.Parameter(torch.from_numpy(rgb_to_sh0(np.full((n, 1, 3), 0.5))).float().to(dev)),
    })
    lrs = {"means": 1.6e-4 * scene_scale, "scales": 5e-3, "quats": 1e-3, "opacities": 5e-2, "sh0": 2.5e-3}
    optimizers = {k: torch.optim.Adam([{"params": params[k], "lr": lr, "name": k}], eps=1e-15)
                  for k, lr in lrs.items()}
    strategy = DefaultStrategy()
    strategy.check_sanity(params, optimizers)
    state = strategy.initialize_state(scene_scale=scene_scale)
    t0 = time.time()
    loss_v = float("nan")
    for step in range(iters):
        i = int(torch.randint(len(images), (1,)))
        gt = images[i].to(dev)
        h, w = gt.shape[:2]
        renders, _, info = rasterization(
            means=params["means"], quats=params["quats"], scales=torch.exp(params["scales"]),
            opacities=torch.sigmoid(params["opacities"]), colors=params["sh0"],
            viewmats=viewmats[i][None].to(dev), Ks=Ks[i][None].to(dev), width=w, height=h, sh_degree=0)
        strategy.step_pre_backward(params, optimizers, state, step, info)
        pred = renders[0].clamp(0, 1)
        loss = (1 - ssim_weight) * (pred - gt).abs().mean() + ssim_weight * (1 - _ssim(pred, gt))
        loss.backward()
        for opt in optimizers.values():
            opt.step()
            opt.zero_grad(set_to_none=True)
        strategy.step_post_backward(params, optimizers, state, step, info, packed=False)
        loss_v = float(loss.detach())
        if step % 500 == 0:
            log.info("step %d/%d loss %.4f gaussians %d", step, iters, loss_v, len(params["means"]))
    n_out = write_gaussian_ply(out_ply, params["means"].detach().cpu().numpy(),
                               params["scales"].detach().cpu().numpy(), params["quats"].detach().cpu().numpy(),
                               params["opacities"].detach().cpu().numpy(),
                               params["sh0"].detach().cpu().numpy().reshape(-1, 3))
    return {"iters": iters, "final_loss": loss_v, "n_gaussians": n_out, "train_s": round(time.time() - t0, 1),
            "ply": str(out_ply)}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="J3: gsplat training from a scanner capture")
    ap.add_argument("--capture", required=True)
    ap.add_argument("--out", required=True, help="output directory (dataset + splat.ply)")
    ap.add_argument("--iters", type=int, default=7000)
    ap.add_argument("--downscale", type=int, default=2)
    ap.add_argument("--copy-images", action="store_true", help="copy frames into <out>/images")
    ap.add_argument("--prepare-only", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    summary = prepare_dataset(a.capture, a.out, copy_images=a.copy_images)
    print(f"dataset ready: {summary['n_frames']} frames, {summary['n_points']} init points -> {a.out}")
    if a.prepare_only:
        return 0
    ok, why = gpu_backend_status()
    if not ok:
        print(f"splat training skipped: {why}. Dataset is ready; rerun on a GPU worker.", file=sys.stderr)
        return EXIT_NO_GPU
    res = train(a.out, Path(a.out) / "splat.ply", iters=a.iters, downscale=a.downscale)
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
