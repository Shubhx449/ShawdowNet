import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { useApi } from "../useBackend";
import ScenarioFields from "../ScenarioFields.jsx";

function Editor({ initial, id, onDone, onCancel }) {
  const [sc, setSc] = useState(initial);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  async function save(e) {
    e.preventDefault(); setBusy(true); setError(null);
    try {
      await api(id ? `/scenarios/${id}` : "/scenarios", { method: id ? "PUT" : "POST", body: sc });
      onDone();
    } catch (err) { setError(err.message); setBusy(false); }
  }
  return (
    <form className="card" onSubmit={save}>
      <h2>{id ? `Edit scenario ${id}` : "New scenario"}</h2>
      <ScenarioFields sc={sc} setSc={setSc} />
      {error && <p className="error">{error}</p>}
      <div className="actions">
        <button className="btn primary" disabled={busy || !sc.algorithms.length}>{busy ? "Saving…" : "Save"}</button>
        <button type="button" className="btn" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

export default function Scenarios() {
  const nav = useNavigate();
  const list = useApi("/scenarios");
  const defaults = useApi("/defaults");
  const [editing, setEditing] = useState(null); // {id|null, scenario}
  const [error, setError] = useState(null);

  async function edit(id) {
    try { setError(null); const r = await api(`/scenarios/${id}`); setEditing({ id, scenario: r.scenario }); }
    catch (e) { setError(e.message); }
  }
  async function del(s) {
    if (!window.confirm(`Delete scenario "${s.name}"? Finished experiments keep their own copy.`)) return;
    try { await api(`/scenarios/${s.id}`, { method: "DELETE" }); list.reload(); } catch (e) { setError(e.message); }
  }
  async function run(s) {
    try {
      setError(null);
      const r = await api("/experiments/run", { method: "POST", body: { scenario_id: s.id, save_raw_events: false, async: true } });
      nav(`/experiments/${r.experiment_id}`);
    } catch (e) { setError(e.message); }
  }

  return (
    <div className="page">
      <header className="page-head"><h1>Scenario Manager</h1>
        {!editing && defaults.data && <button className="btn primary" onClick={() => setEditing({ id: null, scenario: defaults.data })}>New scenario</button>}
      </header>
      {error && <p className="error">{error}</p>}
      {editing && <Editor key={editing.id || "new"} initial={editing.scenario} id={editing.id}
        onDone={() => { setEditing(null); list.reload(); }} onCancel={() => setEditing(null)} />}
      <section className="card">
        <h2>Saved scenarios</h2>
        {list.error && <p className="error">{list.error.message}</p>}
        {list.data && !list.data.scenarios.length && <p className="muted">None saved yet. "New scenario" starts from the paper defaults.</p>}
        {list.data && list.data.scenarios.length > 0 && (
          <table className="table results"><thead><tr><th>Name</th><th>Nodes</th><th>Runs</th><th>Algorithms</th><th></th></tr></thead>
            <tbody>{list.data.scenarios.map((s) => (
              <tr key={s.id}><td>{s.name} <span className="mono muted">{s.id}</span></td><td>{s.num_nodes}</td><td>{s.num_runs}</td>
                <td className="muted">{s.algorithms.join(" · ")}</td>
                <td className="actions tight"><button className="btn" onClick={() => run(s)}>Run</button>
                  <button className="btn" onClick={() => edit(s.id)}>Edit</button>
                  <button className="btn" onClick={() => del(s)}>Delete</button></td></tr>))}</tbody></table>
        )}
        <p className="muted small">Saving does not check the run-size limits; they apply when you Run (the server rejects an oversized job with a clear message). Run uses "raw events off".</p>
      </section>
    </div>
  );
}
