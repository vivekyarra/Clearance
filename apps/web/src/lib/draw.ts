/** Shared 2D drawing for twin views (editor, report, replay, live, coach). */
import { type OccGrid, gridToRGBA, pxToWorld, worldToPx } from "./grid";
import { primitiveFootprint } from "./geometry";
import type { Hazard, KeepoutZone, Primitive, Threshold, Waypoint, XY } from "../types";

export interface View {
  scale: number; // screen px per grid cell
  tx: number;
  ty: number;
}

export type GridMeta = Pick<OccGrid, "height" | "width" | "res" | "origin">;

export function w2s(g: GridMeta, v: View, x: number, y: number): XY {
  const [px, py] = worldToPx(g, x, y);
  return [px * v.scale + v.tx, py * v.scale + v.ty];
}

export function s2w(g: GridMeta, v: View, sx: number, sy: number): XY {
  return pxToWorld(g, (sx - v.tx) / v.scale, (sy - v.ty) / v.scale);
}

/** Fit the grid (or a world bbox) into a w x h viewport with padding. */
export function fitView(g: GridMeta, w: number, h: number, pad = 24, bbox?: { minX: number; minY: number; maxX: number; maxY: number }): View {
  const bx = bbox ?? { minX: g.origin[0], minY: g.origin[1], maxX: g.origin[0] + g.width * g.res, maxY: g.origin[1] + g.height * g.res };
  const [px0, py0] = worldToPx(g, bx.minX, bx.maxY);
  const [px1, py1] = worldToPx(g, bx.maxX, bx.minY);
  const scale = Math.max(0.05, Math.min((w - 2 * pad) / Math.max(1, px1 - px0), (h - 2 * pad) / Math.max(1, py1 - py0)));
  return { scale, tx: (w - (px1 - px0) * scale) / 2 - px0 * scale, ty: (h - (py1 - py0) * scale) / 2 - py0 * scale };
}

export function zoomAt(v: View, sx: number, sy: number, factor: number, min = 0.1, max = 80): View {
  const s = Math.max(min, Math.min(max, v.scale * factor));
  const k = s / v.scale;
  return { scale: s, tx: sx - (sx - v.tx) * k, ty: sy - (sy - v.ty) * k };
}

export interface Palette {
  free: string;
  occ: string;
  unknown: string;
  fg: string;
  muted: string;
  accent: string;
  green: string;
  amber: string;
  red: string;
  violet: string;
  bg: string;
  elev: string;
}

export function readPalette(): Palette {
  const s = getComputedStyle(document.documentElement);
  const g = (n: string) => s.getPropertyValue(n).trim();
  return {
    free: g("--map-free"),
    occ: g("--map-occ"),
    unknown: g("--map-unknown"),
    fg: g("--fg"),
    muted: g("--fg-muted"),
    accent: g("--accent"),
    green: g("--green"),
    amber: g("--amber"),
    red: g("--red"),
    violet: g("--violet"),
    bg: g("--bg-sunk"),
    elev: g("--bg-elev"),
  };
}

function rgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  const v = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  const n = parseInt(v.slice(0, 6), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

const gridCache = new WeakMap<OccGrid, { key: string; canvas: HTMLCanvasElement }>();

export function gridCanvas(g: OccGrid, pal: Palette): HTMLCanvasElement {
  const key = pal.free + pal.occ + pal.unknown;
  const c = gridCache.get(g);
  if (c && c.key === key) return c.canvas;
  const canvas = document.createElement("canvas");
  canvas.width = g.width;
  canvas.height = g.height;
  const ctx = canvas.getContext("2d")!;
  const img = ctx.createImageData(g.width, g.height);
  img.data.set(gridToRGBA(g, { free: rgb(pal.free), occ: rgb(pal.occ), unknown: rgb(pal.unknown) }));
  ctx.putImageData(img, 0, 0);
  gridCache.set(g, { key, canvas });
  return canvas;
}

export function drawGrid(ctx: CanvasRenderingContext2D, g: OccGrid, v: View, pal: Palette, alpha = 1) {
  ctx.save();
  ctx.imageSmoothingEnabled = v.scale < 1;
  ctx.globalAlpha = alpha;
  ctx.drawImage(gridCanvas(g, pal), v.tx, v.ty, g.width * v.scale, g.height * v.scale);
  ctx.restore();
}

/** 1 m scale bar + metric grid lines when zoomed in. */
export function drawScaleBar(ctx: CanvasRenderingContext2D, g: GridMeta, v: View, pal: Palette, h: number) {
  const pxPerM = v.scale / g.res;
  const len = pxPerM >= 60 ? 1 : pxPerM >= 25 ? 2 : 5;
  ctx.save();
  ctx.strokeStyle = pal.fg;
  ctx.fillStyle = pal.fg;
  ctx.lineWidth = 2;
  const x = 14;
  const y = h - 14;
  ctx.beginPath();
  ctx.moveTo(x, y - 5);
  ctx.lineTo(x, y);
  ctx.lineTo(x + len * pxPerM, y);
  ctx.lineTo(x + len * pxPerM, y - 5);
  ctx.stroke();
  ctx.font = "600 11px Inter, system-ui, sans-serif";
  ctx.fillText(`${len} m`, x + 4, y - 7);
  ctx.restore();
}

export function polyPath(ctx: CanvasRenderingContext2D, g: GridMeta, v: View, pts: XY[], close = true) {
  ctx.beginPath();
  pts.forEach(([x, y], i) => {
    const [sx, sy] = w2s(g, v, x, y);
    if (i) ctx.lineTo(sx, sy);
    else ctx.moveTo(sx, sy);
  });
  if (close) ctx.closePath();
}

export interface PrimStyle {
  selected?: boolean;
  lowConfidence?: boolean;
  ghost?: boolean;
  highlight?: boolean;
  label?: boolean;
}

export function drawPrimitive(ctx: CanvasRenderingContext2D, g: GridMeta, v: View, p: Primitive, pal: Palette, st: PrimStyle = {}) {
  const pts = primitiveFootprint(p, 28);
  ctx.save();
  polyPath(ctx, g, v, pts);
  const base = p.movable ? pal.accent : pal.muted;
  ctx.fillStyle = st.ghost ? "transparent" : withAlpha(base, st.selected ? 0.38 : 0.22);
  ctx.fill();
  ctx.lineWidth = st.selected ? 2.2 : 1.3;
  ctx.strokeStyle = st.highlight ? pal.green : st.selected ? pal.accent : base;
  if (st.ghost) ctx.setLineDash([4, 3]);
  ctx.stroke();
  if (st.lowConfidence && !st.ghost) {
    ctx.setLineDash([3, 3]);
    ctx.lineWidth = 2;
    ctx.strokeStyle = pal.amber;
    polyPath(ctx, g, v, primitiveFootprint({ ...p, size: [p.size[0] + 0.08, p.size[1] + 0.08, p.size[2]] }, 28));
    ctx.stroke();
  }
  if (p.fragile && !st.ghost) {
    const [sx, sy] = w2s(g, v, p.pose.xyz[0], p.pose.xyz[1]);
    ctx.fillStyle = pal.red;
    ctx.beginPath();
    ctx.arc(sx + Math.max(6, (p.size[0] / 2 / g.res) * v.scale * 0.6), sy - 6, 3, 0, Math.PI * 2);
    ctx.fill();
  }
  if (st.highlight) {
    ctx.setLineDash([]);
    ctx.lineWidth = 3;
    ctx.strokeStyle = withAlpha(pal.green, 0.6);
    polyPath(ctx, g, v, primitiveFootprint({ ...p, size: [p.size[0] + 0.16, p.size[1] + 0.16, p.size[2]] }, 28));
    ctx.stroke();
  }
  const pxPerM = v.scale / g.res;
  if (st.label !== false && pxPerM > 34 && !st.ghost) {
    const [sx, sy] = w2s(g, v, p.pose.xyz[0], p.pose.xyz[1]);
    ctx.fillStyle = pal.fg;
    ctx.font = "600 10.5px Inter, system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(p.id, sx, sy);
  }
  ctx.restore();
}

export function drawKeepout(ctx: CanvasRenderingContext2D, g: GridMeta, v: View, k: KeepoutZone, pal: Palette, selected = false) {
  if (k.polygon.length < 2) return;
  ctx.save();
  polyPath(ctx, g, v, k.polygon);
  ctx.fillStyle = withAlpha(pal.red, selected ? 0.22 : 0.12);
  ctx.fill();
  ctx.clip();
  ctx.strokeStyle = withAlpha(pal.red, 0.35);
  ctx.lineWidth = 1;
  const [ax, ay] = w2s(g, v, Math.min(...k.polygon.map((p) => p[0])), Math.max(...k.polygon.map((p) => p[1])));
  const [bx, by] = w2s(g, v, Math.max(...k.polygon.map((p) => p[0])), Math.min(...k.polygon.map((p) => p[1])));
  for (let d = -(by - ay); d < bx - ax; d += 8) {
    ctx.beginPath();
    ctx.moveTo(ax + d, by);
    ctx.lineTo(ax + d + (by - ay), ay);
    ctx.stroke();
  }
  ctx.restore();
  ctx.save();
  polyPath(ctx, g, v, k.polygon);
  ctx.lineWidth = selected ? 2.2 : 1.4;
  ctx.strokeStyle = pal.red;
  ctx.setLineDash(selected ? [] : [6, 4]);
  ctx.stroke();
  ctx.restore();
}

export const WAYPOINT_COLOR: Record<Waypoint["kind"], keyof Palette> = {
  dock: "violet",
  pickup: "amber",
  dest: "accent",
  entrance: "green",
  exit: "green",
};

export function drawWaypoint(ctx: CanvasRenderingContext2D, g: GridMeta, v: View, w: Waypoint, pal: Palette, selected = false, highlight = false) {
  const [sx, sy] = w2s(g, v, w.xy[0], w.xy[1]);
  const col = pal[WAYPOINT_COLOR[w.kind]];
  ctx.save();
  if (highlight) {
    ctx.fillStyle = withAlpha(pal.green, 0.25);
    ctx.beginPath();
    ctx.arc(sx, sy, 18, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.translate(sx, sy);
  ctx.fillStyle = col;
  ctx.strokeStyle = selected ? pal.fg : pal.elev;
  ctx.lineWidth = selected ? 2.5 : 2;
  ctx.beginPath();
  ctx.arc(0, 0, 7, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
  const yaw = w.yaw ?? 0;
  ctx.beginPath();
  ctx.moveTo(Math.cos(-yaw) * 13, Math.sin(-yaw) * 13);
  ctx.lineTo(Math.cos(-yaw + 2.6) * 7, Math.sin(-yaw + 2.6) * 7);
  ctx.lineTo(Math.cos(-yaw - 2.6) * 7, Math.sin(-yaw - 2.6) * 7);
  ctx.closePath();
  ctx.fill();
  ctx.font = "650 11px Inter, system-ui, sans-serif";
  ctx.textBaseline = "middle";
  const label = w.id;
  const tw = ctx.measureText(label).width;
  ctx.fillStyle = withAlpha(pal.elev, 0.9);
  ctx.fillRect(10, -17, tw + 8, 16);
  ctx.fillStyle = col;
  ctx.fillText(label, 14, -9);
  ctx.restore();
}

export function drawThreshold(ctx: CanvasRenderingContext2D, g: GridMeta, v: View, t: Threshold, pal: Palette, selected = false, pulse = 0) {
  const [a, b] = t.segment;
  const [ax, ay] = w2s(g, v, a[0], a[1]);
  const [bx, by] = w2s(g, v, b[0], b[1]);
  const flagged = t.status === "flagged_manual" && t.height_m == null;
  const col = flagged ? pal.amber : t.status === "user_entered" ? pal.green : pal.muted;
  ctx.save();
  if (flagged) {
    ctx.strokeStyle = withAlpha(pal.amber, 0.25 + 0.2 * pulse);
    ctx.lineWidth = 14;
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.moveTo(ax, ay);
    ctx.lineTo(bx, by);
    ctx.stroke();
  }
  ctx.strokeStyle = col;
  ctx.lineWidth = selected ? 5 : 3.5;
  ctx.lineCap = "round";
  ctx.setLineDash(flagged ? [6, 4] : []);
  ctx.beginPath();
  ctx.moveTo(ax, ay);
  ctx.lineTo(bx, by);
  ctx.stroke();
  ctx.setLineDash([]);
  const label = `${t.id} ${t.height_m != null ? `${(t.height_m * 100).toFixed(1)} cm` : "measure on site"}`;
  ctx.font = "600 10.5px Inter, system-ui, sans-serif";
  const mx = (ax + bx) / 2;
  const my = (ay + by) / 2;
  const tw = ctx.measureText(label).width;
  ctx.fillStyle = withAlpha(pal.elev, 0.92);
  ctx.fillRect(mx - tw / 2 - 4, my + 8, tw + 8, 15);
  ctx.fillStyle = col;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(label, mx, my + 15.5);
  ctx.restore();
}

export function drawHazard(ctx: CanvasRenderingContext2D, g: GridMeta, v: View, h: Hazard, pal: Palette, selected = false) {
  if (!h.xy) return;
  const [sx, sy] = w2s(g, v, h.xy[0], h.xy[1]);
  ctx.save();
  ctx.translate(sx, sy);
  ctx.beginPath();
  ctx.moveTo(0, -10);
  ctx.lineTo(9, 6);
  ctx.lineTo(-9, 6);
  ctx.closePath();
  ctx.fillStyle = pal.amber;
  ctx.strokeStyle = selected ? pal.fg : pal.elev;
  ctx.lineWidth = selected ? 2.2 : 1.6;
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = "#111";
  ctx.font = "800 10px Inter, system-ui, sans-serif";
  ctx.textAlign = "center";
  ctx.fillText("!", 0, 4);
  ctx.restore();
}

/** Heat ramp: transparent -> amber -> red. */
export function heatCanvas(g: OccGrid, data: Float32Array, max?: number): HTMLCanvasElement {
  const canvas = document.createElement("canvas");
  canvas.width = g.width;
  canvas.height = g.height;
  const ctx = canvas.getContext("2d")!;
  const img = ctx.createImageData(g.width, g.height);
  let m = max ?? 0;
  if (!max) for (let i = 0; i < data.length; i++) if (data[i] > m) m = data[i];
  m = m || 1;
  for (let i = 0; i < data.length; i++) {
    const t = Math.min(1, data[i] / m);
    if (t < 0.04) continue;
    const r = 255;
    const gg = Math.round(196 - 150 * t);
    const b = Math.round(60 - 40 * t);
    img.data[i * 4] = r;
    img.data[i * 4 + 1] = gg;
    img.data[i * 4 + 2] = b;
    img.data[i * 4 + 3] = Math.round(40 + 175 * Math.sqrt(t));
  }
  ctx.putImageData(img, 0, 0);
  return canvas;
}

export function drawHeat(ctx: CanvasRenderingContext2D, g: OccGrid, v: View, heat: HTMLCanvasElement) {
  ctx.save();
  ctx.imageSmoothingEnabled = true;
  ctx.drawImage(heat, v.tx, v.ty, g.width * v.scale, g.height * v.scale);
  ctx.restore();
}

export function withAlpha(color: string, a: number): string {
  if (color.startsWith("#")) {
    const [r, g, b] = rgb(color);
    return `rgba(${r},${g},${b},${a})`;
  }
  return color;
}

export function drawRobot(ctx: CanvasRenderingContext2D, g: GridMeta, v: View, foot: XY[], x: number, y: number, yaw: number, pal: Palette, color?: string) {
  const c = Math.cos(yaw);
  const s = Math.sin(yaw);
  const pts = foot.map(([px, py]) => [x + px * c - py * s, y + px * s + py * c] as XY);
  ctx.save();
  polyPath(ctx, g, v, pts);
  ctx.fillStyle = withAlpha(color ?? pal.accent, 0.85);
  ctx.strokeStyle = pal.elev;
  ctx.lineWidth = 1.5;
  ctx.fill();
  ctx.stroke();
  const [sx, sy] = w2s(g, v, x, y);
  const maxX = Math.max(...foot.map((p) => p[0]));
  const [hx, hy] = w2s(g, v, x + c * maxX * 0.8, y + s * maxX * 0.8);
  ctx.strokeStyle = pal.elev;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(sx, sy);
  ctx.lineTo(hx, hy);
  ctx.stroke();
  ctx.restore();
}

export function drawPed(ctx: CanvasRenderingContext2D, g: GridMeta, v: View, xy: XY, pal: Palette, controlled = false) {
  const [sx, sy] = w2s(g, v, xy[0], xy[1]);
  const r = Math.max(3.5, (0.22 / g.res) * v.scale);
  ctx.save();
  ctx.fillStyle = controlled ? pal.green : withAlpha(pal.fg, 0.7);
  ctx.strokeStyle = pal.elev;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.arc(sx, sy, r, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
  if (controlled) {
    ctx.strokeStyle = pal.green;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(sx, sy, r + 5, 0, Math.PI * 2);
    ctx.stroke();
  }
  ctx.restore();
}

/** Pick a "nice" grid line step in metres for the current zoom. */
export function drawMetricGrid(ctx: CanvasRenderingContext2D, g: GridMeta, v: View, w: number, h: number, color: string) {
  const pxPerM = v.scale / g.res;
  if (pxPerM < 18) return;
  const step = pxPerM > 120 ? 0.25 : pxPerM > 50 ? 0.5 : 1;
  const [x0, y1] = s2w(g, v, 0, 0);
  const [x1, y0] = s2w(g, v, w, h);
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let x = Math.ceil(x0 / step) * step; x < x1; x += step) {
    const [sx] = w2s(g, v, x, 0);
    ctx.moveTo(Math.round(sx) + 0.5, 0);
    ctx.lineTo(Math.round(sx) + 0.5, h);
  }
  for (let y = Math.ceil(y0 / step) * step; y < y1; y += step) {
    const [, sy] = w2s(g, v, 0, y);
    ctx.moveTo(0, Math.round(sy) + 0.5);
    ctx.lineTo(w, Math.round(sy) + 0.5);
  }
  ctx.stroke();
  ctx.restore();
}
