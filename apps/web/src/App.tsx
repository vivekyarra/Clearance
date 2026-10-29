import { lazy, Suspense, type ReactNode } from "react";
import { BrowserRouter, Link, NavLink, Outlet, Route, Routes } from "react-router-dom";
import { api, setOffline } from "./api";
import { Icon } from "./components/Icon";
import { Loading, ToastHost } from "./components/ui";
import { useTheme } from "./hooks/useTheme";
import { SitesPage } from "./pages/Sites";

const ConsolePage = lazy(() => import("./pages/Console"));
const EditorPage = lazy(() => import("./pages/Editor"));
const ReportPage = lazy(() => import("./pages/Report"));
const ReplayPage = lazy(() => import("./pages/Replay"));
const ObservePage = lazy(() => import("./pages/Observe"));
const JoystickPage = lazy(() => import("./pages/Joystick"));
const LivePage = lazy(() => import("./pages/Live"));
const CoachPage = lazy(() => import("./pages/Coach"));
const MarkerPage = lazy(() => import("./pages/Marker"));
const JoinPage = lazy(() => import("./pages/Join"));

function Shell() {
  const [theme, toggle] = useTheme();
  return (
    <div className="shell">
      <header className="topbar">
        <Link to="/" className="brand">
          <span className="brand-mark">
            <Icon name="chevronRight" size={16} stroke={3} />
          </span>
          Clearance
          <small className="hide-sm">Know if a robot fits before you buy it.</small>
        </Link>
        <nav className="nav">
          <NavLink to="/" end>
            Sites
          </NavLink>
        </nav>
        <div className="spacer" />
        {api.mode === "offline" ? (
          <button className="badge blue" style={{ cursor: "pointer" }} title="Bundled sample data. Click to switch to the live API." onClick={() => setOffline(false)}>
            <span className="dot" /> Offline mode
          </button>
        ) : (
          <button className="badge" style={{ cursor: "pointer" }} title="Connected to the Clearance API. Click to use bundled sample data." onClick={() => setOffline(true)}>
            <span className="dot" style={{ color: "var(--green)" }} /> Live API
          </button>
        )}
        <button className="btn ghost icon" onClick={toggle} title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}>
          <Icon name={theme === "dark" ? "sun" : "moon"} />
        </button>
      </header>
      <Outlet />
    </div>
  );
}

const S = ({ children }: { children: ReactNode }) => <Suspense fallback={<Loading />}>{children}</Suspense>;

export function App() {
  return (
    <ToastHost>
      <BrowserRouter basename={import.meta.env.BASE_URL.replace(/\/$/, "")}>
        <Routes>
          <Route element={<Shell />}>
            <Route path="/" element={<SitesPage />} />
            <Route path="/sites/:id" element={<S><ConsolePage /></S>} />
            <Route path="/sites/:id/editor" element={<S><EditorPage /></S>} />
            <Route path="/analyses/:id" element={<S><ReportPage /></S>} />
            <Route path="/replay/:batchId/:n" element={<S><ReplayPage /></S>} />
            <Route path="/live/:session" element={<S><LivePage /></S>} />
            <Route path="*" element={<div className="page empty">Page not found. <Link to="/">Back to sites</Link></div>} />
          </Route>
          <Route path="/observe/:site" element={<S><ObservePage /></S>} />
          <Route path="/joystick/:session" element={<S><JoystickPage /></S>} />
          <Route path="/coach/:site" element={<S><CoachPage /></S>} />
          <Route path="/marker/:site" element={<S><MarkerPage /></S>} />
          <Route path="/join/:siteId" element={<S><JoinPage /></S>} />
        </Routes>
      </BrowserRouter>
    </ToastHost>
  );
}
