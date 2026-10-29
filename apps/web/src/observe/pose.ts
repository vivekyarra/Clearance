/**
 * Marker-based camera pose for fixed observers. Site frame: origin at marker center, Z up,
 * marker lying flat on the floor with its image "up" direction along site +Y.
 * Output T_site_cam is row-major 4x4 in the ARKit camera convention used by PoseRecord
 * (x right, y up, camera looks along -Z), converted from OpenCV's (x right, y down, z forward).
 */
export type Vec3 = [number, number, number];
export type Mat3 = [number, number, number, number, number, number, number, number, number];

/** Object points in ArUco corner order (TL, TR, BR, BL) = the order SOLVEPNP_IPPE_SQUARE expects. */
export function markerObjectPoints(size: number): Vec3[] {
  const h = size / 2;
  return [
    [-h, h, 0],
    [h, h, 0],
    [h, -h, 0],
    [-h, -h, 0],
  ];
}

export function rodrigues(r: Vec3): Mat3 {
  const th = Math.hypot(r[0], r[1], r[2]);
  if (th < 1e-12) return [1, 0, 0, 0, 1, 0, 0, 0, 1];
  const [x, y, z] = [r[0] / th, r[1] / th, r[2] / th];
  const c = Math.cos(th);
  const s = Math.sin(th);
  const C = 1 - c;
  return [c + x * x * C, x * y * C - z * s, x * z * C + y * s, y * x * C + z * s, c + y * y * C, y * z * C - x * s, z * x * C - y * s, z * y * C + x * s, c + z * z * C];
}

/** rvec/tvec from solvePnP map site -> OpenCV camera: X_cv = R X_site + t. */
export function siteFromCamera(rvec: Vec3, tvec: Vec3): number[] {
  const R = rodrigues(rvec);
  // R^T
  const Rt = [R[0], R[3], R[6], R[1], R[4], R[7], R[2], R[5], R[8]];
  const c: Vec3 = [-(Rt[0] * tvec[0] + Rt[1] * tvec[1] + Rt[2] * tvec[2]), -(Rt[3] * tvec[0] + Rt[4] * tvec[1] + Rt[5] * tvec[2]), -(Rt[6] * tvec[0] + Rt[7] * tvec[1] + Rt[8] * tvec[2])];
  // T_site_ark = T_site_cv * diag(1, -1, -1, 1): negate the camera y and z columns
  return [Rt[0], -Rt[1], -Rt[2], c[0], Rt[3], -Rt[4], -Rt[5], c[1], Rt[6], -Rt[7], -Rt[8], c[2], 0, 0, 0, 1];
}

export function cameraPosition(T: number[]): Vec3 {
  return [T[3], T[7], T[11]];
}

/** Angle (rad) between the rotation parts of two row-major 4x4 poses. */
export function rotationDelta(a: number[], b: number[]): number {
  // trace(Ra^T Rb)
  let tr = 0;
  for (let i = 0; i < 3; i++) for (let k = 0; k < 3; k++) tr += a[k * 4 + i] * b[k * 4 + i];
  return Math.acos(Math.max(-1, Math.min(1, (tr - 1) / 2)));
}

/** Pinhole intrinsics when the browser cannot report them: assumes a horizontal field of view. */
export function intrinsicsFromFov(w: number, h: number, hfovDeg: number): [number, number, number, number] {
  const f = w / 2 / Math.tan(((hfovDeg * Math.PI) / 180) / 2);
  return [f, f, w / 2, h / 2];
}

export interface StableResult {
  stable: boolean;
  heldS: number;
  pose: number[] | null;
}

/**
 * Lock rule: the marker must be detected continuously for `holdS` seconds with every pose within
 * `maxMoveM` / `maxRotRad` of the window mean. The locked pose averages the window translations.
 */
export class PoseStabilizer {
  private win: { t: number; T: number[] }[] = [];
  constructor(
    public holdS = 2,
    public maxMoveM = 0.02,
    public maxRotRad = (1.5 * Math.PI) / 180,
    public maxGapS = 0.5,
  ) {}
  reset() {
    this.win = [];
  }
  /** Feed a detection (T) or a miss (null) at time t (seconds). */
  push(t: number, T: number[] | null): StableResult {
    if (!T) {
      const last = this.win[this.win.length - 1];
      if (!last || t - last.t > this.maxGapS) this.win = [];
      return this.result(t);
    }
    const last = this.win[this.win.length - 1];
    if (last && t - last.t > this.maxGapS) this.win = [];
    this.win.push({ t, T });
    // drop samples that disagree with the newest one: the phone moved
    const p = cameraPosition(T);
    const keepFrom = this.win.findIndex((s) => {
      const q = cameraPosition(s.T);
      return Math.hypot(q[0] - p[0], q[1] - p[1], q[2] - p[2]) <= this.maxMoveM && rotationDelta(s.T, T) <= this.maxRotRad;
    });
    this.win = this.win.slice(Math.max(0, keepFrom));
    for (let i = 0; i < this.win.length; i++) {
      const s = this.win[i];
      const q = cameraPosition(s.T);
      if (Math.hypot(q[0] - p[0], q[1] - p[1], q[2] - p[2]) > this.maxMoveM || rotationDelta(s.T, T) > this.maxRotRad) {
        this.win = this.win.slice(i + 1);
        i = -1;
      }
    }
    return this.result(t);
  }
  private result(t: number): StableResult {
    if (!this.win.length) return { stable: false, heldS: 0, pose: null };
    const heldS = Math.min(t, this.win[this.win.length - 1].t) - this.win[0].t;
    if (heldS + 1e-9 < this.holdS) return { stable: false, heldS, pose: null };
    const n = this.win.length;
    const mean = [0, 0, 0];
    for (const s of this.win) {
      mean[0] += s.T[3] / n;
      mean[1] += s.T[7] / n;
      mean[2] += s.T[11] / n;
    }
    const T = this.win[n - 1].T.slice();
    T[3] = mean[0];
    T[7] = mean[1];
    T[11] = mean[2];
    return { stable: true, heldS, pose: T };
  }
}
