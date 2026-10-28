import { Link, useParams } from "react-router-dom";
import { Icon } from "../components/Icon";
import { detectDevice } from "../lib/device";
import { appJoinLink } from "../lib/join";

/** Landing page of the site QR code (/join/{site_id}): routes each phone to its role. */
export default function JoinPage() {
  const { siteId = "" } = useParams();
  const dev = detectDevice(navigator.userAgent, navigator.maxTouchPoints);
  const appLink = appJoinLink(siteId, location.search, location.origin);
  const options = [
    { key: "ios", title: "Open in the Clearance app", text: "iPhone scanner: walks the room with ARKit and plays AR replays anchored to the marker.", icon: "scan", href: appLink },
    { key: "android", title: "Start observer", text: "Android phone on a stand: locks its position from the floor marker, then records traffic with faces blurred on the device.", icon: "camera", to: `/observe/${encodeURIComponent(siteId)}` },
    { key: "other", title: "Coach screen", text: "Any spare screen: shows scan coverage and prompts for the person scanning.", icon: "phone", to: `/coach/${encodeURIComponent(siteId)}` },
  ];
  const primary = options.find((o) => o.key === dev)!;
  const rest = options.filter((o) => o.key !== dev);
  return (
    <div className="phone" style={{ padding: "28px 18px", gap: 18, overflow: "auto", background: "radial-gradient(circle at 50% 0%, #1a2440, #07090d)" }}>
      <div>
        <span className="pill">Clearance</span>
        <h1 style={{ fontSize: 28, marginTop: 14 }}>Join this site</h1>
        <div style={{ opacity: 0.7, marginTop: 4 }}>
          Site <span className="mono">{siteId}</span>
        </div>
      </div>
      <Choice o={primary} big />
      <div style={{ opacity: 0.6, fontSize: 13, marginTop: 6 }}>Other roles</div>
      {rest.map((o) => (
        <Choice key={o.key} o={o} />
      ))}
    </div>
  );
}

function Choice({ o, big }: { o: { title: string; text: string; icon: string; to?: string; href?: string }; big?: boolean }) {
  const body = (
    <div style={{ display: "flex", gap: 14, alignItems: "center", padding: big ? "20px 18px" : "14px 16px", borderRadius: 16, background: big ? "#fff" : "rgba(255,255,255,0.08)", color: big ? "#000" : "#fff" }}>
      <Icon name={o.icon} size={big ? 30 : 22} />
      <div style={{ flex: 1 }}>
        <div style={{ fontWeight: 700, fontSize: big ? 18 : 15 }}>{o.title}</div>
        <div style={{ fontSize: 13, opacity: 0.75 }}>{o.text}</div>
      </div>
      <Icon name="chevronRight" size={20} />
    </div>
  );
  return o.to ? (
    <Link to={o.to} style={{ textDecoration: "none" }}>
      {body}
    </Link>
  ) : (
    <a href={o.href} style={{ textDecoration: "none" }}>
      {body}
    </a>
  );
}
