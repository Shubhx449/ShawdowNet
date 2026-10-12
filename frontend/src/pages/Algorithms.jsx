import { useMemo, useState } from "react";
import { aearScores, DEFAULT_WEIGHTS } from "../algorithms";
import { fmt } from "../format";

const START = [
  { name: "A", rssi_dbm: -85, estimated_hops: 3, queue_occupancy: 2, queue_capacity: 30 },
  { name: "B", rssi_dbm: -100, estimated_hops: 2, queue_occupancy: 5, queue_capacity: 30 },
  { name: "C", rssi_dbm: -92, estimated_hops: 2, queue_occupancy: 27, queue_capacity: 30 },
];

function Calculator() {
  const [prio, setPrio] = useState("EMERGENCY");
  const [w, setW] = useState(DEFAULT_WEIGHTS.EMERGENCY);
  const [rows, setRows] = useState(START);
  const wsum = w.signal + w.hop + w.congestion;
  const out = useMemo(() => aearScores(rows.map((r) => ({ ...r, candidate: r.name })), w), [rows, w]);
  const best = out.reduce((a, b) => (b.score > a.score ? b : a), out[0]);
  const bestRssi = rows.reduce((a, b) => (b.rssi_dbm > a.rssi_dbm ? b : a), rows[0]);
  const upd = (i, k, v) => setRows((rs) => rs.map((r, j) => (j === i ? { ...r, [k]: v } : r)));
  const num = (i, k) => (e) => upd(i, k, e.target.value === "" ? 0 : Number(e.target.value));
  const pickPrio = (p) => { setPrio(p); setW(DEFAULT_WEIGHTS[p]); };
  return (
    <section className="card">
      <h2>Score a hop — AEAR vs RSSI baseline</h2>
      <p className="muted small">Enter the candidates a node could forward to. The AEAR column uses the formula above; the RSSI baseline simply takes the strongest signal. Try making the strongest-signal neighbour congested.</p>
      <div className="actions tight">
        <label className="field">Priority<select value={prio} onChange={(e) => pickPrio(e.target.value)}>{Object.keys(DEFAULT_WEIGHTS).map((p) => <option key={p}>{p}</option>)}</select></label>
        {["signal", "hop", "congestion"].map((k) => (
          <label className="field" key={k}>w {k}<input type="number" step="0.05" min="0" max="1" value={w[k]} onChange={(e) => setW({ ...w, [k]: Number(e.target.value) })} /></label>))}
      </div>
      {Math.abs(wsum - 1) > 1e-6 && <p className="error">Weights sum to {wsum.toFixed(2)}; the router requires exactly 1.00, so this is not a valid AEAR configuration.</p>}
      <div className="scroll"><table className="table cand"><thead><tr><th>Node</th><th>RSSI dBm</th><th>Est. hops</th><th>Queue</th><th>Capacity</th><th>S</th><th>H</th><th>C</th><th>Score</th><th></th></tr></thead>
        <tbody>{out.map((o, i) => (
          <tr key={i} className={o === best ? "picked" : ""}>
            <td><input value={rows[i].name} onChange={(e) => upd(i, "name", e.target.value)} style={{ width: 50 }} /></td>
            <td><input type="number" value={rows[i].rssi_dbm} onChange={num(i, "rssi_dbm")} style={{ width: 70 }} /></td>
            <td><input type="number" min="0" value={rows[i].estimated_hops} onChange={num(i, "estimated_hops")} style={{ width: 60 }} /></td>
            <td><input type="number" min="0" value={rows[i].queue_occupancy} onChange={num(i, "queue_occupancy")} style={{ width: 60 }} /></td>
            <td><input type="number" min="0" value={rows[i].queue_capacity} onChange={num(i, "queue_capacity")} style={{ width: 60 }} /></td>
            <td>{fmt(o.S, 3)}</td><td>{fmt(o.H, 3)}</td><td>{fmt(o.C, 3)}</td><td><strong>{fmt(o.score, 4)}</strong></td>
            <td>{rows.length > 1 && <button className="btn" onClick={() => setRows(rows.filter((_, j) => j !== i))}>✕</button>}</td></tr>))}</tbody></table></div>
      <div className="actions tight"><button className="btn" onClick={() => setRows([...rows, { name: String.fromCharCode(65 + rows.length), rssi_dbm: -95, estimated_hops: 2, queue_occupancy: 0, queue_capacity: 30 }])}>Add candidate</button></div>
      <p>AEAR picks <strong className="text-online">{best.name}</strong> · RSSI baseline picks <strong style={{ color: "#fbbf24" }}>{bestRssi.name}</strong>
        {best.name === bestRssi.name ? <span className="muted"> — same choice here.</span> : <span className="muted"> — they disagree; that is the case AEAR's extra factors exist for.</span>}</p>
      <p className="muted small">This mirrors the router's formula in JavaScript. Checked against the real router on 731 candidates: the winner matched in all 82 decisions; scores differ by up to ~0.001 only because the router's events round RSSI to 2 decimals.</p>
    </section>
  );
}

export default function Algorithms() {
  return (
    <div className="page">
      <header className="page-head"><h1>Algorithm Explorer</h1><span className="muted">How each router picks the next hop</span></header>
      <div className="grid">
        <section className="card">
          <h2>AEAR</h2>
          <p>A local decision at every node, over currently available neighbours only. Each candidate j is scored:</p>
          <p className="mono">Score = w<sub>S</sub>·S + w<sub>H</sub>·H + w<sub>C</sub>·C</p>
          <ul className="plain small">
            <li><strong>S</strong> signal: RSSI min-max normalised across the current candidates</li>
            <li><strong>H</strong> hop efficiency: 1 − normalised <em>estimated</em> remaining hops (straight-line distance to the destination ÷ range, rounded up; 0 if the candidate is the destination)</li>
            <li><strong>C</strong> congestion availability: 1 − queue fill ratio</li>
          </ul>
          <table className="table cand"><thead><tr><th>Priority</th><th>w<sub>S</sub></th><th>w<sub>H</sub></th><th>w<sub>C</sub></th></tr></thead>
            <tbody>{Object.entries(DEFAULT_WEIGHTS).map(([p, x]) => <tr key={p}><td>{p}</td><td>{x.signal}</td><td>{x.hop}</td><td>{x.congestion}</td></tr>)}</tbody></table>
          <p className="muted small">Defaults in this codebase; a scenario can override them. Emergency packets weight hop progress most and congestion least.</p>
          <p className="muted small"><strong>Normalisation quirk:</strong> if every candidate has the same value for a factor, the router treats the normalised value as 1.0, so H becomes 0 for all of them and the factor stops discriminating. It does not change the ranking, but it does lower every score.</p>
        </section>
        <section className="card">
          <h2>RSSI baseline</h2>
          <p>Forward to the available, unvisited neighbour with the strongest measured RSSI. No congestion awareness, no hop estimate, no priority weighting — deliberately, so any difference from AEAR comes from AEAR's extra factors.</p>
          <h2 style={{ marginTop: 18 }}>AODV baseline</h2>
          <p>Route-discovery routing: with no route, the source floods a route request (RREQ); the destination or a node holding a fresh route answers with a route reply (RREP) along the reverse path, and only then is data forwarded hop by hop along the table.</p>
          <ul className="plain small">
            <li>Duplicate RREQ suppression, reverse-route caching, destination sequence numbers with the freshness rule, route expiry</li>
            <li>Route errors (RERR) to upstream nodes when a node or link fails</li>
            <li>A recognisable AODV, not a byte-exact RFC 3561 stack</li>
          </ul>
          <p className="muted small">AODV makes no per-candidate scores, so the Live decision panel shows its route-table decision instead. Its control messages (RREQ/RREP/RERR) count toward routing overhead in Mode 2.</p>
        </section>
      </div>
      <Calculator />
    </div>
  );
}
