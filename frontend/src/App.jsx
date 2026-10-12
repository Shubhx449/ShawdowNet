import { NavLink, Route, Routes } from "react-router-dom";
import { useBackendStatus } from "./useBackend";
import Dashboard from "./pages/Dashboard.jsx";
import Experiments from "./pages/Experiments.jsx";
import Scenarios from "./pages/Scenarios.jsx";
import Live from "./pages/Live.jsx";
import ExperimentDetail from "./pages/ExperimentDetail.jsx";
import NotFound from "./pages/NotFound.jsx";

// One row per page. `phase` is the roadmap phase that builds the real page.
export const PAGES = [
  { path: "/", label: "Dashboard", icon: "◈", element: <Dashboard /> },
  { path: "/experiments", label: "Experiments", icon: "▤", tag: "Mode 2",
    element: <Experiments /> },
  { path: "/live", label: "Live Network", icon: "◉", tag: "Mode 1",
    element: <Live /> },
  { path: "/scenarios", label: "Scenarios", icon: "▦",
    element: <Scenarios /> },
];

export default function App() {
  const status = useBackendStatus();
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">◭</span>
          <div>
            <div className="brand-name">ShadowNet</div>
            <div className="brand-sub">AEAR · LoRa mesh</div>
          </div>
        </div>
        <nav>
          {PAGES.map((p) => (
            <NavLink key={p.path} to={p.path} end={p.path === "/"}
              className={({ isActive }) => "nav-link" + (isActive ? " active" : "")}>
              <span className="nav-icon">{p.icon}</span>
              <span>{p.label}</span>
              {p.tag && <span className="nav-tag">{p.tag}</span>}
            </NavLink>
          ))}
        </nav>
        <div className={`status status-${status}`} title="Polled from /api/health every 5s">
          <span className="dot" />
          backend {status}
        </div>
      </aside>
      <main className="content">
        <Routes>
          {PAGES.map((p) => <Route key={p.path} path={p.path} element={p.element} />)}
          <Route path="/experiments/:id" element={<ExperimentDetail />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
    </div>
  );
}
