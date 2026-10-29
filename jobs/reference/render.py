"""Render an ARKit-format Capture of a reference room (software half of the scan gate, docs/GATE.md).

Geometry: walls 2.5 m, every Solid extruded between its z0..z1, floor Z = 0, ceiling at 2.5 m over
the room. The camera is hand-held at ~1.4 m and walks the room's scan_path while sweeping its heading.

Conventions (see jobs/common/camera.py): world = site frame (Z up); camera = ARKit (x right,
y up, looks along -Z); T_world_cam is camera->world, written row-major into poses.jsonl.
look_rotation(yaw, pitch) builds R_world_cam with columns [right, up, -forward].

Tiers
- T1_LIDAR: depth/<frame>.png uint16 mm at 256x192 + confidence/<frame>.png (0/1/2). Noise model:
  1 % depth-proportional Gaussian + 0.3 % smooth per-frame field, edge dropout (confidence 0)
  and mixed "flying" pixels at depth discontinuities (confidence 1), confidence 1 beyond 3.5 m,
  no depth beyond 5 m.
- T2_VIO_MONO: no metric depth. reldepth/<frame>.png uint16 = per-frame affine map of a distorted
  inverse depth (a model of a relative-depth network such as Depth Anything V2 Small: smooth
  multiplicative shape error, edge blur, pixel noise), plus feature_points.jsonl with 100-300
  metric world points per frame (2 cm noise, 5 % outliers), expressed with the drifting poses.
Both tiers: ARKit-like pose drift (random walk, ~1 cm and ~0.06 deg per 10 m), a global tilt and
height offset of the world frame (floor fit must remove it), and a few 'limited' tracking frames
with larger pose error.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Literal

import cv2
import numpy as np

from clearance_core.schemas import Capture, CaptureFiles, Device, PoseRecord
from jobs.common.camera import K_scaled, look_rotation, make_T, pixel_rays_cam, rot_axis, rot_z
from jobs.common.imageio import imwrite

from .geometry import raycast_solids

IMG_W, IMG_H = 320, 240
DEPTH_W, DEPTH_H = 256, 192
FX_AT_1920 = 1450.0
CAM_H = 1.40
WALK_MPS = 0.5
KEYFRAME_DT_M = 0.10
KEYFRAME_DROT_DEG = 10.0
LIDAR_MAX_M = 5.0

DEFAULT_NOISE = {
    "lidar_rel_sigma": 0.01, "lidar_field_sigma": 0.003, "edge_rel_jump": 0.05, "edge_drop_p": 0.6,
    "rel_shape_sigma": 0.045, "rel_pixel_sigma": 0.015, "rel_blur_px": 1.0,
    "fp_sigma_m": 0.02, "fp_outlier_frac": 0.05, "fp_min": 100, "fp_max": 300,
    "drift_trans_per_10m": 0.01, "drift_yaw_deg_per_10m": 0.06, "world_tilt_deg": 0.4, "world_dz_m": 0.015,
    "limited_bursts": 2, "limited_len": 4,
}


def image_K():
    f = FX_AT_1920 * IMG_W / 1920.0
    return (f, f, (IMG_W - 1) / 2.0, (IMG_H - 1) / 2.0)


def plan_trajectory(room, rng: np.random.Generator):
    """Hand-held walk along room.scan_path. Returns a list of true T_world_cam keyframes and
    their arc length (m)."""
    path = np.asarray(room.scan_path, float)
    seg = np.diff(path, axis=0)
    seglen = np.linalg.norm(seg, axis=1)
    keep = seglen > 1e-6
    path = np.vstack([path[:1], path[1:][keep]])
    seg, seglen = seg[keep], seglen[keep]
    cum = np.concatenate([[0], np.cumsum(seglen)])
    s_all = np.arange(0, cum[-1], 0.01)
    xy = np.stack([np.interp(s_all, cum, path[:, 0]), np.interp(s_all, cum, path[:, 1])], axis=1)
    # smoothed heading from a +-0.4 m look-ahead window
    ahead = np.stack([np.interp(np.minimum(s_all + 0.4, cum[-1]), cum, path[:, k]) for k in (0, 1)], axis=1)
    behind = np.stack([np.interp(np.maximum(s_all - 0.4, 0), cum, path[:, k]) for k in (0, 1)], axis=1)
    hd = ahead - behind
    heading = np.unwrap(np.arctan2(hd[:, 1], hd[:, 0]))
    ph = rng.uniform(0, 2 * np.pi, 4)
    sweep = np.deg2rad(65) * np.sin(2 * np.pi * s_all / 3.2 + ph[0])
    pitch = -0.50 + 0.17 * np.sin(2 * np.pi * s_all / 2.3 + ph[1])
    height = CAM_H + 0.02 * np.sin(2 * np.pi * s_all / 0.7 + ph[2])
    roll = np.deg2rad(2.0) * np.sin(2 * np.pi * s_all / 1.9 + ph[3])
    yaw = heading + sweep

    tall = [s for s in room.solids if s.z1 > CAM_H - 0.3]
    keys, arcs = [], []
    last_p, last_R = None, None
    for i in range(len(s_all)):
        p = np.array([xy[i, 0], xy[i, 1], height[i]])
        R = look_rotation(yaw[i], pitch[i], roll[i])
        if last_p is not None:
            dang = np.degrees(np.arccos(np.clip((np.trace(last_R.T @ R) - 1) / 2, -1, 1)))
            if np.linalg.norm(p - last_p) < KEYFRAME_DT_M and dang < KEYFRAME_DROT_DEG:
                continue
        for s in tall:
            if s.contains_xy(p[0], p[1]):
                raise ValueError(f"scan path passes through {s.id} at {p[:2]}")
        keys.append(make_T(R, p))
        arcs.append(s_all[i])
        last_p, last_R = p, R
    return keys, np.asarray(arcs)


def _drift(arcs, rng, noise):
    """Per keyframe drift transforms D_k (applied about the camera) + global world tilt G."""
    n = len(arcs)
    ds = np.diff(np.concatenate([[0], arcs]))
    st = noise["drift_trans_per_10m"] / np.sqrt(100.0)  # per 0.1 m step
    sy = np.deg2rad(noise["drift_yaw_deg_per_10m"]) / np.sqrt(100.0)
    scale = np.sqrt(np.maximum(ds, 1e-6) / 0.1)
    dt = np.cumsum(rng.normal(0, 1, (n, 3)) * st * scale[:, None], axis=0)
    dt[:, 2] *= 0.5
    dyaw = np.cumsum(rng.normal(0, 1, n) * sy * scale)
    axis = rng.normal(0, 1, 3)
    axis[2] = 0
    G = make_T(rot_axis(axis, np.deg2rad(noise["world_tilt_deg"])), [0, 0, noise["world_dz_m"]])
    return dt, dyaw, G


def _limited_mask(n, rng, noise):
    m = np.zeros(n, bool)
    for _ in range(noise["limited_bursts"]):
        if n > 3 * noise["limited_len"]:
            i = int(rng.integers(n // 5, n - noise["limited_len"]))
            m[i:i + noise["limited_len"]] = True
    return m


def _smooth_field(h, w, rng, sigma, n_waves=4):
    yy, xx = np.mgrid[0:h, 0:w]
    f = np.zeros((h, w))
    for _ in range(n_waves):
        kx, ky = rng.uniform(-1.5, 1.5, 2) * 2 * np.pi
        f += np.cos(kx * xx / w + ky * yy / h + rng.uniform(0, 2 * np.pi))
    return sigma * f / np.sqrt(n_waves / 2)


def _edges(depth, rel_jump):
    d = np.where(depth > 0, depth, 1e3)
    e = np.zeros(d.shape, bool)
    for ax in (0, 1):
        r = np.abs(np.diff(d, axis=ax)) / np.minimum(d.take(range(1, d.shape[ax]), axis=ax),
                                                        d.take(range(0, d.shape[ax] - 1), axis=ax))
        j = r > rel_jump
        if ax == 0:
            e[1:] |= j
            e[:-1] |= j
        else:
            e[:, 1:] |= j
            e[:, :-1] |= j
    return e


def render_depth(room, T_wc, K, w, h, extra_solids=()):
    """True metric depth (m, 0 = no hit) and hit ids for one camera. Hit ids index
    room.solids + extra_solids (-1 none, -2 floor, -3 ceiling)."""
    dirs_c, _, _ = pixel_rays_cam(K, w, h)
    R, p = T_wc[:3, :3], T_wc[:3, 3]
    dirs_w = dirs_c @ R.T
    allsol = list(room.solids) + list(extra_solids)
    near_idx = [i for i, s in enumerate(allsol) if np.hypot(s.cx - p[0], s.cy - p[1]) < 12.0 + max(s.l, s.w)]
    t, hid = raycast_solids(p, dirs_w, [allsol[i] for i in near_idx], floor=True, ceiling_z=2.5, max_t=25.0)
    lut = np.asarray(near_idx + [-1], np.int32)
    hid = np.where(hid >= 0, lut[np.clip(hid, 0, len(near_idx))], hid)
    depth = np.where(np.isfinite(t), t, 0.0).reshape(h, w)
    return depth, hid.reshape(h, w)


def render_capture(room, out_dir: str, tier: Literal["T1_LIDAR", "T2_VIO_MONO"] = "T1_LIDAR", seed: int = 0,
                   noise: dict | None = None, max_frames: int | None = None, transients=(),
                   no_depth_ids=()) -> str:
    """transients: (Solid, k0, k1) items present only in keyframes k0 <= k < k1 (people, carts moving
    through the scan). no_depth_ids: ids of solids that return no depth and no feature points
    (glass, mirrors, black surfaces): the sensor sees nothing where they are hit."""
    t_start = time.perf_counter()
    nz = {**DEFAULT_NOISE, **(noise or {})}
    rng = np.random.default_rng(10_000 + seed)
    out = Path(out_dir)
    (out / "frames").mkdir(parents=True, exist_ok=True)
    if tier == "T1_LIDAR":
        (out / "depth").mkdir(exist_ok=True)
        (out / "confidence").mkdir(exist_ok=True)
    else:
        (out / "reldepth").mkdir(exist_ok=True)

    keys, arcs = plan_trajectory(room, rng)
    if max_frames:
        keys, arcs = keys[:max_frames], arcs[:max_frames]
    n = len(keys)
    dt, dyaw, G = _drift(arcs, rng, nz)
    limited = _limited_mask(n, rng, nz)
    K_img = image_K()
    K_d = K_scaled(K_img, DEPTH_W / IMG_W, DEPTH_H / IMG_H)
    dirs_c, _, _ = pixel_rays_cam(K_d, DEPTH_W, DEPTH_H)
    n_sol = len(room.solids)
    blind = [i for i, s in enumerate(room.solids) if s.id in set(no_depth_ids)]
    blind += [n_sol + j for j, (s, _, _) in enumerate(transients) if s.id in set(no_depth_ids)]

    poses, fps_lines = [], []
    shade_lut = rng.uniform(70, 200, len(room.solids) + 3)
    for k, T in enumerate(keys):
        name = f"{k:06d}"
        extra = [s_ if k0 <= k < k1 else None for (s_, k0, k1) in transients]
        depth, hid = render_depth(room, T, K_d, DEPTH_W, DEPTH_H,
                                  [e for e in extra if e is not None])
        if transients:  # map compacted extra indices back to their transient slot
            slots = [j for j, e in enumerate(extra) if e is not None]
            ex = hid >= n_sol
            hid[ex] = n_sol + np.asarray(slots, np.int32)[hid[ex] - n_sol]
        if blind:
            depth = np.where(np.isin(hid, blind), 0.0, depth)
        R_est = rot_z(dyaw[k]) @ T[:3, :3]
        p_est = T[:3, 3] + dt[k]
        if limited[k]:
            R_est = rot_z(np.deg2rad(rng.normal(0, 1.5))) @ R_est
            p_est = p_est + rng.normal(0, 0.03, 3)
        T_est = G @ make_T(R_est, p_est)

        # grey frame (image resolution), shaded by surface id and distance
        sh = shade_lut[np.where(hid >= n_sol, -1, np.where(hid >= 0, hid, hid + len(shade_lut)))].astype(np.float32)
        sh = sh * np.clip(1.2 - 0.1 * depth, 0.4, 1.2) * (depth > 0)
        img = cv2.resize(np.clip(sh, 0, 255).astype(np.uint8), (IMG_W, IMG_H), interpolation=cv2.INTER_LINEAR)
        imwrite(out / "frames" / f"{name}.jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])

        valid = depth > 0
        edges = _edges(depth, nz["edge_rel_jump"])
        if tier == "T1_LIDAR":
            field = 1 + _smooth_field(DEPTH_H, DEPTH_W, rng, nz["lidar_field_sigma"], 3)
            dn = depth * field * (1 + rng.normal(0, nz["lidar_rel_sigma"], depth.shape))
            conf = np.full(depth.shape, 2, np.uint8)
            conf[depth > 3.5] = 1
            # flying pixels at discontinuities: blend with the 3x3 max, medium/low confidence
            dmax = cv2.dilate(depth.astype(np.float32), np.ones((3, 3), np.uint8))
            mix = rng.uniform(0, 1, depth.shape)
            fly = edges & (rng.uniform(0, 1, depth.shape) >= nz["edge_drop_p"])
            dn = np.where(fly, depth * mix + dmax * (1 - mix), dn)
            conf[edges] = np.where(fly[edges], 1, 0)
            drop = (depth > LIDAR_MAX_M) | ~valid | (rng.uniform(0, 1, depth.shape) < 0.01)
            conf[drop] = 0
            dn[drop] = 0
            imwrite(out / "depth" / f"{name}.png", np.clip(dn * 1000, 0, 65535).astype(np.uint16))
            imwrite(out / "confidence" / f"{name}.png", conf)
        else:
            inv = np.where(valid, 1.0 / np.maximum(depth, 0.05), 0.0)
            inv = inv * (1 + _smooth_field(DEPTH_H, DEPTH_W, rng, nz["rel_shape_sigma"], 4))
            if nz["rel_blur_px"] > 0:
                inv = cv2.GaussianBlur(inv.astype(np.float32), (0, 0), nz["rel_blur_px"]).astype(np.float64)
            inv = inv * (1 + rng.normal(0, nz["rel_pixel_sigma"], inv.shape))
            a, b = rng.uniform(0.5, 3.0), rng.uniform(-0.3, 0.3)
            rel = a * inv + b
            lo, hi = rel.min(), rel.max()
            q = np.round((rel - lo) / max(hi - lo, 1e-9) * 65535).astype(np.uint16)
            imwrite(out / "reldepth" / f"{name}.png", q)

        # sparse metric feature points (ARKit rawFeaturePoints), in the tracked (drifting) world frame
        cand = np.flatnonzero(valid.ravel() & (depth.ravel() < 6.0))
        if cand.size:
            m = int(rng.integers(nz["fp_min"], nz["fp_max"] + 1))
            sel = rng.choice(cand, size=min(m, cand.size), replace=False)
            dsel = depth.ravel()[sel].copy()
            out_m = rng.uniform(0, 1, sel.size) < nz["fp_outlier_frac"]
            dsel[out_m] *= rng.uniform(0.6, 1.6, out_m.sum())
            pc = dirs_c[sel] * dsel[:, None]
            pw = pc @ T_est[:3, :3].T + T_est[:3, 3] + rng.normal(0, nz["fp_sigma_m"], pc.shape)
            fps_lines.append(json.dumps({"frame": f"{name}.jpg", "points_world": np.round(pw, 4).tolist()}))

        poses.append(PoseRecord(t=round(float(arcs[k] / WALK_MPS + 0.05 * k), 4), frame=f"{name}.jpg",
                                T_world_cam=[float(v) for v in T_est.ravel()], K=list(K_img), w=IMG_W, h=IMG_H,
                                tracking="limited" if limited[k] else "normal"))

    (out / "poses.jsonl").write_text("\n".join(p.model_dump_json() for p in poses) + "\n", encoding="utf-8")
    (out / "feature_points.jsonl").write_text("\n".join(fps_lines) + "\n", encoding="utf-8")
    files = CaptureFiles(frames="frames", poses="poses.jsonl", feature_points="feature_points.jsonl",
                         depth="depth" if tier == "T1_LIDAR" else None,
                         confidence="confidence" if tier == "T1_LIDAR" else None)
    cap = Capture(capture_id=f"cap_ref_{room.kind}_s{room.seed}_{tier}_{seed}", site_id=room.twin.site_id,
                  device=Device(model="reference-renderer", os="", tier=tier), role="scanner", files=files,
                  duration_s=round(float(poses[-1].t), 2) if poses else 0.0)
    cj = cap.model_dump(mode="json")
    if tier != "T1_LIDAR":
        cj["files"]["relative_depth"] = "reldepth"  # precomputed relative depth (see recon.depth)
    (out / "capture.json").write_text(json.dumps(cj, indent=2), encoding="utf-8")
    (out / "render_meta.json").write_text(json.dumps({
        "room": room.kind, "room_seed": room.seed, "render_seed": seed, "tier": tier, "n_frames": n,
        "path_length_m": round(float(arcs[-1]), 2) if n else 0.0, "n_limited": int(limited.sum()),
        "noise": nz, "final_drift_m": round(float(np.linalg.norm(dt[-1])), 4) if n else 0.0,
        "K_image": list(K_img), "depth_size": [DEPTH_W, DEPTH_H],
        "render_s": round(time.perf_counter() - t_start, 2)}, indent=2), encoding="utf-8")
    return str(out)
