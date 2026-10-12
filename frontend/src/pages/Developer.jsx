import { useEffect, useState } from "react";
import { useApi } from "../useBackend";
import { api } from "../api";

const QUICK = ["/health", "/defaults", "/adapters", "/scenarios", "/experiments"];

function Console() {
  const [method, setMethod] = useState("GET");
  const [path, setPath] = useState("/health");
  const [body, setBody] = useState("");
  const [res, setRes] = useState(null);
  async function send(m = method, p = path) {
    const t0 = performance.now();
    try {
      const r = await fetch(`/api${p}`, { method: m, headers: { "Content-Type": "application/json" }, body: m === "GET" || !body.trim() ? undefined : body });
      const text = await r.text();
      let pretty = text; try { pretty = JSON.stringify(JSON.parse(text), null, 2); } catch { /* not JSON */ }
      setRes({ status: r.status, ms: Math.round(performance.now() - t0), text: pretty });
    } catch { setRes({ status: 0, ms: 0, text: "Cannot reach the backend." }); }
  }
  return (
    <section className="card">
      <h2>API console</h2>
      <div className="actions tight">{QUICK.map((q) => <button key={q} className="btn" onClick={() => { setMethod("GET"); setPath(q); send("GET", q); }}>{q}</button>)}</div>
      <div className="actions tight">
        <select value={method} onChange={(e) => setMethod(e.target.value)}>{["GET", "POST", "PUT", "DELETE"].map((m) => <option key={m}>{m}</option>)}</select>
        <input value={path} onChange={(e) => setPath(e.target.value)} style={{ flex: 1, minWidth: 200 }} />
        <button className="btn primary" onClick={() => send()}>Send</button>
      </div>
      {method !== "GET" && <textarea className="code" rows={5} placeholder="JSON body" value={body} onChange={(e) => setBody(e.target.value)} />}
      {res && <><p className={res.status >= 400 || !res.status ? "error" : "text-online"}>HTTP {res.status || "—"} <span className="muted">· {res.ms} ms</span></p><pre className="code out">{res.text}</pre></>}
      <p className="muted small">This sends real requests: POST/PUT/DELETE change stored data. Paths are relative to /api.</p>
    </section>
  );
}

function Validation() {
  const list = useApi("/experiments");
  const [id, setId] = useState("");
  const [runs, setRuns] = useState(null);
  const [err, setErr] = useState(null);
  const done = (list.data?.experiments || []).filter((e) => e.status === "completed");
  useEffect(() => {
    if (!id) return setRuns(null);
    setErr(null); setRuns(null);
    api(`/experiments/${id}/results`).then((r) => setRuns(Object.values(r.results).flat())).catch((e) => setErr(e.message));
  }, [id]);
  const bad = runs ? runs.filter((r) => !r.validation?.is_valid) : [];
  return (
    <section className="card">
      <h2>Validation reports</h2>
      <select value={id} onChange={(e) => setId(e.target.value)}><option value="">Choose a completed experiment…</option>
        {done.map((e) => <option key={e.id} value={e.id}>{e.id} — {e.name}</option>)}</select>
      {err && <p className="error">{err}</p>}
      {runs && <>
        <p className={bad.length ? "error" : "text-online"}>{bad.length ? `${bad.length} of ${runs.length} runs have validation issues` : `All ${runs.length} runs passed validation`}</p>
        <div className="scroll"><table className="table results"><thead><tr><th>Algorithm</th><th>Run</th><th>Seed</th><th>Events</th><th>Valid</th><th>Summary</th></tr></thead>
          <tbody>{runs.map((r) => (
            <tr key={r.algorithm + r.run_number}><td>{r.algorithm}</td><td>{r.run_number}</td><td className="mono">{r.seed}</td><td className="mono">{r.event_count}</td>
              <td className={r.validation?.is_valid ? "text-online" : "error"}>{r.validation?.is_valid ? "yes" : "NO"}</td>
              <td className="muted small">{r.validation?.summary}{!r.validation?.is_valid && <pre className="code out">{JSON.stringify(r.validation.issues, null, 2)}</pre>}</td></tr>))}</tbody></table></div></>}
      <p className="muted small">Raw per-event data is only stored when "Save raw events" was ticked for the run, and is not served by the API yet — it is on disk under the experiment's folder.</p>
    </section>
  );
}

export default function Developer() {
  return (
    <div className="page">
      <header className="page-head"><h1>Developer / Debug</h1></header>
      <Console />
      <Validation />
    </div>
  );
}
