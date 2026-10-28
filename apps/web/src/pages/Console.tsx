import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { openOperatorTokenDialog } from "../api/OperatorTokenDialog";
import { Icon } from "../components/Icon";
import { TwinView } from "../components/TwinView";
import { ErrorBox, Loading, NumberField, Progress, QR, ReferenceBadge, useToast } from "../components/ui";
import { useAsync, useInterval } from "../hooks/useAsync";
import { metres } from "../lib/stats";
import type { BusinessInputs, CaptureStatus, Density, DeviceTier, RobotSpec, SiteDetail, Twin } from "../types";
import { joinUrl } from "./Sites";

const TIER: Record<DeviceTier, { label: string; icon: string; role: string }> = {
  T1_LIDAR: { label: "T1 LiDAR", icon: "scan", role: "Scanner (LiDAR depth)" },
  T2_VIO_MONO: { label: "T2 VIO", icon: "scan", role: "Scanner (ARKit poses + depth)" },
  T3_FIXED_OBSERVER: { label: "T3 Observer", icon: "camera", role: "Fixed observer (traffic)" },
  T0_COACH: { label: "T0 Coach", icon: "phone", role: "Coach screen" },
};

export default function ConsolePage() {
  const { id = "" } = useParams();
  const site = useAsync(() => api.getSite(id), [id]);
  const twin = useAsync(async () => {
    const t = await api.getTwin(id);
    const g = t.occupancy?.width ? await api.occupancy(t) : null;
    return { twin: t, grid: g };
  }, [id]);
  const busy = site.data && ((site.data.captures ?? []).some((c) => c.status && !["done", "failed"].includes(c.status)) || (site.data.twin_status && site.data.twin_status.state !== "ready") || (site.data.analyses ?? []).some((a) => a.status === "running"));
  // Poll fast while uploads, twin builds or checks run, and slowly otherwise so newly joined phones appear.
  useInterval(() => site.reload(), busy ? 4000 : 10000, true);
  const hasTwin = !!twin.data?.twin?.occupancy?.width;
  useInterval(() => twin.reload(), 8000, !hasTwin);

  if (site.error) return <main className="page"><ErrorBox error={site.error} onRetry={site.reload} /></main>;
  if (!site.data) return <Loading />;
  const s = site.data;
  const t = twin.data?.twin;

  return (
    <main className="page">
      <div className="crumbs">
        <Link to="/">Sites</Link> / {s.name}
      </div>
      <div className="page-head">
        <div>
          <div className="row">
            <h1>{s.name}</h1>
            {s.geometry_source === "reference" && <ReferenceBadge />}
          </div>
          <div className="sub">
            {s.area_m2 ? `${s.area_m2} m² · ` : ""}
            {t?.version ? `Twin v${t.version}` : "No twin yet"}
            {s.twin_versions && s.twin_versions.length > 1 ? ` · ${s.twin_versions.length} versions` : ""}
          </div>
        </div>
        <div className="row">
          <Link className="btn" to={`/coach/${id}`} target="_blank">
            <Icon name="phone" size={15} /> Coach screen
          </Link>
          {t?.version ? (
            <Link className="btn" to={`/sites/${id}/editor`}>
              <Icon name="map" size={15} /> Edit twin
            </Link>
          ) : null}
          <a className="btn primary" href="#check">
            Run Clearance check
          </a>
        </div>
      </div>

      <div className="grid2" style={{ gridTemplateColumns: "minmax(0, 0.9fr) minmax(0, 1.1fr)" }}>
        <CapturePanel site={s} />
        <TwinPanel site={s} twin={t} grid={twin.data?.grid ?? null} loading={twin.loading} />
      </div>

      <div className="grid2" style={{ marginTop: 16, gridTemplateColumns: "minmax(0, 1.25fr) minmax(0, 0.75fr)" }} id="check">
        {t?.version ? <CheckForm siteId={id} twin={t} kind={s.kind} /> : <div className="card card-pad muted">Run a Clearance check once the twin is ready.</div>}
        <div className="stack">
          <ChecksList site={s} />
          {t?.version ? <LiveCard siteId={id} twin={t} /> : null}
        </div>
      </div>
    </main>
  );
}

function CapturePanel({ site }: { site: SiteDetail }) {
  const caps = site.captures ?? [];
  const [showJoin, setShowJoin] = useState(!caps.length);
  // A join URL without its site token lets a phone open the site but not upload; show it only when it carries one.
  const joinReady = site.geometry_source === "reference" || (site.join_url ?? "").includes("token=");
  return (
    <section className="card">
      <div className="card-head">
        <h3>Capture</h3>
        <button className="btn sm" onClick={() => setShowJoin((x) => !x)}>
          <Icon name="qr" size={14} /> Join a phone
        </button>
      </div>
      <div className="card-body">
        {showJoin && (
          <div className="row" style={{ alignItems: "flex-start", gap: 16, marginBottom: 14 }}>
            {joinReady ? (
              <QR text={site.join_url ?? joinUrl(site.site_id, site.name)} size={124} />
            ) : (
              <div className="stack-sm small" style={{ width: 124 }}>
                <div>Phones need a join code from the operator.</div>
                <button className="btn sm primary" onClick={() => void openOperatorTokenDialog("Enter the operator token to show the phone join code.").then((t) => t && window.location.reload())}>
                  Show join code
                </button>
              </div>
            )}
            <div className="stack-sm small" style={{ flex: 1, minWidth: 180 }}>
              <div>Scan with each phone. Android phones on a stand become fixed observers: they lock their position from the floor marker, then record traffic with faces blurred on the device.</div>
              <div className="row">
                <a className="btn sm" href={api.markerPdfUrl(site.site_id)} target="_blank" rel="noreferrer">
                  <Icon name="doc" size={14} /> Marker PDF
                </a>
                <Link className="btn sm ghost" to={`/observe/${site.site_id}`} target="_blank">
                  Open observer page
                </Link>
              </div>
            </div>
          </div>
        )}
        {!caps.length && <div className="note">No devices yet. Join the iPhone scanner first, then the observers.</div>}
        {caps.map((c) => (
          <DeviceRow key={c.capture_id} c={c} />
        ))}
        {site.twin_status && site.twin_status.state !== "ready" && (
          <div className="stack-sm" style={{ marginTop: 12 }}>
            <div className="row between small">
              <span>Building twin: {site.twin_status.message ?? site.twin_status.state.replace(/_/g, " ")}</span>
              <span className="num muted">{Math.round((site.twin_status.progress ?? 0) * 100)}%</span>
            </div>
            <Progress value={site.twin_status.progress ?? 0} />
          </div>
        )}
      </div>
    </section>
  );
}

function DeviceRow({ c }: { c: CaptureStatus }) {
  const tier = TIER[c.device.tier];
  const done = c.status === "done" || c.status === "uploaded";
  return (
    <div className="device">
      <div className="ic">
        <Icon name={tier.icon} size={18} />
      </div>
      <div style={{ minWidth: 0 }}>
        <div className="row" style={{ gap: 8 }}>
          <span style={{ fontWeight: 600 }}>{c.device.model}</span>
          <span className="badge">{tier.label}</span>
        </div>
        <div className="small muted">
          {tier.role}
          {c.duration_s ? ` · ${fmtDuration(c.duration_s)}` : ""}
          {c.privacy?.faces_blurred_on_device ? " · faces blurred on device" : ""}
        </div>
        {!done && c.progress != null && (
          <div style={{ marginTop: 6 }}>
            <Progress value={c.progress} />
          </div>
        )}
      </div>
      <span className={`badge ${done ? "green" : c.status === "failed" ? "red" : "blue"}`}>{c.status === "done" ? "Processed" : (c.status ?? "registered").replace(/_/g, " ")}</span>
    </div>
  );
}

const fmtDuration = (s: number) => (s >= 60 ? `${Math.floor(s / 60)} min ${Math.round(s % 60)} s` : `${Math.round(s)} s`);

function TwinPanel({ site, twin, grid, loading }: { site: SiteDetail; twin?: Twin; grid: import("../lib/grid").OccGrid | null; loading: boolean }) {
  const prims = twin?.primitives ?? [];
  const low = prims.filter((p) => (p.confidence ?? 1) < 0.6).length;
  const flagged = (twin?.thresholds ?? []).filter((t) => t.status === "flagged_manual" && t.height_m == null);
  const acc = twin?.accuracy;
  return (
    <section className="card">
      <div className="card-head">
        <h3>Twin of the real room</h3>
        {twin?.version ? (
          <Link className="btn sm" to={`/sites/${site.site_id}/editor`}>
            Review in editor
          </Link>
        ) : null}
      </div>
      <div className="card-body stack">
        {loading && !twin ? <div className="skeleton" style={{ height: 280 }} /> : twin && grid ? <TwinView twin={twin} grid={grid} height={280} /> : <div className="empty">The twin appears here about 10 minutes after the scan uploads.</div>}
        {twin?.version ? (
          <div className="grid3" style={{ gap: 10 }}>
            <div className="stat">
              <span className="v">{acc?.median_abs_err_m != null ? `${(acc.median_abs_err_m * 100).toFixed(1)} cm` : "–"}</span>
              <span className="l">
                Median scan error{acc?.p90_abs_err_m != null ? ` (p90 ${(acc.p90_abs_err_m * 100).toFixed(1)} cm)` : ""} · {acc?.method === "tape_check" ? "tape check" : acc?.method === "reference_ground_truth" ? "ground truth" : "estimated"}
              </span>
            </div>
            <div className="stat">
              <span className="v">{prims.length}</span>
              <span className="l">Objects{low ? ` · ${low} to confirm` : ""}</span>
            </div>
            <div className="stat">
              <span className="v">{(twin.waypoints ?? []).length}</span>
              <span className="l">Waypoints{flagged.length ? ` · ${flagged.length} threshold to measure` : ""}</span>
            </div>
          </div>
        ) : null}
        {flagged.length > 0 && (
          <div className="note row" style={{ background: "var(--amber-soft)", color: "var(--amber)" }}>
            <Icon name="ruler" size={15} /> Threshold {flagged.map((f) => f.id).join(", ")}: measure on site and enter the height in the editor before trusting the verdict.
          </div>
        )}
        <div className="row small muted" style={{ gap: 16 }}>
          <span className="row" style={{ gap: 6 }}>
            <Icon name="ar" size={15} /> AR replay: open the Clearance iPhone app and point it at the floor marker to watch any run in this room.
          </span>
        </div>
      </div>
    </section>
  );
}

const DEFAULT_BUSINESS: BusinessInputs = {
  staff_cost_per_h: 18,
  task_hours_per_day: 6,
  days_per_week: 6,
  price_mode: "public_price",
  robot_price: null,
  monthly_lease: null,
  maintenance_pct_per_year: 10,
  currency: "USD",
};

function CheckForm({ siteId, twin, kind }: { siteId: string; twin: Twin; kind?: string }) {
  const wps = twin.waypoints ?? [];
  const docks = wps.filter((w) => w.kind === "dock");
  const pickups = wps.filter((w) => w.kind === "pickup");
  const dests = wps.filter((w) => w.kind === "dest");
  const robots = useAsync(() => api.listRobots(), []);
  const [dock, setDock] = useState(docks[0]?.id ?? "");
  const [pickup, setPickup] = useState(pickups[0]?.id ?? "");
  const [selDest, setSelDest] = useState<string[]>(dests.map((d) => d.id));
  const [ret, setRet] = useState(true);
  const [selRobots, setSelRobots] = useState<string[] | null>(null);
  const [dens, setDens] = useState<Density[]>(["quiet", "normal", "peak"]);
  const [b, setB] = useState<BusinessInputs>(DEFAULT_BUSINESS);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Error>();
  const nav = useNavigate();
  const toast = useToast();
  const wantClass = kind === "storeroom" || kind === "warehouse" ? "B_amr" : "A_service";
  const verified = useMemo(() => (robots.data ?? []).filter((r) => r.verified_by_human), [robots.data]);
  useEffect(() => {
    if (robots.data && selRobots === null) setSelRobots(verified.map((r) => r.robot_id));
  }, [robots.data, verified, selRobots]);

  const sequence = [dock, pickup, ...selDest, ...(ret ? [dock] : [])].filter(Boolean);
  const valid = dock && pickup && selDest.length > 0 && (selRobots?.length ?? 0) > 0 && dens.length > 0 && [b.staff_cost_per_h, b.task_hours_per_day, b.days_per_week, b.maintenance_pct_per_year].every(Number.isFinite);

  const submit = async () => {
    setBusy(true);
    setErr(undefined);
    try {
      const r = await api.startAnalysis(siteId, {
        task: { type: "delivery", sequence },
        template: "deliver_pickup_tables_dock",
        robots: selRobots ?? [],
        business: b,
        densities: dens,
        twin_version: twin.version,
      });
      toast("Clearance check started");
      nav(`/analyses/${r.analysis_id}`, { state: { watch: true } });
    } catch (e) {
      setErr(e as Error);
    } finally {
      setBusy(false);
    }
  };

  const toggle = <T,>(arr: T[], v: T) => (arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);
  const label = (r: RobotSpec) => `${r.vendor} ${r.model}`;

  return (
    <section className="card">
      <div className="card-head">
        <h3>Run Clearance check</h3>
        <span className="small muted">Twin v{twin.version}</span>
      </div>
      <div className="card-body stack">
        <div>
          <h4 style={{ marginBottom: 8 }}>Job</h4>
          <div className="grid3" style={{ gap: 10 }}>
            <label className="field">
              <span>Template</span>
              <select className="select" value="deliver" disabled>
                <option value="deliver">Deliver: pickup → tables → dock</option>
              </select>
            </label>
            <label className="field">
              <span>Dock</span>
              <select className="select" value={dock} onChange={(e) => setDock(e.target.value)}>
                {docks.map((w) => (
                  <option key={w.id}>{w.id}</option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Pickup</span>
              <select className="select" value={pickup} onChange={(e) => setPickup(e.target.value)}>
                {pickups.map((w) => (
                  <option key={w.id}>{w.id}</option>
                ))}
              </select>
            </label>
          </div>
          <div className="field" style={{ marginTop: 10 }}>
            <span>Destinations</span>
            <div className="row" style={{ gap: 6 }}>
              {dests.map((d) => (
                <button key={d.id} type="button" className={`btn sm ${selDest.includes(d.id) ? "on" : ""}`} onClick={() => setSelDest(toggle(selDest, d.id))}>
                  {d.id}
                </button>
              ))}
              {!dests.length && <span className="small muted">Add destination waypoints in the editor first.</span>}
              <label className="check small" style={{ marginLeft: 8 }}>
                <input type="checkbox" checked={ret} onChange={(e) => setRet(e.target.checked)} /> Return to dock
              </label>
            </div>
          </div>
          <div className="small muted" style={{ marginTop: 8 }}>
            Route: <span className="mono">{sequence.join(" → ") || "–"}</span>
          </div>
        </div>
        <div>
          <h4 style={{ marginBottom: 8 }}>Robots</h4>
          {robots.error && <ErrorBox error={robots.error} onRetry={robots.reload} />}
          <div className="stack-sm">
            {verified.map((r) => (
              <label key={r.robot_id} className="check">
                <input type="checkbox" checked={selRobots?.includes(r.robot_id) ?? false} onChange={() => setSelRobots(toggle(selRobots ?? [], r.robot_id))} />
                <span style={{ flex: 1 }}>{label(r)}</span>
                <span className="small muted num">{footprintText(r)}</span>
                <span className={`badge ${r.class === wantClass ? "blue" : ""}`}>{r.class === "A_service" ? "Service" : "AMR"}</span>
              </label>
            ))}
            {robots.data && robots.data.length > verified.length && <span className="small muted">{robots.data.length - verified.length} more catalog entries are awaiting review.</span>}
          </div>
        </div>
        <div>
          <h4 style={{ marginBottom: 8 }}>Traffic levels</h4>
          <div className="seg">
            {(["quiet", "normal", "peak"] as Density[]).map((d) => (
              <button key={d} type="button" className={dens.includes(d) ? "on" : ""} onClick={() => setDens(toggle(dens, d))}>
                {d}
              </button>
            ))}
          </div>
          <span className="small muted" style={{ marginLeft: 10 }}>
            Traffic source: {twin.traffic?.source === "observed" ? "observed by fixed phones" : "your estimate"}
          </span>
        </div>
        <div>
          <h4 style={{ marginBottom: 8 }}>Business inputs</h4>
          <div className="grid3" style={{ gap: 10 }}>
            <NumberField label="Staff cost" value={b.staff_cost_per_h} onChange={(v) => setB({ ...b, staff_cost_per_h: v })} suffix={`${b.currency}/h`} step={0.5} min={0} />
            <NumberField label="Task hours" value={b.task_hours_per_day} onChange={(v) => setB({ ...b, task_hours_per_day: v })} suffix="h/day" step={0.5} min={0} />
            <NumberField label="Days open" value={b.days_per_week} onChange={(v) => setB({ ...b, days_per_week: v })} suffix="days/wk" min={1} />
            <label className="field">
              <span>Price</span>
              <select className="select" value={b.price_mode} onChange={(e) => setB({ ...b, price_mode: e.target.value as BusinessInputs["price_mode"] })}>
                <option value="public_price">Use published price</option>
                <option value="purchase">My purchase quote</option>
                <option value="lease">My monthly lease</option>
              </select>
            </label>
            {b.price_mode === "purchase" && <NumberField label="Purchase quote" value={b.robot_price ?? NaN} onChange={(v) => setB({ ...b, robot_price: Number.isFinite(v) ? v : null })} suffix={b.currency} step={100} min={0} />}
            {b.price_mode === "lease" && <NumberField label="Monthly lease" value={b.monthly_lease ?? NaN} onChange={(v) => setB({ ...b, monthly_lease: Number.isFinite(v) ? v : null })} suffix={`${b.currency}/mo`} step={10} min={0} />}
            <NumberField label="Maintenance" value={b.maintenance_pct_per_year} onChange={(v) => setB({ ...b, maintenance_pct_per_year: v })} suffix="% / yr" step={1} min={0} hint="Default 10%; replace with your quote" />
          </div>
        </div>
        {err && <ErrorBox error={err} />}
        <div className="row between">
          <span className="small muted">
            {selRobots?.length ?? 0} robots × {dens.length} traffic levels × 3 geometry bounds
          </span>
          <button className="btn primary" disabled={!valid || busy} onClick={submit}>
            {busy ? "Starting" : "Run Clearance check"}
          </button>
        </div>
      </div>
    </section>
  );
}

export function footprintText(r: RobotSpec): string {
  const p = r.footprint_polygon_m;
  if (!p?.length) return "footprint awaiting review";
  const l = Math.max(...p.map((q) => q[0])) - Math.min(...p.map((q) => q[0]));
  const w = Math.max(...p.map((q) => q[1])) - Math.min(...p.map((q) => q[1]));
  return `${metres(l)} × ${metres(w)}${r.footprint_approx ? " (box)" : ""}`;
}

function ChecksList({ site }: { site: SiteDetail }) {
  const list = site.analyses ?? (site.latest_analysis_id ? [{ analysis_id: site.latest_analysis_id, status: "done" }] : []);
  return (
    <section className="card">
      <div className="card-head">
        <h3>Clearance checks</h3>
      </div>
      <div className="card-body stack-sm">
        {!list.length && <span className="muted small">No checks yet.</span>}
        {list.map((a) => (
          <Link key={a.analysis_id} to={`/analyses/${a.analysis_id}`} className="row between" style={{ color: "inherit", padding: "6px 0" }}>
            <span className="row" style={{ gap: 8 }}>
              <Icon name="doc" size={15} />
              <span className="mono small">{a.analysis_id}</span>
            </span>
            <span className="row" style={{ gap: 8 }}>
              {"created_at" in a && a.created_at && <span className="small muted">{new Date(a.created_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}</span>}
              <span className={`badge ${a.status === "done" ? "green" : a.status === "failed" ? "red" : "blue"}`}>{a.status === "done" ? "Report ready" : a.status}</span>
            </span>
          </Link>
        ))}
      </div>
    </section>
  );
}

function LiveCard({ siteId, twin }: { siteId: string; twin: Twin }) {
  const robots = useAsync(() => api.listRobots(), []);
  const [robot, setRobot] = useState("");
  const [density, setDensity] = useState<Density>("normal");
  const [busy, setBusy] = useState(false);
  const nav = useNavigate();
  const toast = useToast();
  const list = (robots.data ?? []).filter((r) => r.verified_by_human);
  const start = async () => {
    setBusy(true);
    try {
      const r = await api.startLive({ site_id: siteId, robot_id: robot || list[0]?.robot_id, density, task: { type: "delivery", sequence: (twin.waypoints ?? []).map((w) => w.id) } });
      const joy = (r as { joystick_url?: string }).joystick_url;
      if (joy) {
        try { sessionStorage.setItem(`clearance.joy.${r.session_id}`, joy); } catch { /* storage unavailable */ }
      }
      nav(`/live/${r.session_id}`);
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="card">
      <div className="card-head">
        <h3>Live stress test</h3>
        <span className="badge">Manual test</span>
      </div>
      <div className="card-body stack-sm">
        <div className="small muted">Steer one pedestrian from a phone while the robot runs its normal controller and safety stop.</div>
        <div className="row">
          <select className="select sm" style={{ flex: 1 }} value={robot} onChange={(e) => setRobot(e.target.value)}>
            {list.map((r) => (
              <option key={r.robot_id} value={r.robot_id}>
                {r.vendor} {r.model}
              </option>
            ))}
          </select>
          <select className="select sm" style={{ width: 110 }} value={density} onChange={(e) => setDensity(e.target.value as Density)}>
            <option value="quiet">quiet</option>
            <option value="normal">normal</option>
            <option value="peak">peak</option>
          </select>
          <button className="btn sm" disabled={busy || !list.length} onClick={start}>
            <Icon name="joystick" size={14} /> Start
          </button>
        </div>
      </div>
    </section>
  );
}
