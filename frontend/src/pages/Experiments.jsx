import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { useApi } from "../useBackend";
import ScenarioFields from "../ScenarioFields.jsx";

function ScenarioForm({ defaults }) {
  const nav = useNavigate();
  const [sc, setSc] = useState(defaults);
  const [raw, setRaw] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function submit(e) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const r = await api("/experiments/run", { method: "POST", body: { scenario: sc, save_raw_events: raw, async: true } });
      nav(`/experiments/${r.experiment_id}`);
    } catch (err) { setError(err.message); setBusy(false); }
  }

  return (
    <form className="card" onSubmit={submit}>
      <h2>New experiment</h2>
      <ScenarioFields sc={sc} setSc={setSc} />
      <label className="checks">
        <input type="checkbox" checked={raw} onChange={(e) => setRaw(e.target.checked)} />
        Save raw events <span className="muted">(large on disk; needed only for later event inspection)</span>
      </label>
      {error && <p className="error">{error}</p>}
      <div className="actions">
        <button className="btn primary" disabled={busy || !sc.algorithms.length}>{busy ? "Starting…" : "Run experiment"}</button>
        <button type="button" className="btn" onClick={() => setSc(defaults)}>Reset to paper defaults</button>
      </div>
    </form>
  );
}

export default function Experiments() {
  const defaults = useApi("/defaults");
  const list = useApi("/experiments");
  useEffect(() => { const t = setInterval(list.reload, 4000); return () => clearInterval(t); }, [list.reload]);
  return (
    <div className="page">
      <header className="page-head"><h1>Experiments</h1><span className="pill">Mode 2</span></header>
      {defaults.error ? <p className="error">{defaults.error.message}</p>
        : defaults.data ? <ScenarioForm defaults={defaults.data} /> : <p className="muted">Loading defaults…</p>}
      <section className="card">
        <h2>Past experiments</h2>
        {list.data && !list.data.experiments.length && <p className="muted">None yet.</p>}
        {list.data && list.data.experiments.length > 0 && (
          <table className="table"><tbody>
            {[...list.data.experiments].reverse().map((e) => (
              <tr key={e.id}>
                <td><Link to={`/experiments/${e.id}`} className="mono">{e.id}</Link></td>
                <td>{e.name}</td><td className="muted">{(e.algorithms || []).join(" · ")}</td>
                <td className={e.status === "completed" ? "text-online" : e.status === "failed" ? "error" : "muted"}>{e.status}</td>
              </tr>
            ))}
          </tbody></table>
        )}
      </section>
    </div>
  );
}
