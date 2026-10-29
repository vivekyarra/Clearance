import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { FactChips, FactProvider, useFacts, type FactActions } from "../components/Facts";
import { Icon } from "../components/Icon";
import { Markdown, splitSections } from "../components/Markdown";
import { CheckProgress } from "../components/progress/CheckProgress";
import { useReplay } from "../components/progress/useReplay";
import { TwinView } from "../components/TwinView";
import { Drawer, ErrorBox, EstimateBadge, Loading, QR, ReferenceBadge, VerdictBadge, useToast } from "../components/ui";
import { useAsync, useInterval } from "../hooks/useAsync";
import { measurementTarget, parseBatchRef, type MeasurementTarget } from "../lib/facts";
import type { OccGrid } from "../lib/grid";
import { metres, money, pct } from "../lib/stats";
import type {
  AnalysisDoc,
  Citation,
  Density,
  Experiment,
  Fact,
  Feasibility,
  Investigation,
  OptimizerChange,
  Primitive,
  ReportResponse,
  RobotSpec,
  SimBatch,
  Twin,
  UsageSummary,
  Verdict,
  VerdictCheck,
} from "../types";
import { footprintText } from "./Console";

const SECTIONS: [string, string, RegExp][] = [
  ["verdict", "Verdict", /verdict|summary/i],
  ["room", "Real room", /site|measure|accuracy/i],
  ["robots", "Robot comparison", /comparison|robots/i],
  ["pinch", "Pinch Map", /pinch|bottleneck/i],
  ["investigation", "How we investigated", /investigat|why/i],
  ["changes", "Recommended changes", /change|layout/i],
  ["payback", "Payback", /payback|roi/i],
  ["hazards", "Hazards and manual checks", /hazard|manual/i],
  ["method", "Methodology and scope", /method|scope/i],
  ["sources", "Sources", /source|citation/i],
  ["usage", "Model and compute usage", /usage/i],
];

interface Loaded {
  doc: AnalysisDoc;
  report: ReportResponse;
  inv: Investigation | null;
  twin: Twin | null;
  grid: OccGrid | null;
  robots: Map<string, RobotSpec>;
  batches: SimBatch[];
  usage: UsageSummary | null;
}

async function loadAll(id: string, doc: AnalysisDoc): Promise<Loaded> {
  const siteId = doc.site_id ?? doc.inputs?.site_id ?? "";
  const version = (doc as { twin_version?: number }).twin_version ?? doc.inputs?.twin_version;
  const [report, inv, twin, robots, usage] = await Promise.all([
    api.getReport(id),
    api.getInvestigation(id).catch(() => null),
    siteId ? api.getTwin(siteId, version).catch(() => null) : Promise.resolve(null),
    api.listRobots().catch(() => [] as RobotSpec[]),
    api.usageSummary().catch(() => null),
  ]);
  const grid = twin ? await api.occupancy(twin).catch(() => null) : null;
  const raw = doc.batches ?? [];
  const batches: SimBatch[] = [];
  const ids = raw.filter((b): b is string => typeof b === "string");
  for (const b of raw) if (typeof b !== "string") batches.push(b);
  for (let i = 0; i < ids.length; i += 8) batches.push(...(await Promise.all(ids.slice(i, i + 8).map((b) => api.getBatch(b).catch(() => null)))).filter((x): x is SimBatch => !!x));
  return { doc, report, inv, twin, grid, robots: new Map(robots.map((r) => [r.robot_id, r])), batches, usage };
}

export default function ReportPage() {
  const { id = "" } = useParams();
  const loc = useLocation();
  const doc = useAsync(() => api.getAnalysis(id), [id]);
  const running = doc.data && doc.data.status !== "done" && doc.data.status !== "failed";
  useInterval(() => doc.reload(), 2000, !!running);
  const done = doc.data?.status === "done";
  const all = useAsync(async () => (done && doc.data ? loadAll(id, doc.data) : null), [id, done]);
  // Offline mode: a check opened right after "Start" (or with ?watch=1) replays its recorded stages.
  const watch = api.mode === "offline" && ((loc.state as { watch?: boolean } | null)?.watch === true || new URLSearchParams(loc.search).get("watch") === "1");
  const replay = useReplay(doc.data, watch);
  const shown = replay.doc ?? doc.data;
  const waited = useRef(false);

  if (doc.error) return <main className="page"><ErrorBox error={doc.error} onRetry={doc.reload} /></main>;
  if (!doc.data || !shown) return <Loading label="Loading Clearance Report" />;
  const finishing = shown.status === "done" && !all.data && !all.error;
  if (shown.status !== "done" || finishing) {
    waited.current = true;
    return (
      <main className="page" style={{ maxWidth: 1240 }}>
        <div className="crumbs">
          <Link to="/">Sites</Link> / {shown.site_id ? <Link to={`/sites/${shown.site_id}`}>Site</Link> : "Site"} / Clearance check <EstimateBadge />
        </div>
        <CheckProgress id={id} doc={shown} finishing={finishing} replayElapsed={replay.elapsed} onSkip={replay.skip} />
        {shown.error && <div style={{ marginTop: 12 }}><ErrorBox error={new Error(shown.error)} /></div>}
      </main>
    );
  }
  if (all.error) return <main className="page"><ErrorBox error={all.error} onRetry={all.reload} /></main>;
  if (!all.data) return <Loading label="Loading Clearance Report" />;
  return waited.current ? (
    <div className="cp-fade-in">
      <Report id={id} d={all.data} />
    </div>
  ) : (
    <Report id={id} d={all.data} />
  );
}

function Report({ id, d }: { id: string; d: Loaded }) {
  const nav = useNavigate();
  const toast = useToast();
  const { doc, report, inv, twin, grid, robots, batches } = d;
  const [measure, setMeasure] = useState<{ target: MeasurementTarget; label: string } | null>(null);
  const [hlStep, setHlStep] = useState<number | null>(null);
  const [active, setActive] = useState("verdict");
  const siteId = doc.site_id ?? doc.inputs?.site_id ?? twin?.site_id ?? "";
  const sections = useMemo(() => splitSections(report.markdown), [report.markdown]);
  const mdFor = (key: string) => {
    const re = SECTIONS.find((s) => s[0] === key)![2];
    return sections.filter((s) => s.title && re.test(s.title) && SECTIONS.find((x) => x[2].test(s.title))?.[0] === key);
  };
  const unmatched = sections.filter((s) => s.title && !SECTIONS.some((x) => x[2].test(s.title)));
  const preamble = sections.find((s) => !s.title)?.body;
  const verdicts = doc.verdicts ?? [];
  const feas = useMemo(() => {
    const f = doc.feasibility;
    const list: Feasibility[] = Array.isArray(f) ? f : f ? Object.values(f) : [];
    return new Map(list.map((x) => [x.robot_id, x]));
  }, [doc.feasibility]);
  const changes: OptimizerChange[] = Array.isArray(doc.optimizer) ? doc.optimizer : (doc.optimizer?.changes ?? []);
  const robotName = (rid: string) => {
    const r = robots.get(rid);
    return r ? `${r.vendor} ${r.model}` : rid;
  };
  const replayLink = (batchId: string, n = 0) => `/replay/${encodeURIComponent(batchId)}/${n}?analysis=${encodeURIComponent(id)}${siteId ? `&site=${encodeURIComponent(siteId)}` : ""}`;

  const actions: FactActions = {
    primary(f: Fact) {
      switch (f.origin.type) {
        case "sim_batch": {
          const { batchId, episode } = parseBatchRef(f.origin.ref);
          return { label: "Open replay", run: () => nav(replayLink(batchId, episode)) };
        }
        case "source_url":
          return f.origin.ref.startsWith("http") ? { label: "Open source", run: () => undefined, href: f.origin.ref } : null;
        case "measurement":
          return twin ? { label: "Show on twin", run: () => setMeasure({ target: measurementTarget(f.origin.ref, twin), label: f.label }) } : null;
        case "investigation":
          return {
            label: "Open in trace",
            run: () => {
              const step = inv?.steps?.find((s) => s.fact_ids?.includes(f.fact_id));
              setHlStep(step?.i ?? null);
              document.getElementById("investigation")?.scrollIntoView({ behavior: "smooth" });
            },
          };
        case "formula":
        case "user_input":
          return { label: "Show payback inputs", run: () => document.getElementById("payback")?.scrollIntoView({ behavior: "smooth" }) };
      }
    },
  };

  useEffect(() => {
    const obs = new IntersectionObserver(
      (es) => {
        const v = es.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        if (v) setActive(v.target.id);
      },
      { rootMargin: "-80px 0px -60% 0px" },
    );
    for (const [k] of SECTIONS) {
      const el = document.getElementById(k);
      if (el) obs.observe(el);
    }
    return () => obs.disconnect();
  }, []);

  return (
    <FactProvider facts={report.facts} citations={report.citations} actions={actions}>
      <main className="page" style={{ maxWidth: 1320 }}>
        <div className="crumbs">
          <Link to="/">Sites</Link> / {siteId ? <Link to={`/sites/${siteId}`}>{siteId}</Link> : "Site"} / Clearance Report
        </div>
        <div className="page-head">
          <div>
            <div className="row">
              <h1>Clearance Report</h1>
              <EstimateBadge />
              {twin?.geometry_source === "reference" && <ReferenceBadge />}
            </div>
            <div className="sub">
              Twin v{twin?.version ?? "?"} · route <span className="mono">{doc.inputs?.task?.sequence?.join(" → ")}</span>
              {doc.created_at ? ` · ${new Date(doc.created_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}` : ""}
            </div>
          </div>
          <div className="row no-print">
            <button
              className="btn"
              onClick={() => {
                navigator.clipboard?.writeText(location.href).then(() => toast("Link copied"), () => toast("Copy failed", true));
              }}
            >
              <Icon name="link" size={15} /> Copy link
            </button>
            <button className="btn" onClick={() => window.print()}>
              <Icon name="doc" size={15} /> Print / PDF
            </button>
          </div>
        </div>

        <div className="report">
          <nav className="toc">
            {SECTIONS.map(([k, label]) => (
              <a key={k} href={`#${k}`} className={active === k ? "active" : ""}>
                {label}
              </a>
            ))}
          </nav>
          <div className="stack" style={{ gap: 20 }}>
            <Section id="verdict" title="Verdict" right={<span className="small muted">Rules set by code; the language model only explains them.</span>}>
              <div>
                {verdicts.map((v) => (
                  <VerdictRow key={v.robot_id} v={v} robot={robots.get(v.robot_id)} name={robotName(v.robot_id)} feas={feas.get(v.robot_id)} batches={batches} replayLink={replayLink} />
                ))}
                {!verdicts.length && <div className="card-body muted">No verdicts in this Clearance check.</div>}
              </div>
              {(preamble || mdFor("verdict").length > 0) && (
                <div className="card-body" style={{ borderTop: "1px solid var(--border)" }}>
                  {preamble && <Markdown md={preamble} />}
                  {mdFor("verdict").map((s, i) => (
                    <Markdown key={i} md={s.body} />
                  ))}
                </div>
              )}
            </Section>

            <Section id="room" title="The real room">
              <div className="card-body">
                <div className="grid2" style={{ gridTemplateColumns: "minmax(0,1.2fr) minmax(0,1fr)" }}>
                  {twin && grid ? <TwinView twin={twin} grid={grid} height={300} /> : <div className="empty">Twin not available.</div>}
                  <div className="stack-sm">
                    {mdFor("room").map((s, i) => (
                      <Markdown key={i} md={s.body} />
                    ))}
                    {twin && <AccuracyLine twin={twin} />}
                    <div className="note row" style={{ gap: 12, alignItems: "flex-start" }}>
                      <QR text={batches[0] ? `${location.origin}${replayLink(batches.find((b) => b.density === "normal" && b.geometry_bound === "nominal")?.batch_id ?? batches[0].batch_id)}` : location.href} size={84} />
                      <div>
                        <div style={{ fontWeight: 600, color: "var(--fg)" }}>Watch it in your room</div>
                        Open the Clearance iPhone app, scan this code and point the phone at the floor marker. The robot plays its simulated run in AR, hidden behind real furniture.
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </Section>

            <Section id="robots" title="Robot comparison" right={<span className="small muted">Nominal geometry; range = conservative to optimistic scan bound</span>}>
              <ComparisonTable verdicts={verdicts} robots={robots} feas={feas} batches={batches} replayLink={replayLink} doc={doc} />
            </Section>

            <Section id="pinch" title="Pinch Map">
              <PinchMap twin={twin} grid={grid} batches={batches} robotName={robotName} md={mdFor("pinch").map((s) => s.body).join("\n")} />
            </Section>

            <Section id="investigation" title="How we investigated" right={inv ? <span className="badge">{inv.mode === "live" ? inv.model_id : "deterministic policy (offline mode)"}</span> : undefined}>
              <InvestigationPanel inv={inv} robotName={robotName} replayLink={replayLink} hlStep={hlStep} md={mdFor("investigation")} />
            </Section>

            <Section id="changes" title="Recommended changes">
              <ChangesPanel changes={changes} twin={twin} grid={grid} robotName={robotName} report={report} replayLink={replayLink} md={mdFor("changes").map((s) => s.body).join("\n")} />
            </Section>

            <Section id="payback" title="Payback">
              <PaybackPanel doc={doc} robotName={robotName} md={mdFor("payback").map((s) => s.body).join("\n")} />
            </Section>

            <Section id="hazards" title="Hazards and manual checks">
              <div className="card-body">
                <HazardList twin={twin} robots={[...robots.values()].filter((r) => verdicts.some((v) => v.robot_id === r.robot_id))} onShow={(t, label) => setMeasure({ target: t, label })} />
                {mdFor("hazards").map((s, i) => (
                  <Markdown key={i} md={s.body} />
                ))}
              </div>
            </Section>

            <Section id="method" title="Methodology and scope">
              <div className="card-body">
                {mdFor("method").map((s, i) => (
                  <Markdown key={i} md={s.body} />
                ))}
                {unmatched.map((s, i) => (
                  <div key={i}>
                    <h3 style={{ margin: "12px 0 6px" }}>{s.title}</h3>
                    <Markdown md={s.body} />
                  </div>
                ))}
                <div className="note">
                  Every number links to its origin: a simulation batch, a measurement on the twin, a cited page, your input, or a formula. Results are simulated on the measured twin.
                </div>
              </div>
            </Section>

            <Section id="sources" title="Sources">
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Source</th>
                      <th>Quote</th>
                      <th>Retrieved</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.citations.map((c, i) => (
                      <tr key={i}>
                        <td style={{ maxWidth: 320 }}>
                          <a href={c.url} target="_blank" rel="noreferrer">
                            {c.title || c.url}
                          </a>
                          <div className="tiny faint" style={{ wordBreak: "break-all" }}>
                            {c.url}
                          </div>
                        </td>
                        <td className="small">
                          {citationQuotes(c).length ? (
                            citationQuotes(c).map((q, j) => <div key={j}>"{q}"</div>)
                          ) : (
                            <span className="faint">–</span>
                          )}
                        </td>
                        <td className="small muted">{c.retrieved_at ? new Date(c.retrieved_at).toLocaleDateString(undefined, { dateStyle: "medium" }) : "–"}</td>
                      </tr>
                    ))}
                    {!report.citations.length && (
                      <tr>
                        <td colSpan={3} className="muted">
                          No external sources cited.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </Section>

            <Section id="usage" title="Model and compute usage">
              <UsagePanel usage={d.usage} />
            </Section>
          </div>
        </div>
        {measure && twin && grid && (
          <Drawer title={measure.label} onClose={() => setMeasure(null)}>
            <TwinView
              twin={twin}
              grid={grid}
              height={420}
              highlight={measure.target}
              focus={measure.target.kind === "point" ? measure.target.xy : measure.target.kind === "segment" ? [(measure.target.a[0] + measure.target.b[0]) / 2, (measure.target.a[1] + measure.target.b[1]) / 2] : null}
            />
            {measure.target.kind === "none" && <div className="note" style={{ marginTop: 10 }}>This measurement has no location on the twin.</div>}
          </Drawer>
        )}
      </main>
    </FactProvider>
  );
}

function Section({ id, title, right, children }: { id: string; title: string; right?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section id={id} className="card section">
      <div className="card-head">
        <h2>{title}</h2>
        {right}
      </div>
      {children}
    </section>
  );
}

function AccuracyLine({ twin }: { twin: Twin }) {
  const a = twin.accuracy;
  return (
    <div className="kv">
      <dt>Scan error</dt>
      <dd>
        {a?.median_abs_err_m != null ? `${(a.median_abs_err_m * 100).toFixed(1)} cm median` : "not measured"}
        {a?.p90_abs_err_m != null ? ` · ${(a.p90_abs_err_m * 100).toFixed(1)} cm p90` : ""}
        <span className="muted"> ({a?.method === "tape_check" ? "tape check" : a?.method === "reference_ground_truth" ? "ground truth" : "estimated"})</span>
      </dd>
      <dt>Objects</dt>
      <dd>{(twin.primitives ?? []).length} reviewed in the twin editor</dd>
      <dt>Traffic</dt>
      <dd>{twin.traffic?.source === "observed" ? "observed by fixed phones" : "user-estimated"}</dd>
    </div>
  );
}

const ruleIcon = (c: VerdictCheck) =>
  c.passed === true ? (
    <span style={{ color: "var(--green)" }}>
      <Icon name="check" size={13} stroke={2.6} />
    </span>
  ) : c.passed === false ? (
    <span style={{ color: "var(--red)" }}>
      <Icon name="x" size={13} stroke={2.6} />
    </span>
  ) : (
    <span style={{ color: "var(--amber)" }}>
      <Icon name="alert" size={13} stroke={2.4} />
    </span>
  );

function checkValue(c: VerdictCheck) {
  if (c.value == null) return "unknown";
  if (typeof c.value === "number") return c.threshold != null && c.value <= 1 ? pct(c.value) : String(c.value);
  return c.value;
}

function VerdictRow({ v, robot, name, feas, batches, replayLink }: { v: Verdict; robot?: RobotSpec; name: string; feas?: Feasibility; batches: SimBatch[]; replayLink: (b: string, n?: number) => string }) {
  const [open, setOpen] = useState(false);
  const worst = feas?.legs.reduce<Feasibility["legs"][number] | null>((m, l) => (l.narrowest_gap_m != null && (!m || l.narrowest_gap_m < (m.narrowest_gap_m ?? 99)) ? l : m), null);
  const run = batches.find((b) => b.robot_id === v.robot_id && b.density === "normal" && b.geometry_bound === "nominal") ?? batches.find((b) => b.robot_id === v.robot_id);
  const failing = [...v.as_is].find((c) => c.passed !== true);
  return (
    <div className="verdict-row">
      <div>
        <div style={{ fontWeight: 600 }}>{name}</div>
        <div className="small muted num">{robot ? footprintText(robot) : ""}</div>
      </div>
      <div>
        <VerdictBadge label={v.label} size="lg" />
      </div>
      <div className="stack-sm" style={{ gap: 4 }}>
        <div>{v.main_reason}</div>
        <div className="small muted">
          Rule: {v.label === "Cleared" ? "every check passed as-is" : failing ? failing.rule : v.as_is[0]?.rule}
          {failing?.fact_ids?.length ? (
            <>
              {" "}
              <FactChips ids={failing.fact_ids} />
            </>
          ) : null}
        </div>
        {worst && (
          <div className="small num">
            Narrowest gap on route: <strong>{metres(worst.narrowest_gap_m)}</strong>
            {worst.required_gap_m != null && <span className="muted"> · needs {metres(worst.required_gap_m)}</span>}
            <span className="muted">
              {" "}
              · {worst.from_wp} → {worst.to_wp}
            </span>
          </div>
        )}
        <div className="row" style={{ gap: 8 }}>
          <button className="btn sm ghost" onClick={() => setOpen((x) => !x)}>
            {open ? "Hide checks" : "Show checks"}
          </button>
          {run && (
            <Link className="btn sm ghost" to={replayLink(run.batch_id)}>
              <Icon name="play" size={12} /> Watch a run
            </Link>
          )}
        </div>
        {open && (
          <div className="grid2" style={{ gap: 12 }}>
            <div>
              <h4>As-is</h4>
              <ul className="check-list">
                {v.as_is.map((c, i) => (
                  <li key={i}>
                    {ruleIcon(c)}
                    <span>
                      {c.rule}: <span className="num">{checkValue(c)}</span> <FactChips ids={c.fact_ids ?? []} />
                    </span>
                  </li>
                ))}
              </ul>
            </div>
            {!!v.with_changes?.length && (
              <div>
                <h4>With changes</h4>
                <ul className="check-list">
                  {v.with_changes.map((c, i) => (
                    <li key={i}>
                      {ruleIcon(c)}
                      <span>
                        {c.rule}: <span className="num">{checkValue(c)}</span> <FactChips ids={c.fact_ids ?? []} />
                      </span>
                    </li>
                  ))}
                </ul>
                {!!v.required_changes?.length && <div className="small muted">Required: {v.required_changes.map(describeChange).join("; ")}</div>}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export function describeChange(c: Record<string, unknown>): string {
  if (typeof c.measure_threshold === "string") return `measure threshold ${c.measure_threshold}`;
  const parts: string[] = [];
  const mv = c.move as Record<string, { dx?: number; dy?: number; dyaw?: number }> | undefined;
  if (mv) for (const [k, d] of Object.entries(mv)) parts.push(`move ${k} by ${Math.hypot(d.dx ?? 0, d.dy ?? 0).toFixed(2)} m${d.dyaw ? ` and ${Math.round(((d.dyaw ?? 0) * 180) / Math.PI)}°` : ""}`);
  const rm = c.remove as string[] | undefined;
  if (rm?.length) parts.push(`remove ${rm.join(", ")}`);
  if (typeof c.object_id === "string") parts.push(`move ${c.object_id}`);
  return parts.join(", ") || JSON.stringify(c);
}

function citationQuotes(c: Citation): string[] {
  const all = [...(c.quote ? [c.quote] : []), ...(c.quotes ?? [])].filter((q) => q && q.trim());
  return [...new Set(all)].slice(0, 4);
}

function factFor(facts: Fact[], ref: string) {
  return facts.find((f) => f.origin.type === "sim_batch" && f.origin.ref === ref && f.unit === "ratio");
}

function ComparisonTable({ verdicts, robots, feas, batches, replayLink, doc }: { verdicts: Verdict[]; robots: Map<string, RobotSpec>; feas: Map<string, Feasibility>; batches: SimBatch[]; replayLink: (b: string, n?: number) => string; doc: AnalysisDoc }) {
  const { facts } = useFactsList();
  const ids = verdicts.length ? verdicts.map((v) => v.robot_id) : [...new Set(batches.map((b) => b.robot_id))];
  const dens = (["quiet", "normal", "peak"] as Density[]).filter((d) => batches.some((b) => b.density === d));
  const get = (rid: string, d: Density, b: string) => batches.find((x) => x.robot_id === rid && x.density === d && x.geometry_bound === b);
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>Robot</th>
            <th>Verdict</th>
            <th>Narrowest gap</th>
            {dens.map((d) => (
              <th key={d} className="right">
                Success, {d}
              </th>
            ))}
            <th className="right">Median mission</th>
            <th className="right">Near-misses</th>
            <th>Price</th>
          </tr>
        </thead>
        <tbody>
          {ids.map((rid) => {
            const r = robots.get(rid);
            const v = verdicts.find((x) => x.robot_id === rid);
            const f = feas.get(rid);
            const worst = f?.legs.reduce<number | null>((m, l) => (l.narrowest_gap_m != null && (m == null || l.narrowest_gap_m < m) ? l.narrowest_gap_m : m), null);
            const nn = get(rid, "normal", "nominal");
            const roi = doc.roi?.[rid];
            return (
              <tr key={rid}>
                <td style={{ minWidth: 190 }}>
                  <div style={{ fontWeight: 600 }}>{r ? `${r.vendor} ${r.model}` : rid}</div>
                  <div className="tiny muted">{r ? footprintText(r) : ""}</div>
                </td>
                <td>{v && <VerdictBadge label={v.label} />}</td>
                <td className="num">
                  {metres(worst)}
                  {f?.legs[0]?.required_gap_m != null && <div className="tiny muted">needs {metres(f.legs[0].required_gap_m)}</div>}
                </td>
                {dens.map((d) => {
                  const nom = get(rid, d, "nominal");
                  const con = get(rid, d, "conservative");
                  const opt = get(rid, d, "optimistic");
                  if (!nom?.results) return <td key={d} className="right muted">–</td>;
                  const fact = factFor(facts, nom.batch_id);
                  return (
                    <td key={d} className="right num">
                      <Link to={replayLink(nom.batch_id)} title="Open a replay of this batch" style={{ fontWeight: 600 }}>
                        {pct(nom.results.success_rate)}
                      </Link>
                      {fact && <FactChips ids={[fact.fact_id]} />}
                      <div className="tiny muted">
                        {pct(con?.results?.success_rate)}–{pct(opt?.results?.success_rate)} · n={nom.results.n_episodes ?? nom.n_episodes}
                      </div>
                    </td>
                  );
                })}
                <td className="right num">{nn?.results?.median_mission_s != null ? `${Math.round(nn.results.median_mission_s)} s` : "–"}</td>
                <td className="right num">{nn?.results ? (nn.results.near_miss_per_mission ?? 0).toFixed(2) : "–"}</td>
                <td className="small">
                  {r?.price?.value != null ? money(r.price.value, r.price.currency) : <span className="muted">{r?.price?.note || "price on request"}</span>}
                  {roi?.payback_months != null && <div className="tiny muted">payback {roi.payback_months.toFixed(1)} mo</div>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function useFactsList() {
  const { facts } = useFacts();
  return { facts: [...facts.values()] };
}

function PinchMap({ twin, grid, batches, robotName, md }: { twin: Twin | null; grid: OccGrid | null; batches: SimBatch[]; robotName: (r: string) => string; md: string }) {
  const robotIds = [...new Set(batches.map((b) => b.robot_id))];
  const [robot, setRobot] = useState(robotIds[0] ?? "");
  const [density, setDensity] = useState<Density>("normal");
  const batch = batches.find((b) => b.robot_id === robot && b.density === density && b.geometry_bound === "nominal");
  const heat = useAsync(async () => (batch && grid ? api.getPinchMap(batch, grid) : null), [batch?.batch_id, grid]);
  if (!twin || !grid) return <div className="card-body muted">Twin not available.</div>;
  return (
    <div className="card-body stack">
      <div className="row between">
        <select className="select sm" style={{ width: 260 }} value={robot} onChange={(e) => setRobot(e.target.value)}>
          {robotIds.map((r) => (
            <option key={r} value={r}>
              {robotName(r)}
            </option>
          ))}
        </select>
        <div className="seg">
          {(["quiet", "normal", "peak"] as Density[]).map((d) => (
            <button key={d} className={density === d ? "on" : ""} onClick={() => setDensity(d)}>
              {d}
            </button>
          ))}
        </div>
      </div>
      <TwinView
        twin={twin}
        grid={grid}
        height={380}
        heat={heat.data ?? null}
        dimGrid
        legend={
          <>
            <span>
              <i style={{ background: "rgba(255,190,60,.6)" }} />
              some waiting
            </span>
            <span>
              <i style={{ background: "rgba(255,60,30,.85)" }} />
              robots wait here most
            </span>
            {!heat.loading && !heat.data && <span>Pinch Map not recorded for this batch</span>}
          </>
        }
      />
      {md && <Markdown md={md} />}
    </div>
  );
}

const OUTCOME: Record<string, string> = { supported: "green", refuted: "red", inconclusive: "amber" };
const KIND_LABEL: Record<Experiment["kind"], string> = {
  reduce_pedestrians_zone: "Fewer pedestrians in a zone",
  move_object: "Move an object",
  remove_object: "Remove an object",
  change_density: "Change traffic level",
  swap_robot: "Try another robot",
  measure_threshold: "Measure a threshold",
  other: "Other probe",
};

function InvestigationPanel({ inv, robotName, replayLink, hlStep, md }: { inv: Investigation | null; robotName: (r: string) => string; replayLink: (b: string, n?: number) => string; hlStep: number | null; md: { title: string; body: string }[] }) {
  const [trace, setTrace] = useState(false);
  useEffect(() => {
    if (hlStep != null) setTrace(true);
  }, [hlStep]);
  if (!inv || !(inv.experiments ?? []).length)
    return (
      <div className="card-body stack">
        {md.length ? (
          md.map((s, i) => <Markdown key={i} md={s.body} />)
        ) : (
          <div className="small muted">
            The investigation loop runs simulator experiments on robots that can drive the route but fall short of the success rule. In this check every simulated robot met the rule or was stopped earlier by geometry, so no experiment was needed.
          </div>
        )}
      </div>
    );
  return (
    <div className="card-body stack">
      <div className="small muted">Hypothesis, experiment, result, conclusion. Every number below comes from a simulation probe; the model may cite only these facts.</div>
      {(inv.experiments ?? []).map((x) => (
        <div key={x.experiment_id} className="stack-sm">
          <div className="row between">
            <span className="small muted">
              {robotName(x.robot_id)} · {x.experiment_id}
            </span>
            <span className="row" style={{ gap: 6 }}>
              <span className={`badge ${OUTCOME[x.outcome ?? "inconclusive"]}`}>{x.outcome ?? "inconclusive"}</span>
              <span className="badge">{x.next_action === "dig_further" ? "dig further" : "conclude"}</span>
            </span>
          </div>
          <div className="exp">
            <div>
              <div className="lbl">Hypothesis</div>
              {x.hypothesis}
            </div>
            <div>
              <div className="lbl">Experiment</div>
              {KIND_LABEL[x.kind]}
              {x.change && Object.keys(x.change).length ? <div className="tiny muted mono" style={{ marginTop: 3, wordBreak: "break-all" }}>{describeChange(x.change)}</div> : null}
            </div>
            <div>
              <div className="lbl">Result</div>
              {x.result_fact_ids?.length ? <FactChips ids={x.result_fact_ids} /> : <span className="muted">awaiting measurement</span>}
              {x.baseline_fact_ids?.length ? (
                <div className="tiny muted" style={{ marginTop: 3 }}>
                  baseline <FactChips ids={x.baseline_fact_ids} />
                </div>
              ) : null}
              {x.batch_id && (
                <div>
                  <Link className="tiny" to={replayLink(x.batch_id)}>
                    replay probe
                  </Link>
                </div>
              )}
            </div>
            <div>
              <div className="lbl">Conclusion</div>
              <Markdown md={x.conclusion || "–"} />
            </div>
          </div>
        </div>
      ))}
      {!!inv.findings?.length && (
        <div>
          <h4 style={{ marginBottom: 8 }}>Findings</h4>
          {inv.findings.map((f) => (
            <div key={f.finding_id} className="row" style={{ alignItems: "flex-start", marginBottom: 8 }}>
              <span className={`badge ${f.confirmed_by_probe ? "green" : "amber"}`}>{f.confirmed_by_probe ? "Confirmed by probe" : "Not yet confirmed"}</span>
              <div style={{ flex: 1 }}>
                <Markdown md={f.claim} />
              </div>
            </div>
          ))}
        </div>
      )}
      {!(inv.experiments ?? []).length &&
        md.map((s, i) => (
          <Markdown key={i} md={s.body} />
        ))}
      <div>
        <button className="btn sm" onClick={() => setTrace((t) => !t)}>
          {trace ? "Hide" : "Show"} full trace ({inv.steps?.length ?? 0} steps)
        </button>
        <span className="small muted" style={{ marginLeft: 10 }}>
          Stop reason: {inv.stop_reason || "–"}
        </span>
      </div>
      {trace && (
        <div className="timeline">
          {(inv.steps ?? []).map((s) => (
            <div key={s.i} className={`tl-item ${hlStep === s.i ? "hl" : ""}`}>
              <div className="row" style={{ gap: 8 }}>
                <span className="mono small faint">#{s.i}</span>
                {s.tool && <span className="badge blue mono">{s.tool}</span>}
                {s.fact_ids?.length ? <FactChips ids={s.fact_ids} /> : null}
              </div>
              {s.thought && <div style={{ marginTop: 4 }}>{s.thought}</div>}
              {s.args && Object.keys(s.args).length > 0 && <pre className="json">args {JSON.stringify(s.args, null, 1)}</pre>}
              {s.result_summary && Object.keys(s.result_summary).length > 0 && <pre className="json">result {JSON.stringify(s.result_summary, null, 1)}</pre>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function applyChange(twin: Twin, c: OptimizerChange): Primitive[] {
  const mv = c.change.move ?? {};
  const rm = new Set(c.change.remove ?? []);
  return [
    ...(twin.primitives ?? [])
      .filter((p) => !rm.has(p.id))
      .map((p) => {
        const d = mv[p.id];
        if (!d) return p;
        return { ...p, pose: { ...p.pose, xyz: [p.pose.xyz[0] + (d.dx ?? 0), p.pose.xyz[1] + (d.dy ?? 0), p.pose.xyz[2]] as [number, number, number], yaw: (p.pose.yaw ?? 0) + (d.dyaw ?? 0) } };
      }),
    ...(c.change.add_boxes ?? []),
  ];
}

function ChangesPanel({ changes, twin, grid, robotName, report, replayLink, md }: { changes: OptimizerChange[]; twin: Twin | null; grid: OccGrid | null; robotName: (r: string) => string; report: ReportResponse; replayLink: (b: string, n?: number) => string; md: string }) {
  const [sel, setSel] = useState(0);
  const [after, setAfter] = useState(true);
  if (!changes.length) return <div className="card-body">{md ? <Markdown md={md} /> : <span className="muted">No layout change is recommended.</span>}</div>;
  const c = changes[Math.min(sel, changes.length - 1)];
  const moved = twin && after ? applyChange(twin, c) : null;
  const factAfter = c.batch_ids?.[1] ? report.facts.find((f) => f.origin.ref === c.batch_ids![1]) : undefined;
  const factBefore = c.batch_ids?.[0] ? report.facts.find((f) => f.origin.ref === c.batch_ids![0] && f.unit === "ratio") : undefined;
  return (
    <div className="card-body stack">
      <div className="grid2" style={{ gridTemplateColumns: "minmax(0,0.9fr) minmax(0,1.1fr)" }}>
        <div className="stack-sm">
          {changes.map((x, i) => (
            <button key={i} className={`card card-pad ${i === sel ? "on" : ""}`} style={{ textAlign: "left", cursor: "pointer", borderColor: i === sel ? "var(--accent)" : undefined, font: "inherit", color: "inherit" }} onClick={() => setSel(i)}>
              <div style={{ fontWeight: 600 }}>{x.description}</div>
              <div className="small muted">{x.robot_id ? robotName(x.robot_id) : ""}</div>
              <div className="row num" style={{ marginTop: 6, gap: 8 }}>
                <span className="badge">{pct(x.before.success_rate)}</span>
                <Icon name="chevronRight" size={14} />
                <span className="badge green">{pct(x.after.success_rate)}</span>
                <span className="small muted">
                  n={x.after.n_episodes} · normal traffic
                </span>
              </div>
            </button>
          ))}
          <div className="small">
            Success {pct(c.before.success_rate)} {factBefore && <FactChips ids={[factBefore.fact_id]} />} → {pct(c.after.success_rate)} {factAfter && <FactChips ids={[factAfter.fact_id]} />}
            {c.batch_ids?.[1] && (
              <>
                {" "}
                · <Link to={replayLink(c.batch_ids[1])}>replay after</Link>
              </>
            )}
          </div>
          {md && <Markdown md={md} />}
        </div>
        <div className="stack-sm">
          <div className="seg" style={{ alignSelf: "flex-start" }}>
            <button className={!after ? "on" : ""} onClick={() => setAfter(false)}>
              Before
            </button>
            <button className={after ? "on" : ""} onClick={() => setAfter(true)}>
              After
            </button>
          </div>
          {twin && grid && <TwinView twin={twin} grid={grid} height={340} movedPrimitives={moved} highlightObjects={Object.keys(c.change.move ?? {})} />}
        </div>
      </div>
    </div>
  );
}

function PaybackPanel({ doc, robotName, md }: { doc: AnalysisDoc; robotName: (r: string) => string; md: string }) {
  const roi = Object.entries(doc.roi ?? {});
  const cur = (doc.inputs?.business?.currency as string) ?? "USD";
  return (
    <div className="card-body stack">
      {md && <Markdown md={md} />}
      {roi.length > 0 && (
        <div className="table-wrap card">
          <table className="table">
            <thead>
              <tr>
                <th>Robot</th>
                <th className="right">Automatable share</th>
                <th className="right">Monthly saving</th>
                <th className="right">Running cost</th>
                <th className="right">Payback</th>
              </tr>
            </thead>
            <tbody>
              {roi.map(([rid, r]) => (
                <tr key={rid}>
                  <td>{robotName(rid)}</td>
                  <td className="right num">{pct(r.inputs.automatable_share as number)}</td>
                  <td className="right num">{money(r.monthly_saving, cur)}</td>
                  <td className="right num">{money(r.inputs.monthly_running_cost as number, cur)}/mo</td>
                  <td className="right num">{r.payback_months != null ? `${r.payback_months.toFixed(1)} months` : <span className="muted">{r.inputs.price == null && r.inputs.monthly_lease == null ? "price on request" : r.inputs.monthly_lease != null ? "lease" : "–"}</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {roi[0] && (
        <div className="stack-sm">
          <div className="note mono" style={{ whiteSpace: "pre-wrap" }}>{roi[0][1].formula}</div>
          <div className="small muted">automatable share = simulated success (nominal, normal traffic) × fraction of task legs feasible · days per month = days per week × 52 / 12</div>
        </div>
      )}
    </div>
  );
}

function HazardList({ twin, robots, onShow }: { twin: Twin | null; robots: RobotSpec[]; onShow: (t: MeasurementTarget, label: string) => void }) {
  const flagged = (twin?.thresholds ?? []).filter((t) => t.status === "flagged_manual" && t.height_m == null);
  const approx = robots.filter((r) => r.footprint_approx);
  const items = [
    ...(twin?.hazards ?? []).map((h) => ({ icon: "hazard", tone: "amber", text: `${h.label}`, sub: `vision model, ${Math.round((h.confidence ?? 0) * 100)}% confidence; confirm on site`, target: h.xy ? ({ kind: "point", xy: h.xy } as MeasurementTarget) : null })),
    ...flagged.map((t) => ({ icon: "ruler", tone: "amber", text: `Threshold ${t.id}: measure on site`, sub: "near the scan's noise floor; enter the height in the twin editor", target: { kind: "entity", id: t.id } as MeasurementTarget })),
    ...approx.map((r) => ({ icon: "robot", tone: "blue", text: `${r.vendor} ${r.model}: footprint from a bounding box`, sub: "vendor publishes overall dimensions only", target: null })),
  ];
  if (!items.length) return <div className="muted" style={{ marginBottom: 10 }}>No hazards or manual checks.</div>;
  return (
    <div className="stack-sm" style={{ marginBottom: 12 }}>
      {items.map((it, i) => (
        <div key={i} className="row between" style={{ padding: "6px 0", borderBottom: "1px solid var(--border)" }}>
          <span className="row" style={{ gap: 10 }}>
            <span style={{ color: `var(--${it.tone === "blue" ? "accent" : it.tone})` }}>
              <Icon name={it.icon} size={16} />
            </span>
            <span>
              <div>{it.text}</div>
              <div className="small muted">{it.sub}</div>
            </span>
          </span>
          {it.target && (
            <button className="btn sm ghost" onClick={() => onShow(it.target!, it.text)}>
              Show on twin
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

function UsagePanel({ usage }: { usage: UsageSummary | null }) {
  if (!usage) return <div className="card-body muted">Usage summary not available.</div>;
  const rows = usage.rows ?? [];
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>Tier</th>
            <th>Model / job</th>
            <th className="right">Calls</th>
            <th className="right">Tokens in</th>
            <th className="right">Tokens out</th>
            <th className="right">Cost</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td>{r.role ?? r.kind ?? "–"}</td>
              <td className="mono small">{r.model_id}</td>
              <td className="right num">{r.calls.toLocaleString()}</td>
              <td className="right num">{(r.tokens_in ?? 0).toLocaleString()}</td>
              <td className="right num">{(r.tokens_out ?? 0).toLocaleString()}</td>
              <td className="right num">{r.cost_usd != null ? `$${r.cost_usd.toFixed(4)}` : "–"}</td>
            </tr>
          ))}
          {usage.totals && (
            <tr>
              <td colSpan={2} style={{ fontWeight: 600 }}>
                Total{usage.mode === "offline" ? " (offline mode: no billed model calls)" : ""}
              </td>
              <td className="right num">{(usage.totals.calls ?? 0).toLocaleString()}</td>
              <td className="right num">{(usage.totals.tokens_in ?? 0).toLocaleString()}</td>
              <td className="right num">{(usage.totals.tokens_out ?? 0).toLocaleString()}</td>
              <td className="right num">{usage.totals.cost_usd != null ? `$${usage.totals.cost_usd.toFixed(4)}` : "–"}</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
