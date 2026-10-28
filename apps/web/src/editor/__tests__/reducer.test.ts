import { describe, expect, it } from "vitest";
import { HISTORY_LIMIT, applyOp, canRedo, canUndo, editorReducer, initialEditorState, isDirty, nextId, type EditorAction, type EditorState } from "../reducer";
import type { Primitive, Twin } from "../../types";

const prim = (id: string, x = 0): Primitive => ({ id, type: "box", label: "table", pose: { xyz: [x, 0, 0.375], yaw: 0 }, size: [0.8, 0.8, 0.75], movable: true });
const twin: Twin = { twin_id: "t", site_id: "s", version: 1, occupancy: { uri: "occ.png" }, primitives: [prim("T1"), prim("T2", 2)], waypoints: [{ id: "DOCK", kind: "dock", xy: [0, 0] }], hazards: [{ label: "cable" }] };
const run = (actions: EditorAction[], s: EditorState = editorReducer(initialEditorState, { type: "load", twin })) => actions.reduce(editorReducer, s);
const move = (id: string, x: number, transient = false): EditorAction => ({ type: "edit", op: { op: "update", kind: "primitive", id, patch: { pose: { xyz: [x, 0, 0.375], yaw: 0 } } }, transient });

describe("editor reducer", () => {
  it("undo and redo restore exact twins", () => {
    const s1 = run([move("T1", 1)]);
    expect(s1.twin!.primitives![0].pose.xyz[0]).toBe(1);
    expect(isDirty(s1)).toBe(true);
    const s2 = run([{ type: "undo" }], s1);
    expect(s2.twin).toBe(twin);
    expect(isDirty(s2)).toBe(false);
    const s3 = run([{ type: "redo" }], s2);
    expect(s3.twin).toBe(s1.twin);
    expect(canRedo(s3)).toBe(false);
  });
  it("a new edit clears the redo stack", () => {
    const s = run([move("T1", 1), { type: "undo" }, move("T2", 5)]);
    expect(canRedo(s)).toBe(false);
    expect(s.past).toHaveLength(1);
  });
  it("a drag gesture is one history entry", () => {
    const s = run([{ type: "gestureStart" }, move("T1", 0.1, true), move("T1", 0.2, true), move("T1", 0.3, true), { type: "gestureEnd" }]);
    expect(s.past).toHaveLength(1);
    expect(s.twin!.primitives![0].pose.xyz[0]).toBe(0.3);
    expect(run([{ type: "undo" }], s).twin).toBe(twin);
  });
  it("a gesture without movement adds no history; cancel restores", () => {
    expect(run([{ type: "gestureStart" }, { type: "gestureEnd" }]).past).toHaveLength(0);
    const c = run([{ type: "gestureStart" }, move("T1", 3, true), { type: "gestureCancel" }]);
    expect(c.twin).toBe(twin);
    expect(c.past).toHaveLength(0);
  });
  it("undo waits until a gesture ends", () => {
    const s = run([move("T1", 1), { type: "gestureStart" }, move("T1", 2, true)]);
    expect(canUndo(s)).toBe(false);
    expect(run([{ type: "undo" }], s)).toBe(s);
  });
  it("edits that change nothing create no history", () => {
    const s = run([move("T1", 0), { type: "edit", op: { op: "update", kind: "primitive", id: "missing", patch: { label: "x" } } }, { type: "edit", op: { op: "add", kind: "primitive", item: prim("T1") } }]);
    expect(s.past).toHaveLength(0);
    expect(s.twin).toBe(twin);
  });
  it("delete clears the selection; undo brings the entity back", () => {
    let s = run([{ type: "select", selection: { kind: "primitive", id: "T2" } }, { type: "edit", op: { op: "delete", kind: "primitive", id: "T2" } }]);
    expect(s.selection).toBeNull();
    expect(s.twin!.primitives!.map((p) => p.id)).toEqual(["T1"]);
    s = run([{ type: "undo" }, { type: "select", selection: { kind: "primitive", id: "T2" } }], s);
    expect(s.selection).toEqual({ kind: "primitive", id: "T2" });
  });
  it("rename keeps ids unique and follows the selection", () => {
    let s = run([{ type: "select", selection: { kind: "waypoint", id: "DOCK" } }, { type: "edit", op: { op: "rename", kind: "waypoint", id: "DOCK", newId: "DOCK_A" } }]);
    expect(s.selection).toEqual({ kind: "waypoint", id: "DOCK_A" });
    s = run([{ type: "edit", op: { op: "add", kind: "waypoint", item: { id: "PICKUP", kind: "pickup", xy: [1, 1] } } }, { type: "edit", op: { op: "rename", kind: "waypoint", id: "PICKUP", newId: "DOCK_A" } }], s);
    expect(s.twin!.waypoints!.map((w) => w.id)).toEqual(["DOCK_A", "PICKUP"]);
  });
  it("hazards are addressed by index", () => {
    const t = applyOp(twin, { op: "update", kind: "hazard", id: "0", patch: { label: "wet floor" } });
    expect(t.hazards![0].label).toBe("wet floor");
  });
  it("history is capped", () => {
    const acts: EditorAction[] = [];
    for (let i = 1; i <= HISTORY_LIMIT + 25; i++) acts.push(move("T1", i));
    const s = run(acts);
    expect(s.past).toHaveLength(HISTORY_LIMIT);
    expect(s.twin!.primitives![0].pose.xyz[0]).toBe(HISTORY_LIMIT + 25);
  });
  it("saved marks the twin clean and keeps history", () => {
    const s1 = run([move("T1", 1)]);
    const s2 = run([{ type: "saved", twin: { ...s1.twin!, version: 2 } }], s1);
    expect(isDirty(s2)).toBe(false);
    expect(s2.past).toHaveLength(1);
  });
  it("nextId finds the first free suffix", () => {
    expect(nextId("B", ["B1", "B2", "B4"])).toBe("B3");
    expect(nextId("K", [])).toBe("K1");
  });
});
