import { describe, expect, it } from "vitest";
import { PoseStabilizer, cameraPosition, intrinsicsFromFov, markerObjectPoints, rodrigues, rotationDelta, siteFromCamera, type Vec3 } from "../pose";
import { detectDevice } from "../../lib/device";
import { FaceTracker, grow } from "../faceblur";

const close = (a: number[], b: number[], eps = 1e-9) => a.forEach((v, i) => expect(Math.abs(v - b[i])).toBeLessThan(eps));

describe("marker pose math", () => {
  it("object points follow TL, TR, BR, BL with +y toward the marker top", () => {
    expect(markerObjectPoints(0.16)).toEqual([
      [-0.08, 0.08, 0],
      [0.08, 0.08, 0],
      [0.08, -0.08, 0],
      [-0.08, -0.08, 0],
    ]);
  });
  it("rodrigues gives a proper rotation", () => {
    close(rodrigues([0, 0, Math.PI / 2]), [0, -1, 0, 1, 0, 0, 0, 0, 1]);
  });
  it("a camera looking straight down from 1.5 m sits above the origin in ARKit convention", () => {
    // OpenCV camera above the marker looking down: rotation pi about x, marker 1.5 m ahead
    const T = siteFromCamera([Math.PI, 0, 0], [0, 0, 1.5]);
    close(cameraPosition(T), [0, 0, 1.5]);
    close([-T[2], -T[6], -T[10]], [0, 0, -1]); // ARKit looks along -Z_cam: straight down
    close([T[1], T[5], T[9]], [0, 1, 0]); // image up = marker top = site +y
  });
  it("intrinsics from field of view", () => {
    const [fx, fy, cx, cy] = intrinsicsFromFov(1280, 720, 90);
    expect(fx).toBeCloseTo(640, 6);
    expect(fy).toBeCloseTo(640, 6);
    expect([cx, cy]).toEqual([640, 360]);
  });
});

describe("PoseStabilizer", () => {
  const at = (x: number, yaw = 0) => siteFromCamera([Math.PI, 0, yaw] as Vec3, [x, 0, 1.5]);
  it("locks only after 2 s of continuous, steady detections", () => {
    const s = new PoseStabilizer(2, 0.02);
    let r = s.push(0, at(0));
    for (let t = 0.2; t < 1.9; t += 0.2) r = s.push(t, at(0.001));
    expect(r.stable).toBe(false);
    r = s.push(2.05, at(0));
    expect(r.stable).toBe(true);
    expect(rotationDelta(r.pose!, at(0))).toBeLessThan(1e-9);
  });
  it("restarts the window after movement or a long gap", () => {
    const s = new PoseStabilizer(2, 0.02, 0.05, 0.5);
    for (let t = 0; t <= 1.6; t += 0.2) s.push(t, at(0));
    expect(s.push(1.8, at(0.2)).stable).toBe(false); // moved 20 cm
    expect(s.push(3.0, at(0.2)).heldS).toBe(0); // 1.2 s gap resets
    expect(s.push(3.5, null).stable).toBe(false);
    expect(s.push(4.2, null).heldS).toBe(0);
  });
});

describe("join device detection and face hold", () => {
  it("routes phones to roles", () => {
    expect(detectDevice("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)")).toBe("ios");
    expect(detectDevice("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15)", 5)).toBe("ios");
    expect(detectDevice("Mozilla/5.0 (Linux; Android 14; RMX3871)")).toBe("android");
    expect(detectDevice("Mozilla/5.0 (Windows NT 10.0; Win64; x64)")).toBe("other");
  });
  it("keeps face boxes alive across missed detections, then drops them", () => {
    const f = new FaceTracker(500, 0.25);
    expect(f.update([{ x: 10, y: 10, w: 40, h: 40 }], 0)).toHaveLength(1);
    expect(f.update([], 400)).toHaveLength(1);
    expect(f.update([], 700)).toHaveLength(0);
    const g = grow({ x: 10, y: 10, w: 40, h: 40 }, 0.25);
    expect(g.w).toBe(60);
    expect(g.x).toBe(0);
  });
});
