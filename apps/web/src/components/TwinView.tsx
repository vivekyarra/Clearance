import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  type Palette,
  type View,
  drawGrid,
  drawHazard,
  drawHeat,
  drawKeepout,
  drawMetricGrid,
  drawPrimitive,
  drawScaleBar,
  drawThreshold,
  drawWaypoint,
  fitView,
  heatCanvas,
  readPalette,
  s2w,
  w2s,
  withAlpha,
  zoomAt,
} from "../lib/draw";
import type { MeasurementTarget } from "../lib/facts";
import type { OccGrid } from "../lib/grid";
import { useThemeTick } from "../hooks/useTheme";
import type { Primitive, Twin, XY } from "../types";
import { Icon } from "./Icon";

export interface TwinViewProps {
  twin: Twin;
  grid: OccGrid;
  height?: number | string;
  heat?: Float32Array | null;
  heatMax?: number;
  highlight?: MeasurementTarget | null;
  highlightObjects?: string[];
  /** Twin primitives replaced by these (e.g. "after" layout); originals drawn as ghosts. */
  movedPrimitives?: Primitive[] | null;
  showWaypoints?: boolean;
  showHazards?: boolean;
  dimGrid?: boolean;
  /** extra drawing on top; return true to keep animating */
  overlay?: (ctx: CanvasRenderingContext2D, v: View, pal: Palette, t: number) => boolean | void;
  animate?: boolean;
  focus?: XY | null;
  legend?: React.ReactNode;
  onWorldClick?: (xy: XY) => void;
  className?: string;
}

export function TwinView(p: TwinViewProps) {
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [view, setView] = useState<View | null>(null);
  const themeTick = useThemeTick();
  const pal = useMemo(() => readPalette(), [themeTick]);
  const heatC = useMemo(() => (p.heat ? heatCanvas(p.grid, p.heat, p.heatMax) : null), [p.heat, p.grid, p.heatMax]);
  const drag = useRef<{ x: number; y: number; v: View; moved: boolean } | null>(null);
  const viewRef = useRef<View | null>(null);
  viewRef.current = view;

  useEffect(() => {
    const el = wrap.current!;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const fit = useCallback(() => {
    if (!size.w) return;
    const b = p.twin.floor?.bounds_polygon;
    const bbox = b && b.length > 2 ? { minX: Math.min(...b.map((q) => q[0])) - 0.4, maxX: Math.max(...b.map((q) => q[0])) + 0.4, minY: Math.min(...b.map((q) => q[1])) - 0.4, maxY: Math.max(...b.map((q) => q[1])) + 0.4 } : undefined;
    setView(fitView(p.grid, size.w, size.h, 12, bbox));
  }, [size.w, size.h, p.grid, p.twin.floor]);

  useEffect(() => {
    if (!view) fit();
  }, [fit, view]);
  useEffect(() => {
    if (size.w) fit();
    // refit on resize
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [size.w, size.h, p.grid]);

  useEffect(() => {
    if (!p.focus || !viewRef.current || !size.w) return;
    const v = viewRef.current;
    const target = Math.max(v.scale, (2.2 / p.grid.res) * (size.w / 600));
    const [px, py] = [(p.focus[0] - p.grid.origin[0]) / p.grid.res, p.grid.height - (p.focus[1] - p.grid.origin[1]) / p.grid.res];
    setView({ scale: target, tx: size.w / 2 - px * target, ty: size.h / 2 - py * target });
  }, [p.focus, p.grid, size.w, size.h]);

  useEffect(() => {
    const c = canvas.current;
    if (!c || !view || !size.w) return;
    const dpr = window.devicePixelRatio || 1;
    c.width = Math.round(size.w * dpr);
    c.height = Math.round(size.h * dpr);
    const ctx = c.getContext("2d")!;
    let raf = 0;
    const t0 = performance.now();
    const frame = () => {
      const t = (performance.now() - t0) / 1000;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.fillStyle = pal.bg;
      ctx.fillRect(0, 0, size.w, size.h);
      drawGrid(ctx, p.grid, view, pal, p.dimGrid ? 0.75 : 1);
      drawMetricGrid(ctx, p.grid, view, size.w, size.h, withAlpha(pal.fg, 0.05));
      if (heatC) drawHeat(ctx, p.grid, view, heatC);
      for (const k of p.twin.keepout_zones ?? []) drawKeepout(ctx, p.grid, view, k, pal);
      const hl = p.highlight;
      const hlIds = new Set(p.highlightObjects ?? []);
      if (hl?.kind === "entity") hlIds.add(hl.id);
      const moved = p.movedPrimitives ? new Map(p.movedPrimitives.map((q) => [q.id, q])) : null;
      for (const prim of p.twin.primitives ?? []) {
        const m = moved?.get(prim.id);
        if (m && (m.pose.xyz[0] !== prim.pose.xyz[0] || m.pose.xyz[1] !== prim.pose.xyz[1] || m.pose.yaw !== prim.pose.yaw)) {
          drawPrimitive(ctx, p.grid, view, prim, pal, { ghost: true });
          drawPrimitive(ctx, p.grid, view, m, pal, { highlight: true });
          const [ax, ay] = w2s(p.grid, view, prim.pose.xyz[0], prim.pose.xyz[1]);
          const [bx, by] = w2s(p.grid, view, m.pose.xyz[0], m.pose.xyz[1]);
          arrow(ctx, ax, ay, bx, by, pal.green);
        } else drawPrimitive(ctx, p.grid, view, prim, pal, { highlight: hlIds.has(prim.id) });
      }
      const pulse = 0.5 + 0.5 * Math.sin(t * 4);
      for (const th of p.twin.thresholds ?? []) drawThreshold(ctx, p.grid, view, th, pal, hlIds.has(th.id), pulse);
      if (p.showHazards !== false) for (const h of p.twin.hazards ?? []) drawHazard(ctx, p.grid, view, h, pal);
      if (p.showWaypoints !== false) for (const w of p.twin.waypoints ?? []) drawWaypoint(ctx, p.grid, view, w, pal, false, hlIds.has(w.id));
      if (hl?.kind === "point" || hl?.kind === "segment") {
        ctx.save();
        ctx.strokeStyle = pal.green;
        ctx.fillStyle = withAlpha(pal.green, 0.18);
        ctx.lineWidth = 2.5;
        if (hl.kind === "point") {
          const [sx, sy] = w2s(p.grid, view, hl.xy[0], hl.xy[1]);
          ctx.beginPath();
          ctx.arc(sx, sy, 12 + 5 * pulse, 0, Math.PI * 2);
          ctx.fill();
          ctx.stroke();
        } else {
          const [ax, ay] = w2s(p.grid, view, hl.a[0], hl.a[1]);
          const [bx, by] = w2s(p.grid, view, hl.b[0], hl.b[1]);
          dimension(ctx, ax, ay, bx, by, pal.green, `${Math.hypot(hl.b[0] - hl.a[0], hl.b[1] - hl.a[1]).toFixed(2)} m`, pal);
        }
        ctx.restore();
      }
      const more = p.overlay?.(ctx, view, pal, t);
      drawScaleBar(ctx, p.grid, view, pal, size.h);
      const flagged = (p.twin.thresholds ?? []).some((x) => x.status === "flagged_manual" && x.height_m == null);
      if (p.animate || more || flagged || hl?.kind === "point") raf = requestAnimationFrame(frame);
    };
    frame();
    return () => cancelAnimationFrame(raf);
  }, [view, size, pal, p.grid, p.twin, heatC, p.highlight, p.highlightObjects, p.movedPrimitives, p.overlay, p.animate, p.showHazards, p.showWaypoints, p.dimGrid]);

  const onWheel = (e: React.WheelEvent) => {
    if (!view) return;
    const r = canvas.current!.getBoundingClientRect();
    setView(zoomAt(view, e.clientX - r.left, e.clientY - r.top, Math.exp(-e.deltaY * 0.0015)));
  };
  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    const stop = (e: WheelEvent) => e.preventDefault();
    c.addEventListener("wheel", stop, { passive: false });
    return () => c.removeEventListener("wheel", stop);
  }, []);

  return (
    <div ref={wrap} className={`mapbox ${p.className ?? ""}`} style={{ height: p.height ?? 360 }}>
      <canvas
        ref={canvas}
        onWheel={onWheel}
        onPointerDown={(e) => {
          if (!view) return;
          (e.target as HTMLElement).setPointerCapture(e.pointerId);
          drag.current = { x: e.clientX, y: e.clientY, v: view, moved: false };
        }}
        onPointerMove={(e) => {
          const d = drag.current;
          if (!d) return;
          const dx = e.clientX - d.x;
          const dy = e.clientY - d.y;
          if (Math.abs(dx) + Math.abs(dy) > 3) d.moved = true;
          if (d.moved) setView({ ...d.v, tx: d.v.tx + dx, ty: d.v.ty + dy });
        }}
        onPointerUp={(e) => {
          const d = drag.current;
          drag.current = null;
          if (d && !d.moved && view && p.onWorldClick) {
            const r = canvas.current!.getBoundingClientRect();
            p.onWorldClick(s2w(p.grid, view, e.clientX - r.left, e.clientY - r.top));
          }
        }}
        style={{ cursor: "grab" }}
      />
      <div className="map-tools no-print">
        <button className="btn sm icon" title="Zoom in" onClick={() => view && setView(zoomAt(view, size.w / 2, size.h / 2, 1.3))}>
          <Icon name="plus" />
        </button>
        <button className="btn sm icon" title="Zoom out" onClick={() => view && setView(zoomAt(view, size.w / 2, size.h / 2, 1 / 1.3))}>
          <Icon name="minus" />
        </button>
        <button className="btn sm icon" title="Fit" onClick={fit}>
          <Icon name="fit" />
        </button>
      </div>
      {p.legend && <div className="map-legend">{p.legend}</div>}
    </div>
  );
}

function arrow(ctx: CanvasRenderingContext2D, ax: number, ay: number, bx: number, by: number, color: string) {
  const a = Math.atan2(by - ay, bx - ax);
  ctx.save();
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(ax, ay);
  ctx.lineTo(bx, by);
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(bx, by);
  ctx.lineTo(bx - 8 * Math.cos(a - 0.45), by - 8 * Math.sin(a - 0.45));
  ctx.lineTo(bx - 8 * Math.cos(a + 0.45), by - 8 * Math.sin(a + 0.45));
  ctx.closePath();
  ctx.fill();
  ctx.restore();
}

export function dimension(ctx: CanvasRenderingContext2D, ax: number, ay: number, bx: number, by: number, color: string, label: string, pal: Palette) {
  const a = Math.atan2(by - ay, bx - ax);
  const nx = -Math.sin(a) * 6;
  const ny = Math.cos(a) * 6;
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(ax, ay);
  ctx.lineTo(bx, by);
  ctx.moveTo(ax - nx, ay - ny);
  ctx.lineTo(ax + nx, ay + ny);
  ctx.moveTo(bx - nx, by - ny);
  ctx.lineTo(bx + nx, by + ny);
  ctx.stroke();
  ctx.font = "700 11.5px Inter, system-ui, sans-serif";
  const tw = ctx.measureText(label).width;
  const mx = (ax + bx) / 2;
  const my = (ay + by) / 2;
  ctx.fillStyle = pal.elev;
  ctx.fillRect(mx - tw / 2 - 5, my - 22, tw + 10, 17);
  ctx.fillStyle = color;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(label, mx, my - 13.5);
  ctx.restore();
}
