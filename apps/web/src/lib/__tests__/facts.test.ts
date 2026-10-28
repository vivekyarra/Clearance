import { describe, expect, it } from "vitest";
import { extractFactIds, formatFactValue, measurementTarget, parseBatchRef, parseFactTags } from "../facts";
import type { Twin } from "../../types";

describe("parseFactTags", () => {
  it("splits text and single tags", () => {
    expect(parseFactTags("Success 93% [f_042] at peak.")).toEqual([
      { kind: "text", text: "Success 93% " },
      { kind: "facts", ids: ["f_042"], raw: "[f_042]" },
      { kind: "text", text: " at peak." },
    ]);
  });
  it("merges adjacent groups and comma lists into one cluster", () => {
    const s = parseFactTags("a [f_1][f_2] b [f_3, f_4; f_5]");
    expect(s.filter((x) => x.kind === "facts").map((x) => (x as { ids: string[] }).ids)).toEqual([
      ["f_1", "f_2"],
      ["f_3", "f_4", "f_5"],
    ]);
  });
  it("leaves markdown links and other brackets alone", () => {
    expect(extractFactIds("[source](https://x.y) [x] [f_] [F_1] [f_ok-2]")).toEqual(["f_ok-2"]);
  });
  it("handles a lone tag and empty text", () => {
    expect(parseFactTags("[f_9]")).toEqual([{ kind: "facts", ids: ["f_9"], raw: "[f_9]" }]);
    expect(parseFactTags("")).toEqual([]);
  });
  it("is reentrant across calls (global regex state)", () => {
    for (let i = 0; i < 3; i++) expect(extractFactIds("x [f_1] y [f_2]")).toEqual(["f_1", "f_2"]);
  });
});

describe("formatFactValue", () => {
  it("formats ratios, metres, money and unknowns", () => {
    expect(formatFactValue({ value: 0.934, unit: "ratio" })).toBe("93.4%");
    expect(formatFactValue({ value: 0.74, unit: "m" })).toBe("74 cm");
    expect(formatFactValue({ value: 1.2, unit: "m" })).toBe("1.2 m");
    expect(formatFactValue({ value: 14900, unit: "USD" })).toBe("$14,900");
    expect(formatFactValue({ value: null, unit: "m" })).toBe("unknown");
    expect(formatFactValue({ value: "price on request", unit: "" })).toBe("price on request");
  });
});

describe("measurementTarget / parseBatchRef", () => {
  const twin = {
    twin_id: "t",
    site_id: "s",
    occupancy: { uri: "" },
    primitives: [{ id: "T4", type: "box", label: "table", pose: { xyz: [0, 0, 0] }, size: [1, 1, 1] }],
    thresholds: [{ id: "D1", segment: [[0, 0], [1, 0]] }],
  } as Twin;
  it("resolves entity ids, points and segments", () => {
    expect(measurementTarget("T4", twin)).toEqual({ kind: "entity", id: "T4" });
    expect(measurementTarget("gap:D1", twin)).toEqual({ kind: "entity", id: "D1" });
    expect(measurementTarget("xy:1.5,-2", twin)).toEqual({ kind: "point", xy: [1.5, -2] });
    expect(measurementTarget("gap@3.1, 2.4", twin)).toEqual({ kind: "point", xy: [3.1, 2.4] });
    expect(measurementTarget("seg:0,0,1,1", twin)).toEqual({ kind: "segment", a: [0, 0], b: [1, 1] });
    expect(measurementTarget("twin_x", twin)).toEqual({ kind: "none" });
  });
  it("parses batch refs with episodes", () => {
    expect(parseBatchRef("sb_1")).toEqual({ batchId: "sb_1", episode: 0 });
    expect(parseBatchRef("sb_1#7")).toEqual({ batchId: "sb_1", episode: 7 });
    expect(parseBatchRef("sb_1/episodes/12")).toEqual({ batchId: "sb_1", episode: 12 });
  });
});
