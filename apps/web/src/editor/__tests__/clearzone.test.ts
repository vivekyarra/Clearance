import { describe, expect, it } from "vitest";
import { canUndo, editorReducer, findEntity, initialEditorState, type EditorAction, type EditorState } from "../reducer";
import { CLEAR_ZONE_REASON, TOOLS } from "../tools";
import { applyClearZones, cheapestPath, clearZoneMask, defaultRoute, unseenOnRoute } from "../unseen";
import { FREE, OCC, UNKNOWN, type OccGrid } from "../../lib/grid";
import type { ClearZone, Twin } from "../../types";

const twin: Twin = { twin_id: "t", site_id: "s", version: 1, occupancy: { uri: "occ.png" }, keepout_zones: [{ id: "K1", polygon: [[0, 0], [1, 0], [1, 1]] }] };
const zone = (id: string, x0 = 1, y0 = 1, x1 = 2, y1 = 2): ClearZone => ({ id, polygon: [[x0, y0], [x1, y0], [x1, y1], [x0, y1]], reason: CLEAR_ZONE_REASON });
const run = (actions: EditorAction[], s: EditorState = editorReducer(initialEditorState, { type: "load", twin })) => actions.reduce(editorReducer, s);
const add = (z: ClearZone): EditorAction => ({ type: "edit", op: { op: "add", kind: "clear", item: z }, select: { kind: "clear", id: z.id } });

/** 0.1 m grid, 40 x 10 cells: free corridor with an unseen band across x in [1.8, 2.2). */
function corridor(): OccGrid {
  const width = 40;
  const height = 10;
  const data = new Uint8Array(width * height).fill(FREE);
  for (let r = 0; r < height; r++) {
    data[r * width] = OCC;
    data[r * width + width - 1] = OCC;
    for (let c = 18; c < 22; c++) data[r * width + c] = UNKNOWN;
  }
  for (let c = 0; c < width; c++) {
    data[c] = OCC;
    data[(height - 1) * width + c] = OCC;
  }
  data[5 * width + 20] = OCC; // a measured obstacle inside the unseen band
  return { data, width, height, res: 0.1, origin: [0, 0] };
}

describe("confirm floor tool", () => {
  it("is on the rail with shortcut F", () => {
    const t = TOOLS.find((x) => x.id === "clear")!;
    expect(t.key).toBe("F");
    expect(t.icon).toBeTruthy();
    expect(TOOLS.filter((x) => x.key === "F")).toHaveLength(1);
  });
  it("adds a clear zone to twin.clear_zones and selects it", () => {
    const s = run([add(zone("C1"))]);
    expect(s.twin!.clear_zones).toHaveLength(1);
    expect(s.twin!.keepout_zones).toHaveLength(1);
    expect(s.selection).toEqual({ kind: "clear", id: "C1" });
    expect(findEntity(s.twin, "clear", "C1")!.reason).toBe(CLEAR_ZONE_REASON);
  });
  it("undo and redo the zone; duplicate ids are a no-op", () => {
    const s1 = run([add(zone("C1")), add(zone("C1", 3, 3, 4, 4))]);
    expect(s1.past).toHaveLength(1);
    const s2 = run([{ type: "undo" }], s1);
    expect(s2.twin).toBe(twin);
    expect(s2.selection).toBeNull();
    const s3 = run([{ type: "redo" }], s2);
    expect(s3.twin!.clear_zones![0].id).toBe("C1");
  });
  it("moving a zone vertex is one gesture; delete removes it", () => {
    let s = run([add(zone("C1")), { type: "gestureStart" }]);
    for (const x of [2.1, 2.2, 2.3]) s = run([{ type: "edit", op: { op: "update", kind: "clear", id: "C1", patch: { polygon: [[1, 1], [x, 1], [x, 2], [1, 2]] } }, transient: true }], s);
    s = run([{ type: "gestureEnd" }], s);
    expect(s.past).toHaveLength(2);
    expect(s.twin!.clear_zones![0].polygon[1]).toEqual([2.3, 1]);
    s = run([{ type: "edit", op: { op: "delete", kind: "clear", id: "C1" }, select: null }], s);
    expect(s.twin!.clear_zones).toHaveLength(0);
    expect(canUndo(s)).toBe(true);
    expect(run([{ type: "undo" }], s).twin!.clear_zones).toHaveLength(1);
  });
});

describe("clear zones on the grid", () => {
  it("open unseen cells only inside the zone and never clear measured obstacles", () => {
    const g = corridor();
    const out = applyClearZones(g, [zone("C1", 1.5, 0, 2.5, 1)]);
    const m = clearZoneMask(g, [zone("C1", 1.5, 0, 2.5, 1)]);
    for (let i = 0; i < g.data.length; i++) {
      if (g.data[i] === OCC) expect(out.data[i]).toBe(OCC);
      else if (g.data[i] === UNKNOWN) expect(out.data[i]).toBe(m[i] ? FREE : UNKNOWN);
      else expect(out.data[i]).toBe(FREE);
    }
    expect(g.data.some((v, i) => v === UNKNOWN && out.data[i] === FREE)).toBe(true);
    expect(out.data[5 * 40 + 20]).toBe(OCC);
  });
  it("no zones returns the same grid", () => {
    const g = corridor();
    expect(applyClearZones(g, [])).toBe(g);
  });
  it("cheapestPath crosses the band; unseenOnRoute counts the patch until a zone covers it", () => {
    const g = corridor();
    const a = 5 * 40 + 2;
    const b = 5 * 40 + 37;
    expect(cheapestPath(g, a, b)).not.toBeNull();
    const t: Twin = { ...twin, waypoints: [{ id: "PICKUP", kind: "pickup", xy: [0.25, 0.45] }, { id: "TBL1", kind: "dest", xy: [3.75, 0.45] }] };
    expect(defaultRoute(t)).toEqual(["PICKUP", "TBL1"]);
    expect(unseenOnRoute(t, g)).toHaveLength(1);
    const covered = { ...t, clear_zones: [zone("C1", 1.7, 0.05, 2.3, 0.95)] };
    expect(unseenOnRoute(covered, g)).toHaveLength(0);
    expect(unseenOnRoute(twin, g)).toBeNull();
  });
});
