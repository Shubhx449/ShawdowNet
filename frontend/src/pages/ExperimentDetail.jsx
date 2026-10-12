import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Cell, ErrorBar, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api";
import { download, fmt, toCsv } from "../format";

const COLORS = { AEAR: "#38bdf8", RSSI: "#fbbf24", AODV: "#a78bfa" };
// key, label, which direction is better (used only to bold the best mean in the table)
const HEADLINE = [
  ["pdr_percent", "PDR (%)", "high"], ["emergency_pdr_percent", "Emergency PDR (%)", "high"],
  ["avg_end_to_end_delay_s", "Avg delay (s)", "low"], ["avg_emergency_delay_s", "Emergency delay (s)", "low"],
  ["avg_hop_count", "Avg hops", "low"], ["throughput_packets_per_s", "Throughput (pkt/s)", "high"],
  ["packet_loss_rate_percent", "Loss rate (%)", "low"], ["routing_overhead_control_messages", "Control messages", "low"],
  ["duplicate_packet_rate_percent", "Duplicate rate (%)", "low"],
];
const CHARTS = HEADLINE.slice(0, 6);

function Progress({ status }) {
  return (
    <section className="card">
      <h2>Progress — <span className={status.status === "failed" ? "error" : "text-checking"}>{status.status}</span></h2>
      {Object.entries(status.progress || {}).map(([alg, p]) => (
        <div key={alg} className="prog-row">
          <span className="mono">{alg}</span>
          <div className="bar"><div style={{ width: `${(100 * p.completed) / p.total}%`, background: COLORS[alg] }} /></div>
          <span className="mono muted">{p.completed}/{p.total}</span>
        </div>
      ))}
      {status.status === "saving" && <p className="muted">Writing results to disk… this can take several seconds.</p>}
      {status.error && <p className="error">{status.error}</p>}
    </section>
  );
}

function MetricChart({ label, points }) {
  const has = points && points.some((p) => p.mean !== null);
  return (
    <div className="card chart">
      <h2>{label}</h2>
      {!has ? <p className="muted">Not collected for this scenario.</p> : (
        <ResponsiveContainer width="100%" height={200}>
          <BarChart data={points} margin={{ top: 8, right: 8, left: -8, bottom: 0 }}>
            <CartesianGrid stroke="#223040" vertical={false} />
            <XAxis dataKey="algorithm" stroke="#7d8fa1" /><YAxis stroke="#7d8fa1" />
            <Tooltip contentStyle={{ background: "#111820", border: "1px solid #223040" }} formatter={(v) => fmt(v, 4)} />
            <Bar dataKey="mean" name="mean">
              {points.map((p) => <Cell key={p.algorithm} fill={COLORS[p.algorithm] || "#7d8fa1"} />)}
              <ErrorBar dataKey="std" stroke="#d6e0ea" width={5} />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}
      <p className="muted small">Bars: mean across runs. Whiskers: ±1 std.</p>
    </div>
  );
}

function Results({ id }) {
  const [d, setD] = useState(null);
  const [err, setErr] = useState(null);
  const [all, setAll] = useState(false);
  useEffect(() => {
    Promise.all([api(`/experiments/${id}/metrics`), api(`/experiments/${id}/graphs`), api(`/experiments/${id}/results`), api(`/experiments/${id}`)])
      .then(([metrics, graphs, results, meta]) => setD({ metrics, graphs, results: results.results, meta }))
      .catch(setErr);
  }, [id]);
  if (err) return <p className="error">{err.message}</p>;
  if (!d) return <p className="muted">Loading results…</p>;

  const algs = Object.keys(d.metrics);
  const runs = Object.values(d.results).flat();
  const invalid = runs.filter((r) => r.validation && !r.validation.is_valid);
  const keys = all ? Object.keys(d.metrics[algs[0]]) : HEADLINE.map((h) => h[0]);
  const meta = Object.fromEntries(HEADLINE.map(([k, l, b]) => [k, { l, b }]));

  const aggCsv = () => toCsv([["algorithm", "metric", "mean", "std", "ci95_low", "ci95_high", "n"],
    ...algs.flatMap((a) => Object.entries(d.metrics[a]).map(([m, s]) => [a, m, s.mean, s.std, s.ci_95?.[0], s.ci_95?.[1], s.n]))]);
  const scalar = (m) => Object.keys(m).filter((k) => typeof m[k] !== "object" || m[k] === null);
  const runCols = scalar(runs[0].metrics);
  const runCsv = () => toCsv([["algorithm", "run", "seed", "valid", ...runCols],
    ...runs.map((r) => [r.algorithm, r.run_number, r.seed, r.validation?.is_valid, ...runCols.map((c) => r.metrics[c])])]);

  return (
    <>
      {invalid.length > 0 && (
        <div className="banner warn">
          <strong>{invalid.length} of {runs.length} runs failed validation.</strong> Treat their numbers with caution:
          <ul className="plain">{invalid.slice(0, 5).map((r) => <li key={r.algorithm + r.run_number} className="mono">
            {r.algorithm} run {r.run_number}: {r.validation.summary}</li>)}</ul>
        </div>
      )}
      <section className="card">
        <h2>Summary — mean ± std over {d.metrics[algs[0]].pdr_percent?.n} runs</h2>
        <div className="scroll"><table className="table results">
          <thead><tr><th>Metric</th>{algs.map((a) => <th key={a} style={{ color: COLORS[a] }}>{a}</th>)}</tr></thead>
          <tbody>{keys.map((k) => {
            const means = algs.map((a) => d.metrics[a][k]?.mean).filter((v) => v !== null && v !== undefined);
            const best = meta[k]?.b === "high" ? Math.max(...means) : meta[k]?.b === "low" ? Math.min(...means) : null;
            return (
              <tr key={k}><td>{meta[k]?.l || k}</td>{algs.map((a) => {
                const s = d.metrics[a][k];
                return <td key={a} className={s?.mean === best && means.length > 1 ? "best" : ""}>
                  {s?.mean === null || !s ? "—" : <>{fmt(s.mean)} <span className="muted">± {fmt(s.std)}</span></>}</td>;
              })}</tr>);
          })}</tbody>
        </table></div>
        <p className="muted small">Bold = best mean where a direction is defined. It is not a significance test — with few runs, check the ± before reading a difference as real.</p>
        <button className="btn" onClick={() => setAll(!all)}>{all ? "Show headline metrics" : "Show all metrics"}</button>
      </section>
      <div className="grid">
        {CHARTS.map(([k, l]) => <MetricChart key={k} label={l} points={d.graphs.metrics[k]} />)}
      </div>
      <section className="card">
        <h2>Export</h2>
        <div className="actions">
          <button className="btn" onClick={() => download(`${id}_aggregated.csv`, aggCsv(), "text/csv")}>Aggregated CSV</button>
          <button className="btn" onClick={() => download(`${id}_per_run.csv`, runCsv(), "text/csv")}>Per-run CSV</button>
          <button className="btn" onClick={() => download(`${id}.json`, JSON.stringify({ metadata: d.meta, metrics: d.metrics, runs }, null, 2), "application/json")}>Full JSON</button>
        </div>
      </section>
    </>
  );
}

export default function ExperimentDetail() {
  const { id } = useParams();
  const [status, setStatus] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    let alive = true, timer;
    const tick = async () => {
      try {
        const s = await api(`/experiments/${id}/status`);
        if (!alive) return;
        setStatus(s); setErr(null);
        if (s.status === "completed" || s.status === "failed") return;
      } catch (e) { if (!alive) return; setErr(e); if (e.status === 404) return; }
      timer = setTimeout(tick, 1000);
    };
    tick();
    return () => { alive = false; clearTimeout(timer); };
  }, [id]);

  return (
    <div className="page">
      <header className="page-head">
        <h1>Experiment <span className="mono">{id}</span></h1>
        {status && <span className="muted">{status.scenario_name}</span>}
        <Link to="/experiments" className="muted">← all experiments</Link>
      </header>
      {err && <p className="error">{err.message}</p>}
      {status && status.status !== "completed" && <Progress status={status} />}
      {status && status.status === "completed" && <Results id={id} />}
    </div>
  );
}
