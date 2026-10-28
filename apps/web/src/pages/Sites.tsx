import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { Icon } from "../components/Icon";
import { TwinThumb } from "../components/TwinThumb";
import { ErrorBox, Modal, NumberField, QR, ReferenceBadge, useToast } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { siteJoinUrl } from "../lib/join";
import type { CreateSiteResponse, SiteKind, SiteSummary } from "../types";

const KIND_LABEL: Record<string, string> = {
  cafe: "Café",
  restaurant: "Restaurant",
  storeroom: "Storeroom",
  warehouse: "Small warehouse",
  corridor: "Corridor / office",
  office: "Office",
  other: "Other",
};

/** Join URL when the API did not send one (offline mode): the console's own /api proxy is the API base. */
export function joinUrl(siteId: string, name?: string) {
  return siteJoinUrl(location.origin, siteId, `${location.origin}/api`, name);
}

export function SitesPage() {
  const sites = useAsync(() => api.listSites(), []);
  const [creating, setCreating] = useState(false);
  const hero = sites.data?.find((s) => s.geometry_source === "reference" && s.latest_analysis_id) ?? sites.data?.[0];

  return (
    <main className="page">
      <section className="hero">
        <div>
          <span className="badge blue">
            <Icon name="phone" size={13} /> Phone scan to verdict
          </span>
          <h1 style={{ marginTop: 14 }}>Know if a robot fits before you buy it.</h1>
          <p className="lede">
            Walk your space with an ordinary phone. Clearance builds a measured twin of the real room, tests each robot's exact footprint against your aisles, doors and foot
            traffic, and plays the result back in AR where it will actually drive.
          </p>
          <div className="row">
            <button className="btn primary lg" onClick={() => setCreating(true)}>
              <Icon name="plus" /> New site
            </button>
            {hero?.latest_analysis_id && (
              <Link className="btn lg" to={`/analyses/${hero.latest_analysis_id}`}>
                See a Clearance Report
              </Link>
            )}
          </div>
        </div>
        <div className="card" style={{ overflow: "hidden" }}>
          {hero ? (
            <>
              <TwinThumb siteId={hero.site_id} height={250} />
              <div className="card-body row between">
                <div>
                  <div style={{ fontWeight: 600 }}>{hero.name}</div>
                  <div className="small muted">Top-down twin from one iPhone scan and two fixed observers</div>
                </div>
                <Link className="btn sm" to={`/sites/${hero.site_id}`}>
                  Open
                </Link>
              </div>
            </>
          ) : (
            <div className="skeleton" style={{ height: 300 }} />
          )}
        </div>
      </section>

      <section className="steps" style={{ marginBottom: 34 }}>
        {[
          ["Place the marker", "Print the floor marker and put it near the entrance. It becomes the room's origin and the accuracy check."],
          ["Scan the real room", "Walk the iPhone through the space. Android phones on stands record traffic with faces blurred on the device."],
          ["Review the twin", "Confirm furniture, mark what can move, place the dock, pickup and tables, and measure any flagged door threshold."],
          ["Run Clearance check", "Each robot gets one verdict with the reason, the narrowest gap on its route, and an AR replay in your room."],
        ].map(([t, d], i) => (
          <div className="step" key={t}>
            <div className="n">{i + 1}</div>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>{t}</div>
            <div className="small muted">{d}</div>
          </div>
        ))}
      </section>

      <div className="page-head">
        <div>
          <h2>Sites</h2>
          <div className="sub">Each site keeps its scans, twin versions and Clearance checks.</div>
        </div>
        <button className="btn" onClick={() => setCreating(true)}>
          <Icon name="plus" /> New site
        </button>
      </div>
      {sites.error && <ErrorBox error={sites.error} onRetry={sites.reload} />}
      <div className="grid3">
        {sites.loading && !sites.data && [0, 1, 2].map((i) => <div key={i} className="card skeleton" style={{ height: 240 }} />)}
        {sites.data?.map((s) => <SiteCard key={s.site_id} s={s} />)}
      </div>
      {sites.data && !sites.data.length && <div className="card empty">No sites yet. Create one to get a join QR code and a printable marker.</div>}
      {creating && <CreateSite onClose={() => setCreating(false)} onCreated={sites.reload} />}
    </main>
  );
}

function SiteCard({ s }: { s: SiteSummary }) {
  return (
    <Link to={`/sites/${s.site_id}`} className="card site-card">
      <div className="thumb">
        <TwinThumb siteId={s.site_id} height={150} />
      </div>
      <div className="card-body stack-sm">
        <div className="row between">
          <h3>{s.name}</h3>
          {s.geometry_source === "reference" && <ReferenceBadge />}
        </div>
        <div className="row small muted" style={{ gap: 14 }}>
          <span>{KIND_LABEL[s.kind ?? "other"] ?? s.kind}</span>
          {s.area_m2 != null && <span className="num">{s.area_m2} m²</span>}
          <span>{s.twin_version ? `Twin v${s.twin_version}` : "No twin yet"}</span>
        </div>
      </div>
    </Link>
  );
}

function CreateSite({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState<SiteKind>("cafe");
  const [area, setArea] = useState<number>(60);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Error>();
  const [done, setDone] = useState<CreateSiteResponse>();
  const nav = useNavigate();
  const toast = useToast();

  const submit = async () => {
    setBusy(true);
    setErr(undefined);
    try {
      const r = await api.createSite({ name: name.trim(), kind, area_m2: Number.isFinite(area) ? area : null });
      setDone(r);
      onCreated();
      toast("Site created");
    } catch (e) {
      setErr(e as Error);
    } finally {
      setBusy(false);
    }
  };

  if (done) {
    const join = done.qr_payload?.startsWith("http") ? done.qr_payload : joinUrl(done.site_id);
    return (
      <Modal
        title="Site ready: join your phones"
        onClose={onClose}
        footer={
          <>
            <button className="btn" onClick={onClose}>
              Close
            </button>
            <button className="btn primary" onClick={() => nav(`/sites/${done.site_id}`)}>
              Open console
            </button>
          </>
        }
      >
        <div className="row" style={{ alignItems: "flex-start", gap: 20 }}>
          <QR text={join} size={170} />
          <div className="stack-sm" style={{ flex: 1, minWidth: 200 }}>
            <div>Scan this code with each phone. The page detects the device and assigns its role (scanner, fixed observer or coach screen).</div>
            <code className="small" style={{ wordBreak: "break-all" }}>
              {join}
            </code>
            <div className="divider" />
            <div style={{ fontWeight: 600 }}>Floor marker</div>
            <div className="small muted">Print at 100% scale, measure the black square with a ruler, and tape it flat near the entrance.</div>
            <div className="row">
              <a className="btn sm" href={done.marker_pdf_url} target="_blank" rel="noreferrer">
                <Icon name="doc" size={14} /> Marker PDF
              </a>
              <a className="btn sm ghost" href={`/marker/${done.site_id}`} target="_blank" rel="noreferrer">
                Print page
              </a>
            </div>
          </div>
        </div>
      </Modal>
    );
  }

  return (
    <Modal
      title="New site"
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn primary" disabled={!name.trim() || busy} onClick={submit}>
            {busy ? "Creating" : "Create site"}
          </button>
        </>
      }
    >
      <div className="stack">
        <label className="field">
          <span>Site name</span>
          <input className="input" autoFocus value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && name.trim() && submit()} />
          <span className="hint">For example the café or storeroom name.</span>
        </label>
        <label className="field">
          <span>Type</span>
          <select className="select" value={kind} onChange={(e) => setKind(e.target.value as SiteKind)}>
            <option value="cafe">Café / restaurant</option>
            <option value="storeroom">Storeroom / small warehouse</option>
            <option value="corridor">Corridor / office</option>
            <option value="other">Other</option>
          </select>
        </label>
        <NumberField label="Floor area estimate" value={area} onChange={setArea} suffix="m²" min={1} />
        {err && <ErrorBox error={err} />}
      </div>
    </Modal>
  );
}
