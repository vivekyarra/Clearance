import { rot } from "../lib/geometry";
import type { Primitive, WaypointKind, XY } from "../types";

export type Tool = "select" | "pan" | "box" | "keepout" | "clear" | "waypoint" | "threshold" | "measure";

export const TOOLS: { id: Tool; icon: string; key: string; label: string }[] = [
  { id: "select", icon: "cursor", key: "V", label: "Select and move" },
  { id: "pan", icon: "hand", key: "H", label: "Pan" },
  { id: "box", icon: "box", key: "B", label: "Add box (drag to size)" },
  { id: "keepout", icon: "polygon", key: "K", label: "Draw keep-out zone" },
  { id: "clear", icon: "check", key: "F", label: "Confirm floor: mark unseen (grey) floor you checked on site as clear" },
  { id: "waypoint", icon: "pin", key: "W", label: "Place waypoint" },
  { id: "threshold", icon: "flag", key: "T", label: "Draw door threshold" },
  { id: "measure", icon: "ruler", key: "M", label: "Measure a gap" },
];

export const WAYPOINT_KINDS: { kind: WaypointKind; label: string; prefix: string }[] = [
  { kind: "dock", label: "Dock", prefix: "DOCK" },
  { kind: "pickup", label: "Pickup", prefix: "PICKUP" },
  { kind: "dest", label: "Destination", prefix: "TBL" },
  { kind: "entrance", label: "Entrance", prefix: "ENTRANCE" },
  { kind: "exit", label: "Exit", prefix: "EXIT" },
];

export const LABELS = ["table", "chair", "stool", "shelf", "counter", "door", "cart", "box", "trash bin", "plant", "cable", "glass", "step", "pillar", "wall"];

export const CLEAR_ZONE_REASON = "floor confirmed clear by operator";

export const LOW_CONFIDENCE = 0.6;

export const snap = (v: number, step = 0.01) => Math.round(v / step) * step;
export const r3 = (v: number) => Math.round(v * 1000) / 1000;

/** Corner handles of a primitive in world coords (box: 4 corners; cylinder: 4 points on the circle). */
export function primHandles(p: Primitive): XY[] {
  const [x, y] = p.pose.xyz;
  const yaw = p.pose.yaw ?? 0;
  const hl = p.size[0] / 2;
  const hw = p.type === "cylinder" ? p.size[0] / 2 : p.size[1] / 2;
  return [
    [hl, hw],
    [-hl, hw],
    [-hl, -hw],
    [hl, -hw],
  ].map(([a, b]) => {
    const [rx, ry] = rot(a, b, yaw);
    return [x + rx, y + ry] as XY;
  });
}

/** Resize a primitive by dragging corner `idx` to world point `pt` with the opposite corner fixed. */
export function resizeFromCorner(p: Primitive, idx: number, pt: XY, minSize = 0.05): Primitive {
  const yaw = p.pose.yaw ?? 0;
  if (p.type === "cylinder") {
    const d = Math.max(minSize, r3(2 * Math.hypot(pt[0] - p.pose.xyz[0], pt[1] - p.pose.xyz[1])));
    return { ...p, size: [d, d, p.size[2]] };
  }
  const corners = primHandles(p);
  const opp = corners[(idx + 2) % 4];
  const [lx, ly] = rot(pt[0] - opp[0], pt[1] - opp[1], -yaw);
  const sx = Math.sign(lx) || 1;
  const sy = Math.sign(ly) || 1;
  const l = Math.max(minSize, Math.abs(lx));
  const w = Math.max(minSize, Math.abs(ly));
  const [cx, cy] = rot((sx * l) / 2, (sy * w) / 2, yaw);
  return { ...p, pose: { ...p.pose, xyz: [r3(opp[0] + cx), r3(opp[1] + cy), p.pose.xyz[2]] }, size: [r3(l), r3(w), p.size[2]] };
}

/** World position of the rotation handle (beyond the +y side in screen space). */
export function rotateHandle(p: Primitive, offsetM: number): XY {
  const [x, y] = p.pose.xyz;
  const yaw = p.pose.yaw ?? 0;
  const reach = (p.type === "cylinder" ? p.size[0] / 2 : p.size[1] / 2) + offsetM;
  const [rx, ry] = rot(0, reach, yaw);
  return [x + rx, y + ry];
}
