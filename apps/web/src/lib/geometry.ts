import type { Primitive, XY } from "../types";

export const rot = (x: number, y: number, a: number): XY => {
  const c = Math.cos(a);
  const s = Math.sin(a);
  return [x * c - y * s, x * s + y * c];
};

/** Box footprint corners in world frame (l along yaw, w across). CCW from rear-right. */
export function boxCorners(cx: number, cy: number, l: number, w: number, yaw: number): XY[] {
  const hl = l / 2;
  const hw = w / 2;
  return [
    [-hl, -hw],
    [hl, -hw],
    [hl, hw],
    [-hl, hw],
  ].map(([x, y]) => {
    const [rx, ry] = rot(x, y, yaw);
    return [cx + rx, cy + ry] as XY;
  });
}

export function primitiveFootprint(p: Primitive, segments = 20): XY[] {
  const [x, y] = p.pose.xyz;
  const yaw = p.pose.yaw ?? 0;
  if (p.type === "cylinder") {
    const r = p.size[0] / 2;
    const pts: XY[] = [];
    for (let i = 0; i < segments; i++) {
      const a = (i / segments) * Math.PI * 2;
      pts.push([x + r * Math.cos(a), y + r * Math.sin(a)]);
    }
    return pts;
  }
  return boxCorners(x, y, p.size[0], p.size[1], yaw);
}

/** Transform a robot footprint (centered on rotation center, +x forward) to world. */
export function placeFootprint(poly: XY[], x: number, y: number, yaw: number): XY[] {
  return poly.map(([px, py]) => {
    const [rx, ry] = rot(px, py, yaw);
    return [x + rx, y + ry] as XY;
  });
}

export function pointInPolygon(pt: XY, poly: XY[]): boolean {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i];
    const [xj, yj] = poly[j];
    if (yi > pt[1] !== yj > pt[1] && pt[0] < ((xj - xi) * (pt[1] - yi)) / (yj - yi + 1e-12) + xi) inside = !inside;
  }
  return inside;
}

/** Point is inside primitive footprint (exact for box and cylinder). */
export function hitPrimitive(p: Primitive, pt: XY, pad = 0): boolean {
  const [x, y] = p.pose.xyz;
  if (p.type === "cylinder") return Math.hypot(pt[0] - x, pt[1] - y) <= p.size[0] / 2 + pad;
  const [lx, ly] = rot(pt[0] - x, pt[1] - y, -(p.pose.yaw ?? 0));
  return Math.abs(lx) <= p.size[0] / 2 + pad && Math.abs(ly) <= p.size[1] / 2 + pad;
}

export function distToSegment(p: XY, a: XY, b: XY): number {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const L2 = dx * dx + dy * dy;
  let t = L2 > 0 ? ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2 : 0;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy));
}

export function polygonArea(poly: XY[]): number {
  let a = 0;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) a += (poly[j][0] + poly[i][0]) * (poly[j][1] - poly[i][1]);
  return Math.abs(a / 2);
}

export function centroid(poly: XY[]): XY {
  let x = 0;
  let y = 0;
  for (const p of poly) {
    x += p[0];
    y += p[1];
  }
  return [x / Math.max(1, poly.length), y / Math.max(1, poly.length)];
}

export function normAngle(a: number): number {
  let r = a % (Math.PI * 2);
  if (r > Math.PI) r -= Math.PI * 2;
  if (r < -Math.PI) r += Math.PI * 2;
  return r;
}

export const deg = (r: number) => (r * 180) / Math.PI;
export const rad = (d: number) => (d * Math.PI) / 180;

/** Default robot footprint when a spec has none: a clearly-approximate 0.5 m square. */
export function robotFootprint(poly: XY[] | null | undefined): { poly: XY[]; approx: boolean } {
  if (poly && poly.length >= 3) return { poly, approx: false };
  return {
    poly: [
      [-0.25, -0.25],
      [0.25, -0.25],
      [0.25, 0.25],
      [-0.25, 0.25],
    ],
    approx: true,
  };
}
