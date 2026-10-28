import { useEffect, useRef } from "react";
import { api } from "../api";
import { drawGrid, drawKeepout, drawPrimitive, drawThreshold, drawWaypoint, fitView, readPalette } from "../lib/draw";
import type { OccGrid } from "../lib/grid";
import { useAsync } from "../hooks/useAsync";
import { useThemeTick } from "../hooks/useTheme";
import type { Twin } from "../types";

/** Static, non-interactive twin preview. */
export function TwinThumb({ siteId, twin: given, grid: givenGrid, height = 150 }: { siteId?: string; twin?: Twin; grid?: OccGrid; height?: number }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const tick = useThemeTick();
  const st = useAsync(async () => {
    if (given && givenGrid) return { twin: given, grid: givenGrid };
    const twin = given ?? (await api.getTwin(siteId!));
    if (!twin.occupancy?.width) return { twin, grid: null };
    return { twin, grid: await api.occupancy(twin) };
  }, [siteId, given, givenGrid]);

  useEffect(() => {
    const c = ref.current;
    const d = st.data;
    if (!c || !d?.grid) return;
    const w = c.clientWidth;
    const h = c.clientHeight;
    const dpr = window.devicePixelRatio || 1;
    c.width = w * dpr;
    c.height = h * dpr;
    const ctx = c.getContext("2d")!;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const pal = readPalette();
    ctx.fillStyle = pal.bg;
    ctx.fillRect(0, 0, w, h);
    const b = d.twin.floor?.bounds_polygon;
    const bbox = b && b.length > 2 ? { minX: Math.min(...b.map((q) => q[0])) - 0.3, maxX: Math.max(...b.map((q) => q[0])) + 0.3, minY: Math.min(...b.map((q) => q[1])) - 0.3, maxY: Math.max(...b.map((q) => q[1])) + 0.3 } : undefined;
    const v = fitView(d.grid, w, h, 8, bbox);
    drawGrid(ctx, d.grid, v, pal);
    for (const k of d.twin.keepout_zones ?? []) drawKeepout(ctx, d.grid, v, k, pal);
    for (const p of d.twin.primitives ?? []) drawPrimitive(ctx, d.grid, v, p, pal, { label: false });
    for (const t of d.twin.thresholds ?? []) drawThreshold(ctx, d.grid, v, t, pal, false, 0.5);
    if (h > 200) for (const wp of d.twin.waypoints ?? []) drawWaypoint(ctx, d.grid, v, wp, pal);
  }, [st.data, tick, height]);

  if (st.data && !st.data.grid)
    return (
      <div className="empty small" style={{ height, display: "grid", placeItems: "center" }}>
        Waiting for the first scan
      </div>
    );
  return <canvas ref={ref} style={{ width: "100%", height, display: "block" }} />;
}
