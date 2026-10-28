import { describe, expect, it } from "vitest";
import { captureFromServer, type ServerCapture } from "../../api/http";
import type { Twin } from "../../types";
import { extOf, splatFormat, twinAssetUrl } from "../assets";
import { coachFromWire } from "../coach";
import { measurementTarget } from "../facts";
import { appJoinLink, joinApiBase, siteJoinUrl } from "../join";

const twin = {
  twin_id: "tw_1",
  site_id: "site_a",
  version: 3,
  occupancy: { uri: "" },
  thresholds: [{ id: "D1", segment: [[0, 0], [1, 0]] }],
  hazards: [{ label: "cable", xy: [2, 3] }, { label: "wet floor" }],
  assets: {
    splat: "sites/site_a/twin_v3/splat.ply",
    occluder_usdz: "sites/site_a/twin_v3/occluder.usdz",
    mesh_glb: "sites/site_a/twin_v3/mesh.glb",
    pointcloud: "https://cdn.example.com/pc.ply",
    capture_id: "cap_1",
  },
} as unknown as Twin;

describe("twin asset URLs", () => {
  it("routes storage keys through the API and keeps the file extension", () => {
    expect(twinAssetUrl("/api", twin, "splat")).toBe("/api/sites/site_a/twin/assets/splat.ply?version=3");
    expect(twinAssetUrl("/api", twin, "occluder_usdz")).toBe("/api/sites/site_a/twin/assets/occluder.usdz?version=3");
    expect(twinAssetUrl("/api", twin, "mesh_glb")).toBe("/api/sites/site_a/twin/assets/mesh.glb?version=3");
  });
  it("passes absolute URLs through and returns null for missing assets", () => {
    expect(twinAssetUrl("/api", twin, "pointcloud")).toBe("https://cdn.example.com/pc.ply");
    expect(twinAssetUrl("/api", twin, "mesh")).toBeNull();
    expect(twinAssetUrl("/api", { ...twin, assets: undefined }, "splat")).toBeNull();
  });
  it("detects splat formats ignoring query strings", () => {
    expect(splatFormat("/api/sites/s/twin/assets/splat.ply?version=3")).toBe("Ply");
    expect(splatFormat("https://x/y.ksplat#a")).toBe("KSplat");
    expect(splatFormat("/x/splat")).toBeNull();
    expect(extOf("a/b.c/d")).toBe("");
  });
});

describe("coach wire messages", () => {
  const cov = { cell_m: 0.5, origin: [-1, 2] as [number, number], width: 2, height: 1, cells: [0, 2] };
  it("splits one message into coverage, prompt and status updates", () => {
    expect(coachFromWire({ type: "coach", site_id: "s", coverage: cov, prompt: "Walk left", t: 5, status: { device: "iPhone", frames: 9, tracking: "normal" } })).toEqual([
      { type: "coverage", ...cov },
      { type: "prompt", text: "Walk left", t: 5 },
      { type: "status", device: "iPhone", frames: 9, tracking: "normal" },
    ]);
  });
  it("ignores other frames and malformed coverage", () => {
    expect(coachFromWire({ type: "hello" })).toEqual([]);
    expect(coachFromWire(null)).toEqual([]);
    expect(coachFromWire({ type: "coach", site_id: "s", coverage: { ...cov, cells: [1] }, prompt: null, t: 1 })).toEqual([]);
  });
});

describe("measurement fact refs", () => {
  it("resolves the fixed forms", () => {
    expect(measurementTarget("D1", twin)).toEqual({ kind: "entity", id: "D1" });
    expect(measurementTarget("xy:1,2", twin)).toEqual({ kind: "point", xy: [1, 2] });
    expect(measurementTarget("seg:0,0,1.5,0", twin)).toEqual({ kind: "segment", a: [0, 0], b: [1.5, 0] });
  });
  it("resolves the refs agents/report/facts.py emits", () => {
    expect(measurementTarget("tw_1:threshold:D1", twin)).toEqual({ kind: "entity", id: "D1" });
    expect(measurementTarget("tw_1:hazard:0", twin)).toEqual({ kind: "point", xy: [2, 3] });
    expect(measurementTarget("tw_1:hazard:1", twin)).toEqual({ kind: "none" });
    expect(measurementTarget("tw_1:measurement:D1", twin)).toEqual({ kind: "none" });
    expect(measurementTarget("tw_1:accuracy", twin)).toEqual({ kind: "none" });
  });
});

describe("site join links", () => {
  it("builds the QR join URL with percent-encoded query items", () => {
    expect(siteJoinUrl("https://c.example.com/", "site_a", "http://10.0.0.5:8000/", "Corner Café")).toBe(
      "https://c.example.com/join/site_a?api=http%3A%2F%2F10.0.0.5%3A8000&name=Corner%20Caf%C3%A9",
    );
    expect(siteJoinUrl("https://c.example.com", "site_a")).toBe("https://c.example.com/join/site_a");
  });
  it("hands the app the API base from the QR, else the console proxy", () => {
    expect(joinApiBase("?api=http%3A%2F%2F10.0.0.5%3A8000&name=x", "https://c.example.com")).toBe("http://10.0.0.5:8000");
    expect(joinApiBase("?api=javascript%3Aalert(1)", "https://c.example.com")).toBe("https://c.example.com/api");
    expect(appJoinLink("site_a", "?api=http%3A%2F%2Fh%3A8000&name=Corner%20Cafe", "https://c")).toBe(
      "clearance://join?api=http%3A%2F%2Fh%3A8000&site=site_a&name=Corner%20Cafe",
    );
    expect(appJoinLink("site_a", "", "https://c.example.com")).toBe("clearance://join?api=https%3A%2F%2Fc.example.com%2Fapi&site=site_a");
  });
});

describe("site capture list", () => {
  const row = (over: Partial<ServerCapture>): ServerCapture => ({
    capture_id: "cap_1",
    site_id: "site_a",
    role: "scanner",
    tier: "T2_VIO_MONO",
    status: "registered",
    device: { model: "iPhone14,5", tier: "T2_VIO_MONO" },
    upload: { status: "waiting", n_expected: 4, n_uploaded: 0, progress: 0 },
    processing: { status: "waiting" },
    ...over,
  });
  it("maps upload and processing state to the console status", () => {
    expect(captureFromServer(row({})).status).toBe("registered");
    const up = captureFromServer(row({ upload: { status: "uploading", n_expected: 4, n_uploaded: 1, progress: 0.25 } }));
    expect([up.status, up.progress]).toEqual(["uploading", 0.25]);
    expect(captureFromServer(row({ upload: { status: "uploaded", n_expected: 4, n_uploaded: 4, progress: 1 } })).status).toBe("uploaded");
    expect(captureFromServer(row({ status: "processing" })).status).toBe("processing");
    expect(captureFromServer(row({ status: "stored", role: "observer" })).status).toBe("done");
    expect(captureFromServer(row({ status: "failed", error: "bad zip" })).error).toBe("bad zip");
    expect(captureFromServer(row({ device: null })).device).toEqual({ model: "T2_VIO_MONO", tier: "T2_VIO_MONO" });
  });
});
