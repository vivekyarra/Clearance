import { describe, expect, it } from "vitest";
import type { AnalysisDoc, StageStatus } from "../../../types";
import { buildRoute, distanceAt, loopDuration, poseAt } from "../route";
import { counters, formatElapsed, latestHypothesis, narrowestGap, overallProgress, replayDoc, replayDuration, resolveFactTags, stageRow, stageRows } from "../stages";

const S = (o: Record<string, unknown>) => o as unknown as Record<string, StageStatus>;

describe("stageRows", () => {
  it("maps every stage to its label and display state, in pipeline order", () => {
    const rows = stageRows(
      S({
        report: { state: "pending", progress: 0 },
        verdicts: { state: "pending", progress: 0 },
        optimizer: { state: "skipped: no layout change needed", progress: 0 },
        investigation: { state: "running", progress: 0 },
        batches: { state: "running", progress: 0.25, detail: "9 of 36" },
        feasibility: { state: "done", progress: 1 },
        candidates: { state: "done", progress: 1, detail: "4 candidate(s)" },
      }),
    );
    expect(rows.map((r) => r.key)).toEqual(["candidates", "feasibility", "batches", "investigation", "optimizer", "verdicts", "report"]);
    expect(rows.map((r) => r.label)).toEqual(["Screening robots", "Checking routes", "Simulating missions", "Nemotron investigation", "Layout search", "Verdicts", "Writing the Clearance Report"]);
    expect(rows.map((r) => r.state)).toEqual(["done", "done", "running", "running", "skipped", "pending", "pending"]);
    expect(rows[0]).toMatchObject({ progress: 1, note: "4 candidate(s)" });
    expect(rows[2]).toMatchObject({ progress: 0.25, note: "9 of 36" });
    expect(rows[3].progress).toBeNull(); // running without progress -> indeterminate
    expect(rows[4]).toMatchObject({ progress: 0, note: "no layout change needed" });
  });

  it("handles failed stages, unknown keys, clamping and an empty object", () => {
    expect(stageRow("batches", { state: "failed: RuntimeError: worker lost" as never, progress: 0.4 })).toMatchObject({ state: "failed", progress: 0.4, note: "RuntimeError: worker lost" });
    expect(stageRow("batches", { state: "running", progress: 3 }).progress).toBe(1);
    expect(stageRow("custom_step", { state: "done" }).label).toBe("Custom step");
    expect(stageRow("simulation", { state: "pending" }).label).toBe("Simulating missions");
    expect(stageRow("optimizer", { state: "skipped" as never }).note).toBeTruthy();
    const rows = stageRows(S({ extra: { state: "done" }, candidates: { state: "running" } }));
    expect(rows.map((r) => r.key)).toEqual(["candidates", "extra"]);
    expect(stageRows(null).map((r) => r.state)).toEqual(Array(7).fill("pending"));
  });

  it("overall progress counts skipped stages as complete", () => {
    expect(overallProgress(stageRows(S({ a: { state: "done" }, b: { state: "skipped: x" }, c: { state: "running", progress: 0.5 }, d: { state: "pending" } })))).toBeCloseTo(0.625);
    expect(overallProgress([])).toBe(0);
  });
});

const baseDoc = (o: Partial<AnalysisDoc> & Record<string, unknown>): AnalysisDoc => ({ analysis_id: "an", status: "running", stages: {}, ...o }) as AnalysisDoc;

describe("counters and captions", () => {
  it("derives batches done / total, episodes and experiments", () => {
    const doc = baseDoc({
      stages: S({ batches: { state: "running", progress: 0.25 } }),
      candidates: ["a", "b"],
      batches: ["b1", "b2", "b3"],
      batch_index: { b1: { n_episodes: 40 }, b2: { n_episodes: 40 }, b3: { n_episodes: 40 } },
      investigations: { a: { experiments: [{ experiment_id: "e1", hypothesis: "h1" }, { experiment_id: "e2", hypothesis: "h2" }] } },
    });
    expect(counters(doc)).toEqual({ candidates: 2, batchesDone: 3, batchesTotal: 12, episodes: 120, experiments: 2 });
    expect(counters(baseDoc({ batches: ["x"], episodes_per_batch: 30, stages: S({ batches: { state: "done" } }) }))).toMatchObject({ batchesTotal: 1, episodes: 30, experiments: null });
  });

  it("resolves fact tags in the latest hypothesis", () => {
    const facts = [{ fact_id: "f_1", value: 0.18, unit: "m", label: "margin", origin: { type: "investigation", ref: "x" } }];
    expect(resolveFactTags("only [f_1] of margin, [f_missing] share.", facts as never)).toBe("only 0.18 m of margin, share.");
    const doc = baseDoc({ investigations: { a: { facts, experiments: [{ experiment_id: "e1", hypothesis: "first" }, { experiment_id: "e2", hypothesis: "gap is [f_1]" }] } } });
    expect(latestHypothesis(doc)).toBe("gap is 0.18 m");
    expect(latestHypothesis(doc, { investigation_id: "i", analysis_id: "an", model_id: "m", experiments: [{ experiment_id: "e9", robot_id: "a", kind: "move_object", hypothesis: "live one" }] })).toBe("live one");
    expect(latestHypothesis(baseDoc({}))).toBeNull();
  });

  it("picks the leg with the smallest margin as the narrowest gap", () => {
    const leg = (g: number, req: number, xy: [number, number]) => ({ from_wp: "A", to_wp: "B", reachable: true, narrowest_gap_m: g, required_gap_m: req, narrowest_xy: xy });
    const doc = baseDoc({ feasibility: { r1: { legs: [leg(0.9, 0.4, [1, 1]), leg(0.65, 0.4, [2, 2])] }, r2: { legs: [leg(0.7, 0.6, [3, 3])] } } as never });
    expect(narrowestGap(doc)).toMatchObject({ xy: [3, 3], gap: 0.7 });
    expect(narrowestGap(baseDoc({}))).toBeNull();
  });

  it("formats elapsed time", () => {
    expect(formatElapsed(0)).toBe("0:00");
    expect(formatElapsed(83_000)).toBe("1:23");
    expect(formatElapsed(3_723_000)).toBe("1:02:03");
  });
});

describe("recorded-run replay", () => {
  const final = baseDoc({
    status: "done",
    stages: S({
      candidates: { state: "done", progress: 1 },
      batches: { state: "done", progress: 1 },
      optimizer: { state: "skipped: nothing to change", progress: 0 },
      report: { state: "done", progress: 1 },
    }),
    candidates: ["a", "b"],
    batches: ["b1", "b2", "b3", "b4"],
    report: { md_key: "x" },
  });

  it("walks the stages from pending to their recorded final state", () => {
    const start = replayDoc(final, 0)!;
    expect(start.status).toBe("running");
    expect(stageRows(start.stages).map((r) => r.state)).toEqual(["pending", "pending", "pending", "pending"]);
    const mid = replayDoc(final, 0.6 + 2.2 + 4.5)!; // halfway through the batches stage
    const rows = stageRows(mid.stages);
    expect(rows.map((r) => r.state)).toEqual(["done", "running", "pending", "pending"]);
    expect(rows[1].progress).toBeCloseTo(0.5, 1);
    expect(mid.batches).toHaveLength(2);
    expect(mid.report).toBeNull();
    const late = replayDoc(final, replayDuration(final) - 0.2)!;
    expect(stageRows(late.stages).map((r) => r.state)).toEqual(["done", "done", "skipped", "done"]);
    expect(replayDoc(final, replayDuration(final))).toBeNull();
    expect(final.batches).toHaveLength(4); // the recorded doc is not modified
  });

  it("reveals the investigation hypothesis while that stage replays", () => {
    const withInv = baseDoc({
      status: "done",
      stages: S({ batches: { state: "done", progress: 1 }, investigation: { state: "done", progress: 1 } }),
      investigations: { a: { facts: [], experiments: [{ experiment_id: "e1", hypothesis: "a chair narrows the aisle" }] } },
    });
    const at = (t: number) => replayDoc(withInv, t)!;
    expect(latestHypothesis(at(0.6 + 9 + 0.5))).toBeNull(); // investigation just started
    const mid = at(0.6 + 9 + 3.5);
    expect(stageRows(mid.stages)[1].state).toBe("running");
    expect(latestHypothesis(mid)).toBe("a chair narrows the aisle");
  });
});

describe("route motion", () => {
  const r = buildRoute([
    [[0, 0], [2, 0]],
    [[2, 0], [2, 1]],
  ])!;
  const m = { speed: 1, pause: 0.5 };
  it("travels each leg and pauses at every stop, looping", () => {
    expect(r.length).toBeCloseTo(3);
    expect(r.stops).toEqual([0, 2, 3]);
    expect(loopDuration(r, m)).toBeCloseTo(4);
    expect(distanceAt(r, m, 1).s).toBeCloseTo(1); // eased midpoint of leg 1
    expect(distanceAt(r, m, 2.2)).toEqual({ s: 2, stopped: true });
    expect(poseAt(r, m, 2.5 + 0.5).y).toBeCloseTo(0.5);
    expect(distanceAt(r, m, 4 + 1).s).toBeCloseTo(1); // looped
    expect(buildRoute([[[0, 0]]])).toBeNull();
  });
});
