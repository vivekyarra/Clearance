import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState, type Dispatch } from "react";
import {
  type View,
  drawGrid,
  drawHazard,
  drawKeepout,
  drawMetricGrid,
  drawPrimitive,
  drawScaleBar,
  drawThreshold,
  drawWaypoint,
  fitView,
  readPalette,
  s2w,
  w2s,
  withAlpha,
  zoomAt,
} from "../lib/draw";
import { distToSegment, hitPrimitive, pointInPolygon } from "../lib/geometry";
import { FREE, OCC, type OccGrid, cellValue, worldToCell } from "../lib/grid";
import { MAX_CLEAR_ZONE_M2 } from "./unseen";
import { useThemeTick } from "../hooks/useTheme";
import { dimension } from "../components/TwinView";
import type { ClearZone, KeepoutZone, Primitive, Threshold, Waypoint, WaypointKind, XY } from "../types";
import { type EditorAction, type EditorState, type EntityKind, type Selection, findEntity, nextId } from "./reducer";
import { CLEAR_ZONE_REASON, LOW_CONFIDENCE, WAYPOINT_KINDS, primHandles, r3, resizeFromCorner, rotateHandle, snap, type Tool } from "./tools";

export interface Layers {
  occupancy: boolean;
  primitives: boolean;
  keepouts: boolean;
  clearZones: boolean;
  waypoints: boolean;
  thresholds: boolean;
  hazards: boolean;
  lowConfidence: boolean;
  grid: boolean;
}

export const DEFAULT_LAYERS: Layers = { occupancy: true, primitives: true, keepouts: true, clearZones: true, waypoints: true, thresholds: true, hazards: true, lowConfidence: true, grid: true };

export interface EditorCanvasHandle {
  fit(): void;
  zoom(f: number): void;
  focus(xy: XY): void;
}

interface Props {
  state: EditorState;
  grid: OccGrid;
  dispatch: Dispatch<EditorAction>;
  tool: Tool;
  setTool: (t: Tool) => void;
  wpKind: WaypointKind;
  layers: Layers;
  onCursor: (info: { xy: XY | null; cell: string; zoom: number }) => void;
  setHint: (h: string | null) => void;
}

type Drag =
  | { mode: "pan"; sx: number; sy: number; v: View }
  | { mode: "movePrim"; id: string; start: XY; orig: Primitive; moved: boolean }
  | { mode: "rotatePrim"; id: string; orig: Primitive }
  | { mode: "resizePrim"; id: string; idx: number; orig: Primitive }
  | { mode: "moveWp"; id: string; start: XY; orig: Waypoint }
  | { mode: "rotateWp"; id: string; orig: Waypoint }
  | { mode: "moveThr"; id: string; start: XY; orig: Threshold }
  | { mode: "thrEnd"; id: string; end: 0 | 1; orig: Threshold }
  | { mode: "moveKo"; kind: ZoneKind; id: string; start: XY; orig: KeepoutZone | ClearZone }
  | { mode: "koVertex"; kind: ZoneKind; id: string; idx: number; orig: KeepoutZone | ClearZone }
  | { mode: "drawClear"; start: XY; cur: XY }
  | { mode: "moveHaz"; id: string; start: XY }
  | { mode: "drawBox"; start: XY; cur: XY }
  | { mode: "drawThr"; start: XY; cur: XY }
  | { mode: "measure"; start: XY; cur: XY };

const HANDLE_PX = 7;

type ZoneKind = "keepout" | "clear";

/** Operator-confirmed clear floor: dashed green outline, light green tint, "confirmed clear" label. */
function drawClearZone(ctx: CanvasRenderingContext2D, grid: OccGrid, view: View, z: ClearZone, pal: ReturnType<typeof readPalette>, selected: boolean) {
  if (z.polygon.length < 2) return;
  const pts = z.polygon.map((q) => w2s(grid, view, q[0], q[1]));
  ctx.save();
  ctx.beginPath();
  pts.forEach(([sx, sy], i) => (i ? ctx.lineTo(sx, sy) : ctx.moveTo(sx, sy)));
  ctx.closePath();
  ctx.fillStyle = withAlpha(pal.green, selected ? 0.2 : 0.1);
  ctx.fill();
  ctx.strokeStyle = pal.green;
  ctx.lineWidth = selected ? 2.2 : 1.6;
  ctx.setLineDash([6, 4]);
  ctx.stroke();
  ctx.setLineDash([]);
  const cx = pts.reduce((a, q) => a + q[0], 0) / pts.length;
  const top = Math.min(...pts.map((q) => q[1]));
  ctx.font = "600 10px Inter, system-ui, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "bottom";
  ctx.fillStyle = pal.green;
  ctx.fillText("confirmed clear", cx, top - 3);
  ctx.restore();
}

export const EditorCanvas = forwardRef<EditorCanvasHandle, Props>(function EditorCanvas(p, ref) {
  const { state, grid, dispatch, tool, layers } = p;
  const twin = state.twin!;
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [view, setView] = useState<View | null>(null);
  const viewRef = useRef<View | null>(null);
  viewRef.current = view;
  const drag = useRef<Drag | null>(null);
  const [dragTick, setDragTick] = useState(0);
  const [poly, setPoly] = useState<XY[]>([]);
  const [hover, setHover] = useState<XY | null>(null);
  const [measure, setMeasure] = useState<[XY, XY] | null>(null);
  const space = useRef(false);
  const pointers = useRef(new Map<number, XY>());
  const pinch = useRef<{ d: number; v: View; c: XY } | null>(null);
  const themeTick = useThemeTick();
  const pal = useMemo(() => readPalette(), [themeTick]);

  useEffect(() => {
    const el = wrap.current!;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const fit = useCallback(() => {
    if (!size.w) return;
    const b = twin.floor?.bounds_polygon;
    const bbox = b && b.length > 2 ? { minX: Math.min(...b.map((q) => q[0])) - 0.5, maxX: Math.max(...b.map((q) => q[0])) + 0.5, minY: Math.min(...b.map((q) => q[1])) - 0.5, maxY: Math.max(...b.map((q) => q[1])) + 0.5 } : undefined;
    setView(fitView(grid, size.w, size.h, 30, bbox));
  }, [size.w, size.h, grid, twin.floor]);

  useEffect(() => {
    if (!viewRef.current && size.w) fit();
  }, [size.w, fit]);

  useImperativeHandle(
    ref,
    () => ({
      fit,
      zoom: (f) => view && setView(zoomAt(view, size.w / 2, size.h / 2, f)),
      focus: (xy) => {
        if (!view) return;
        const [sx, sy] = w2s(grid, view, xy[0], xy[1]);
        setView({ ...view, tx: view.tx + size.w / 2 - sx, ty: view.ty + size.h / 2 - sy });
      },
    }),
    [fit, view, size, grid],
  );

  // drawing-tool state resets when the tool changes
  useEffect(() => {
    setPoly([]);
    if (tool !== "measure") setMeasure(null);
    const hints: Partial<Record<Tool, string>> = {
      box: "Drag to draw a box. Click for a 0.6 m box.",
      keepout: "Click to add corners. Click the first corner or press Enter to close. Backspace removes the last corner.",
      clear: "Confirm floor you checked on site: drag a box over grey (unseen) floor, or click corners and press Enter. Measured obstacles stay. Up to 4 m² per zone.",
      waypoint: "Click to place the waypoint, then name it in the panel.",
      threshold: "Drag along the door line to add a threshold.",
      measure: "Drag between two points to measure the gap.",
    };
    p.setHint(hints[tool] ?? null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tool]);

  const sel = state.selection;
  const selPrim = sel?.kind === "primitive" ? findEntity(twin, "primitive", sel.id) : undefined;
  const selWp = sel?.kind === "waypoint" ? findEntity(twin, "waypoint", sel.id) : undefined;
  const selThr = sel?.kind === "threshold" ? findEntity(twin, "threshold", sel.id) : undefined;
  const selKo = sel?.kind === "keepout" ? findEntity(twin, "keepout", sel.id) : sel?.kind === "clear" ? findEntity(twin, "clear", sel.id) : undefined;
  const selZoneKind: ZoneKind = sel?.kind === "clear" ? "clear" : "keepout";
  const polyTool = tool === "keepout" || tool === "clear";

  // ------------------------------------------------------------------ render
  useEffect(() => {
    const c = canvas.current;
    if (!c || !view || !size.w) return;
    const dpr = window.devicePixelRatio || 1;
    if (c.width !== Math.round(size.w * dpr)) c.width = Math.round(size.w * dpr);
    if (c.height !== Math.round(size.h * dpr)) c.height = Math.round(size.h * dpr);
    const ctx = c.getContext("2d")!;
    let raf = 0;
    const t0 = performance.now();
    const pxPerM = view.scale / grid.res;
    const frame = () => {
      const t = (performance.now() - t0) / 1000;
      const pulse = 0.5 + 0.5 * Math.sin(t * 4);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.fillStyle = pal.bg;
      ctx.fillRect(0, 0, size.w, size.h);
      if (layers.occupancy) drawGrid(ctx, grid, view, pal);
      if (layers.grid) drawMetricGrid(ctx, grid, view, size.w, size.h, withAlpha(pal.fg, 0.06));
      if (layers.keepouts) for (const k of twin.keepout_zones ?? []) drawKeepout(ctx, grid, view, k, pal, sel?.kind === "keepout" && sel.id === k.id);
      if (layers.clearZones) for (const z of twin.clear_zones ?? []) drawClearZone(ctx, grid, view, z, pal, sel?.kind === "clear" && sel.id === z.id);
      if (layers.primitives)
        for (const pr of twin.primitives ?? []) drawPrimitive(ctx, grid, view, pr, pal, { selected: sel?.kind === "primitive" && sel.id === pr.id, lowConfidence: layers.lowConfidence && (pr.confidence ?? 1) < LOW_CONFIDENCE });
      if (layers.thresholds) for (const th of twin.thresholds ?? []) drawThreshold(ctx, grid, view, th, pal, sel?.kind === "threshold" && sel.id === th.id, pulse);
      if (layers.hazards) (twin.hazards ?? []).forEach((h, i) => drawHazard(ctx, grid, view, h, pal, sel?.kind === "hazard" && sel.id === String(i)));
      if (layers.waypoints) for (const w of twin.waypoints ?? []) drawWaypoint(ctx, grid, view, w, pal, sel?.kind === "waypoint" && sel.id === w.id);

      // selection handles
      ctx.save();
      const handle = (xy: XY, round = false) => {
        const [sx, sy] = w2s(grid, view, xy[0], xy[1]);
        ctx.fillStyle = pal.elev;
        ctx.strokeStyle = pal.accent;
        ctx.lineWidth = 1.8;
        ctx.beginPath();
        if (round) ctx.arc(sx, sy, HANDLE_PX - 1, 0, Math.PI * 2);
        else ctx.rect(sx - HANDLE_PX / 2 - 1, sy - HANDLE_PX / 2 - 1, HANDLE_PX + 2, HANDLE_PX + 2);
        ctx.fill();
        ctx.stroke();
      };
      if (selPrim) {
        const rh = rotateHandle(selPrim, 22 / pxPerM);
        const [cx, cy] = w2s(grid, view, selPrim.pose.xyz[0], selPrim.pose.xyz[1]);
        const [hx, hy] = w2s(grid, view, rh[0], rh[1]);
        ctx.strokeStyle = pal.accent;
        ctx.lineWidth = 1.2;
        ctx.setLineDash([3, 3]);
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(hx, hy);
        ctx.stroke();
        ctx.setLineDash([]);
        for (const h of primHandles(selPrim)) handle(h);
        handle(rh, true);
        // dimensions
        ctx.font = "600 11px Inter, system-ui, sans-serif";
        ctx.fillStyle = pal.accent;
        ctx.textAlign = "center";
        const dims = selPrim.type === "cylinder" ? `⌀ ${selPrim.size[0].toFixed(2)} m` : `${selPrim.size[0].toFixed(2)} × ${selPrim.size[1].toFixed(2)} m`;
        const hs = primHandles(selPrim).map((h) => w2s(grid, view, h[0], h[1]));
        const bottom = Math.max(...hs.map((h) => h[1]));
        ctx.fillText(dims, cx, bottom + 16);
      }
      if (selWp) {
        const yaw = selWp.yaw ?? 0;
        handle([selWp.xy[0] + (Math.cos(yaw) * 26) / pxPerM, selWp.xy[1] + (Math.sin(yaw) * 26) / pxPerM], true);
      }
      if (selThr) for (const e of selThr.segment) handle(e);
      if (selKo) for (const v of selKo.polygon) handle(v);
      ctx.restore();

      // in-progress drawing
      const d = drag.current;
      if (d?.mode === "drawBox" || d?.mode === "drawClear") {
        const tone = d.mode === "drawClear" ? pal.green : pal.accent;
        const pts: XY[] = [d.start, [d.cur[0], d.start[1]], d.cur, [d.start[0], d.cur[1]]];
        ctx.save();
        ctx.beginPath();
        pts.forEach((q, i) => {
          const [sx, sy] = w2s(grid, view, q[0], q[1]);
          if (i) ctx.lineTo(sx, sy);
          else ctx.moveTo(sx, sy);
        });
        ctx.closePath();
        ctx.fillStyle = withAlpha(tone, 0.18);
        ctx.strokeStyle = tone;
        ctx.setLineDash([5, 3]);
        ctx.fill();
        ctx.stroke();
        if (d.mode === "drawClear") {
          const area = Math.abs((d.cur[0] - d.start[0]) * (d.cur[1] - d.start[1]));
          const [lx, ly] = w2s(grid, view, Math.max(d.start[0], d.cur[0]), Math.min(d.start[1], d.cur[1]));
          ctx.setLineDash([]);
          ctx.font = "600 11px Inter, system-ui, sans-serif";
          ctx.fillStyle = area > MAX_CLEAR_ZONE_M2 ? pal.red : pal.green;
          ctx.textAlign = "right";
          ctx.fillText(`${area.toFixed(2)} m²`, lx, ly + 14);
        }
        ctx.restore();
      }
      if (d?.mode === "drawThr" || d?.mode === "measure" || (measure && tool === "measure")) {
        const [a, b] = d?.mode === "drawThr" || d?.mode === "measure" ? [d.start, d.cur] : measure!;
        const [ax, ay] = w2s(grid, view, a[0], a[1]);
        const [bx, by] = w2s(grid, view, b[0], b[1]);
        dimension(ctx, ax, ay, bx, by, d?.mode === "drawThr" ? pal.amber : pal.green, `${Math.hypot(b[0] - a[0], b[1] - a[1]).toFixed(2)} m`, pal);
      }
      if (polyTool && poly.length) {
        const tone = tool === "clear" ? pal.green : pal.red;
        ctx.save();
        ctx.strokeStyle = tone;
        ctx.fillStyle = withAlpha(tone, 0.12);
        ctx.lineWidth = 1.8;
        ctx.setLineDash([5, 3]);
        ctx.beginPath();
        [...poly, ...(hover ? [hover] : [])].forEach((q, i) => {
          const [sx, sy] = w2s(grid, view, q[0], q[1]);
          if (i) ctx.lineTo(sx, sy);
          else ctx.moveTo(sx, sy);
        });
        ctx.fill();
        ctx.stroke();
        ctx.setLineDash([]);
        poly.forEach((q, i) => {
          const [sx, sy] = w2s(grid, view, q[0], q[1]);
          ctx.fillStyle = i === 0 ? tone : pal.elev;
          ctx.beginPath();
          ctx.arc(sx, sy, i === 0 ? 6 : 4, 0, Math.PI * 2);
          ctx.fill();
          ctx.stroke();
        });
        ctx.restore();
      }
      if (tool === "waypoint" && hover) {
        const k = WAYPOINT_KINDS.find((x) => x.kind === p.wpKind)!;
        ctx.globalAlpha = 0.55;
        drawWaypoint(ctx, grid, view, { id: k.label, kind: p.wpKind, xy: hover, yaw: 0 }, pal);
        ctx.globalAlpha = 1;
      }
      drawScaleBar(ctx, grid, view, pal, size.h);
      const anim = (twin.thresholds ?? []).some((x) => x.status === "flagged_manual" && x.height_m == null) && layers.thresholds;
      if (anim) raf = requestAnimationFrame(frame);
    };
    frame();
    return () => cancelAnimationFrame(raf);
  }, [view, size, pal, grid, twin, sel, layers, poly, hover, measure, tool, dragTick, selPrim, selWp, selThr, selKo, polyTool, p.wpKind]);

  // ------------------------------------------------------------------ hit testing
  const toWorld = (e: { clientX: number; clientY: number }): XY => {
    const r = canvas.current!.getBoundingClientRect();
    return s2w(grid, viewRef.current!, e.clientX - r.left, e.clientY - r.top);
  };
  const screenDist = (a: XY, b: XY) => {
    const v = viewRef.current!;
    const [ax, ay] = w2s(grid, v, a[0], a[1]);
    const [bx, by] = w2s(grid, v, b[0], b[1]);
    return Math.hypot(ax - bx, ay - by);
  };
  const tolM = (px: number) => (px / viewRef.current!.scale) * grid.res;

  function hitEntity(pt: XY): Selection {
    if (layers.waypoints) for (const w of [...(twin.waypoints ?? [])].reverse()) if (screenDist(pt, w.xy) < 10) return { kind: "waypoint", id: w.id };
    if (layers.hazards) {
      const hz = twin.hazards ?? [];
      for (let i = hz.length - 1; i >= 0; i--) if (hz[i].xy && screenDist(pt, hz[i].xy!) < 11) return { kind: "hazard", id: String(i) };
    }
    if (layers.thresholds) for (const t of twin.thresholds ?? []) if (distToSegment(pt, t.segment[0], t.segment[1]) < tolM(7)) return { kind: "threshold", id: t.id };
    if (layers.primitives) {
      const ps = twin.primitives ?? [];
      // smallest containing primitive wins (chairs over tables)
      let best: Primitive | null = null;
      for (const pr of ps) if (hitPrimitive(pr, pt, tolM(2)) && (!best || pr.size[0] * pr.size[1] < best.size[0] * best.size[1])) best = pr;
      if (best) return { kind: "primitive", id: best.id };
    }
    if (layers.clearZones) for (const z of twin.clear_zones ?? []) if (pointInPolygon(pt, z.polygon)) return { kind: "clear", id: z.id };
    if (layers.keepouts) for (const k of twin.keepout_zones ?? []) if (pointInPolygon(pt, k.polygon)) return { kind: "keepout", id: k.id };
    return null;
  }

  const edit = (kind: EntityKind, id: string, patch: Record<string, unknown>) => dispatch({ type: "edit", op: { op: "update", kind, id, patch }, transient: true });

  // ------------------------------------------------------------------ pointer handlers
  const onPointerDown = (e: React.PointerEvent) => {
    if (!viewRef.current) return;
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    pointers.current.set(e.pointerId, [e.clientX, e.clientY]);
    if (pointers.current.size === 2) {
      // pinch-zoom: cancel any gesture
      if (drag.current && drag.current.mode !== "pan") dispatch({ type: "gestureCancel" });
      drag.current = null;
      const [a, b] = [...pointers.current.values()];
      const r = canvas.current!.getBoundingClientRect();
      pinch.current = { d: Math.hypot(a[0] - b[0], a[1] - b[1]), v: viewRef.current, c: [(a[0] + b[0]) / 2 - r.left, (a[1] + b[1]) / 2 - r.top] };
      return;
    }
    const pt = toWorld(e);
    if (e.button === 1 || e.button === 2 || tool === "pan" || space.current) {
      drag.current = { mode: "pan", sx: e.clientX, sy: e.clientY, v: viewRef.current };
      return;
    }
    if (tool === "box") {
      drag.current = { mode: "drawBox", start: [snap(pt[0]), snap(pt[1])], cur: [snap(pt[0]), snap(pt[1])] };
      return;
    }
    if (tool === "threshold") {
      drag.current = { mode: "drawThr", start: pt, cur: pt };
      return;
    }
    if (tool === "measure") {
      drag.current = { mode: "measure", start: pt, cur: pt };
      return;
    }
    if (tool === "clear" && !poly.length) {
      drag.current = { mode: "drawClear", start: [snap(pt[0]), snap(pt[1])], cur: [snap(pt[0]), snap(pt[1])] };
      return;
    }
    if (polyTool) {
      if (poly.length >= 3 && screenDist(pt, poly[0]) < 10) closePoly();
      else setPoly((q) => [...q, [r3(pt[0]), r3(pt[1])]]);
      return;
    }
    if (tool === "waypoint") {
      const k = WAYPOINT_KINDS.find((x) => x.kind === p.wpKind)!;
      const ids = (twin.waypoints ?? []).map((w) => w.id);
      const id = k.kind === "dest" || ids.includes(k.prefix) ? nextId(k.prefix, ids) : k.prefix;
      const w: Waypoint = { id, kind: k.kind, xy: [r3(pt[0]), r3(pt[1])], yaw: 0 };
      dispatch({ type: "edit", op: { op: "add", kind: "waypoint", item: w }, select: { kind: "waypoint", id } });
      p.setTool("select");
      return;
    }
    // select tool: handles of current selection first
    if (selPrim) {
      const rh = rotateHandle(selPrim, tolM(22));
      if (screenDist(pt, rh) < HANDLE_PX + 3) return begin({ mode: "rotatePrim", id: selPrim.id, orig: selPrim });
      const hs = primHandles(selPrim);
      for (let i = 0; i < 4; i++) if (screenDist(pt, hs[i]) < HANDLE_PX + 3) return begin({ mode: "resizePrim", id: selPrim.id, idx: i, orig: selPrim });
    }
    if (selWp) {
      const yaw = selWp.yaw ?? 0;
      const hxy: XY = [selWp.xy[0] + Math.cos(yaw) * tolM(26), selWp.xy[1] + Math.sin(yaw) * tolM(26)];
      if (screenDist(pt, hxy) < HANDLE_PX + 3) return begin({ mode: "rotateWp", id: selWp.id, orig: selWp });
    }
    if (selThr) for (const end of [0, 1] as const) if (screenDist(pt, selThr.segment[end]) < HANDLE_PX + 3) return begin({ mode: "thrEnd", id: selThr.id, end, orig: selThr });
    if (selKo) selKo.polygon.forEach((v, i) => {
      if (!drag.current && screenDist(pt, v) < HANDLE_PX + 3) begin({ mode: "koVertex", kind: selZoneKind, id: selKo.id, idx: i, orig: selKo });
    });
    if (drag.current) return;
    const hit = hitEntity(pt);
    dispatch({ type: "select", selection: hit });
    if (!hit) {
      drag.current = { mode: "pan", sx: e.clientX, sy: e.clientY, v: viewRef.current };
      return;
    }
    if (hit.kind === "primitive") begin({ mode: "movePrim", id: hit.id, start: pt, orig: findEntity(twin, "primitive", hit.id)!, moved: false });
    else if (hit.kind === "waypoint") begin({ mode: "moveWp", id: hit.id, start: pt, orig: findEntity(twin, "waypoint", hit.id)! });
    else if (hit.kind === "threshold") begin({ mode: "moveThr", id: hit.id, start: pt, orig: findEntity(twin, "threshold", hit.id)! });
    else if (hit.kind === "keepout") begin({ mode: "moveKo", kind: "keepout", id: hit.id, start: pt, orig: findEntity(twin, "keepout", hit.id)! });
    else if (hit.kind === "clear") begin({ mode: "moveKo", kind: "clear", id: hit.id, start: pt, orig: findEntity(twin, "clear", hit.id)! });
    else if (hit.kind === "hazard") begin({ mode: "moveHaz", id: hit.id, start: pt });
  };

  function begin(d: Drag) {
    dispatch({ type: "gestureStart" });
    drag.current = d;
  }

  function closePoly() {
    const pts = poly.filter((q, i) => i === 0 || Math.hypot(q[0] - poly[i - 1][0], q[1] - poly[i - 1][1]) > 0.01);
    if (pts.length < 3) return;
    if (tool === "clear") addClearZone(pts);
    else {
      const id = nextId("K", (twin.keepout_zones ?? []).map((k) => k.id));
      dispatch({ type: "edit", op: { op: "add", kind: "keepout", item: { id, polygon: pts, reason: "" } }, select: { kind: "keepout", id } });
    }
    setPoly([]);
    p.setTool("select");
  }

  function addClearZone(pts: XY[]) {
    const id = nextId("C", (twin.clear_zones ?? []).map((z) => z.id));
    const item: ClearZone = { id, polygon: pts, reason: CLEAR_ZONE_REASON };
    dispatch({ type: "edit", op: { op: "add", kind: "clear", item }, select: { kind: "clear", id } });
  }

  const onPointerMove = (e: React.PointerEvent) => {
    if (!viewRef.current) return;
    if (pointers.current.has(e.pointerId)) pointers.current.set(e.pointerId, [e.clientX, e.clientY]);
    if (pinch.current && pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
      setView(zoomAt(pinch.current.v, pinch.current.c[0], pinch.current.c[1], d / pinch.current.d));
      return;
    }
    const pt = toWorld(e);
    const [r, c] = worldToCell(grid, pt[0], pt[1]);
    const cv = cellValue(grid, r, c);
    p.onCursor({ xy: pt, cell: cv === FREE ? "free" : cv === OCC ? "occupied" : "unknown", zoom: viewRef.current.scale / grid.res });
    if (polyTool || tool === "waypoint") setHover(pt);
    const d = drag.current;
    if (!d) return;
    const fine = e.altKey;
    const sn = (v: number) => (fine ? r3(v) : snap(v));
    switch (d.mode) {
      case "pan":
        setView({ ...d.v, tx: d.v.tx + e.clientX - d.sx, ty: d.v.ty + e.clientY - d.sy });
        return;
      case "movePrim": {
        const dx = pt[0] - d.start[0];
        const dy = pt[1] - d.start[1];
        if (!d.moved && screenDist(pt, d.start) < 3) return;
        d.moved = true;
        edit("primitive", d.id, { pose: { ...d.orig.pose, xyz: [sn(d.orig.pose.xyz[0] + dx), sn(d.orig.pose.xyz[1] + dy), d.orig.pose.xyz[2]] } });
        return;
      }
      case "rotatePrim": {
        let a = Math.atan2(pt[1] - d.orig.pose.xyz[1], pt[0] - d.orig.pose.xyz[0]) - Math.PI / 2;
        if (!e.shiftKey) a = Math.round(a / (Math.PI / 36)) * (Math.PI / 36);
        else a = Math.round(a / (Math.PI / 12)) * (Math.PI / 12);
        while (a > Math.PI) a -= 2 * Math.PI;
        while (a < -Math.PI) a += 2 * Math.PI;
        edit("primitive", d.id, { pose: { ...d.orig.pose, yaw: Math.round(a * 10000) / 10000 } });
        return;
      }
      case "resizePrim": {
        const next = resizeFromCorner(d.orig, d.idx, fine ? pt : [snap(pt[0]), snap(pt[1])]);
        edit("primitive", d.id, { pose: next.pose, size: next.size });
        return;
      }
      case "moveWp":
        edit("waypoint", d.id, { xy: [sn(d.orig.xy[0] + pt[0] - d.start[0]), sn(d.orig.xy[1] + pt[1] - d.start[1])] });
        return;
      case "rotateWp": {
        let a = Math.atan2(pt[1] - d.orig.xy[1], pt[0] - d.orig.xy[0]);
        if (!e.shiftKey) a = Math.round(a / (Math.PI / 12)) * (Math.PI / 12);
        edit("waypoint", d.id, { yaw: Math.round(a * 10000) / 10000 });
        return;
      }
      case "moveThr": {
        const dx = pt[0] - d.start[0];
        const dy = pt[1] - d.start[1];
        edit("threshold", d.id, { segment: d.orig.segment.map(([x, y]) => [sn(x + dx), sn(y + dy)]) });
        return;
      }
      case "thrEnd": {
        const seg = d.orig.segment.slice() as [XY, XY];
        seg[d.end] = [sn(pt[0]), sn(pt[1])];
        edit("threshold", d.id, { segment: seg });
        return;
      }
      case "moveKo": {
        const dx = pt[0] - d.start[0];
        const dy = pt[1] - d.start[1];
        edit(d.kind, d.id, { polygon: d.orig.polygon.map(([x, y]) => [sn(x + dx), sn(y + dy)]) });
        return;
      }
      case "koVertex": {
        const poly2 = d.orig.polygon.slice();
        poly2[d.idx] = [sn(pt[0]), sn(pt[1])];
        edit(d.kind, d.id, { polygon: poly2 });
        return;
      }
      case "moveHaz":
        edit("hazard", d.id, { xy: [sn(pt[0]), sn(pt[1])] });
        return;
      case "drawBox":
      case "drawClear":
        d.cur = [snap(pt[0]), snap(pt[1])];
        setDragTick((x) => x + 1);
        return;
      case "drawThr":
      case "measure":
        d.cur = pt;
        setDragTick((x) => x + 1);
        return;
    }
  };

  const onPointerUp = (e: React.PointerEvent) => {
    pointers.current.delete(e.pointerId);
    if (pinch.current) {
      if (pointers.current.size < 2) pinch.current = null;
      return;
    }
    const d = drag.current;
    drag.current = null;
    if (!d) return;
    switch (d.mode) {
      case "pan":
        return;
      case "drawBox": {
        const ids = (twin.primitives ?? []).map((q) => q.id);
        const id = nextId("B", ids);
        let l = Math.abs(d.cur[0] - d.start[0]);
        let w = Math.abs(d.cur[1] - d.start[1]);
        let cx = (d.cur[0] + d.start[0]) / 2;
        let cy = (d.cur[1] + d.start[1]) / 2;
        if (l < 0.05 || w < 0.05) {
          l = 0.6;
          w = 0.6;
          cx = d.start[0];
          cy = d.start[1];
        }
        const prim: Primitive = { id, type: "box", label: "box", pose: { xyz: [r3(cx), r3(cy), 0.375], yaw: 0 }, size: [r3(l), r3(w), 0.75], movable: true, fragile: false, confidence: 1, source: "user" };
        dispatch({ type: "edit", op: { op: "add", kind: "primitive", item: prim }, select: { kind: "primitive", id } });
        p.setTool("select");
        return;
      }
      case "drawClear": {
        const [x0, y0] = d.start;
        const [x1, y1] = d.cur;
        if (Math.abs(x1 - x0) < 0.05 || Math.abs(y1 - y0) < 0.05) {
          // a click starts a polygon instead: more corners follow, Enter closes
          setPoly([[r3(x0), r3(y0)]]);
          return;
        }
        const [ax, bx, ay, by] = [r3(Math.min(x0, x1)), r3(Math.max(x0, x1)), r3(Math.min(y0, y1)), r3(Math.max(y0, y1))];
        addClearZone([[ax, ay], [bx, ay], [bx, by], [ax, by]]);
        p.setTool("select");
        return;
      }
      case "drawThr": {
        if (Math.hypot(d.cur[0] - d.start[0], d.cur[1] - d.start[1]) < 0.1) {
          setDragTick((x) => x + 1);
          return;
        }
        const id = nextId("D", (twin.thresholds ?? []).map((t) => t.id));
        const th: Threshold = { id, segment: [[r3(d.start[0]), r3(d.start[1])], [r3(d.cur[0]), r3(d.cur[1])]], height_m: null, status: "flagged_manual" };
        dispatch({ type: "edit", op: { op: "add", kind: "threshold", item: th }, select: { kind: "threshold", id } });
        p.setTool("select");
        return;
      }
      case "measure":
        setMeasure([d.start, d.cur]);
        return;
      default:
        dispatch({ type: "gestureEnd" });
    }
  };

  const onWheel = (e: React.WheelEvent) => {
    if (!viewRef.current) return;
    const r = canvas.current!.getBoundingClientRect();
    const f = e.ctrlKey ? Math.exp(-e.deltaY * 0.01) : Math.exp(-e.deltaY * 0.0015);
    setView(zoomAt(viewRef.current, e.clientX - r.left, e.clientY - r.top, f));
  };

  useEffect(() => {
    const c = canvas.current!;
    const stop = (e: WheelEvent) => e.preventDefault();
    c.addEventListener("wheel", stop, { passive: false });
    const ctx = (e: MouseEvent) => e.preventDefault();
    c.addEventListener("contextmenu", ctx);
    return () => {
      c.removeEventListener("wheel", stop);
      c.removeEventListener("contextmenu", ctx);
    };
  }, []);

  // keyboard: space-pan and polygon drawing keys (global shortcuts live in the page)
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.closest("input,textarea,select")) return;
      if (e.code === "Space") {
        space.current = true;
        e.preventDefault();
      }
      if (polyTool) {
        if (e.key === "Enter") closePoly();
        if (e.key === "Backspace") {
          e.preventDefault();
          e.stopPropagation();
          setPoly((q) => q.slice(0, -1));
        }
        if (e.key === "Escape") setPoly([]);
      }
      if (e.key === "Escape" && drag.current && drag.current.mode !== "pan") {
        drag.current = null;
        dispatch({ type: "gestureCancel" });
        setDragTick((x) => x + 1);
      }
    };
    const up = (e: KeyboardEvent) => {
      if (e.code === "Space") space.current = false;
    };
    window.addEventListener("keydown", down, true);
    window.addEventListener("keyup", up);
    return () => {
      window.removeEventListener("keydown", down, true);
      window.removeEventListener("keyup", up);
    };
  });

  const cursor = tool === "pan" ? "grab" : tool === "select" ? "default" : "crosshair";
  return (
    <div ref={wrap} className="editor-canvas">
      <canvas
        ref={canvas}
        style={{ cursor }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onPointerLeave={() => {
          setHover(null);
          p.onCursor({ xy: null, cell: "", zoom: viewRef.current ? viewRef.current.scale / grid.res : 0 });
        }}
        onDoubleClick={() => polyTool && closePoly()}
        onWheel={onWheel}
      />
    </div>
  );
});

