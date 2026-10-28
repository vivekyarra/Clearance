import { describe, expect, it } from "vitest";
import { decodeGrid, decodePinch, INDEX, loadSiteFixture } from "../fixtures";
import { offlineApi } from "../api";
import { extractFactIds } from "../../lib/facts";
import { parseMarkdown, splitSections } from "../../components/Markdown";
import type { Fact } from "../../types";

const SITES = INDEX.sites.map((s) => s.site_id);

describe("offline fixtures are the recorded reference-site Clearance checks", () => {
  it("lists the three reference sites and the real robot catalog", () => {
    expect(SITES.sort()).toEqual(["ref_cafe", "ref_corridor", "ref_storeroom"]);
    for (const s of INDEX.sites) expect(s.geometry_source).toBe("reference");
    expect(INDEX.robots.length).toBeGreaterThanOrEqual(10);
    const raw = JSON.stringify(INDEX.robots);
    expect(raw).not.toMatch(/example\.com/);
    for (const r of INDEX.robots) {
      expect(r.footprint_polygon_m?.length).toBeGreaterThanOrEqual(3);
      expect(Object.keys(r.fields_sources ?? {}).length).toBeGreaterThan(0);
    }
    expect(Object.values(INDEX.analyses).sort()).toEqual(SITES.sort());
  });

  for (const siteId of ["ref_cafe", "ref_storeroom", "ref_corridor"]) {
    describe(siteId, () => {
      it("occupancy decodes to the twin's grid size", async () => {
        const f = await loadSiteFixture(siteId);
        const g = decodeGrid(f.occupancy);
        expect(g.width).toBe(f.twin.occupancy.width);
        expect(g.height).toBe(f.twin.occupancy.height);
        expect(new Set(g.data)).toEqual(new Set([0, 127, 255]));
      });

      it("report verdicts match the code verdicts and every fact tag resolves", async () => {
        const f = await loadSiteFixture(siteId);
        for (const [aid, a] of Object.entries(f.analyses)) {
          const report = await offlineApi.getReport(aid);
          const facts: Fact[] = await offlineApi.getFacts(aid);
          const ids = new Set(facts.map((x) => x.fact_id));
          const tags = extractFactIds(report.markdown);
          expect(tags.length).toBeGreaterThan(10);
          for (const t of tags) expect(ids.has(t)).toBe(true);
          const verdicts = a.doc.verdicts ?? [];
          expect(verdicts.length).toBeGreaterThan(0);
          const comp = splitSections(report.markdown).find((s) => /Robot comparison/.test(s.title));
          const table = parseMarkdown(comp!.body).find((x) => x.kind === "table");
          expect(table?.rows).toHaveLength(verdicts.length);
          for (const v of verdicts) expect(["Cleared", "Cleared with changes", "Not cleared"]).toContain(v.label);
          expect(report.markdown).toMatch(/awaiting review|human-reviewed/); // catalog review status label
        }
      });

      it("replays and Pinch Maps load for every recorded replay batch", async () => {
        const f = await loadSiteFixture(siteId);
        const grid = decodeGrid(f.occupancy);
        for (const a of Object.values(f.analyses))
          for (const [bid, eps] of Object.entries(a.episodes)) {
            const batch = await offlineApi.getBatch(bid);
            expect(batch.batch_id).toBe(bid);
            expect(eps.length).toBeGreaterThan(0);
            const frames = await offlineApi.getEpisode(bid, 0);
            expect(frames.length).toBeGreaterThan(0);
            expect(frames[0].robot).toHaveLength(4);
            if (a.pinch[bid]) expect(decodePinch(a.pinch[bid], grid)).toHaveLength(grid.width * grid.height);
          }
      });
    });
  }

  it("serves the recorded check for a reference site and asks for services/api on an edited twin", async () => {
    const twin = await offlineApi.getTwin("ref_storeroom");
    const r = await offlineApi.startAnalysis("ref_storeroom", { task: { type: "delivery", sequence: ["DOCK", "S1", "DOCK"] }, template: "t", business: {} as never, twin_version: twin.version });
    expect(INDEX.analyses[r.analysis_id]).toBe("ref_storeroom");
    const edited = await offlineApi.saveTwin("ref_storeroom", twin);
    await expect(offlineApi.startAnalysis("ref_storeroom", { task: { type: "delivery", sequence: ["DOCK"] }, template: "t", business: {} as never, twin_version: edited.version })).rejects.toThrow(/services\/api/);
  });

  it("usage summary comes from the recorded runs", async () => {
    const u = await offlineApi.usageSummary();
    expect(u.rows.length).toBeGreaterThan(0);
  });
});
