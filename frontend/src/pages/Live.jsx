import { useEffect, useMemo, useState } from "react";
import { useApi } from "../useBackend";
import { useLiveSession } from "../live/useLiveSession";
import { buildDecisions, EVENT_COLORS, eventSummary } from "../live/decisions";
import NetworkCanvas from "../live/NetworkCanvas.jsx";
import { fmt } from "../format";

const ALGS = ["AEAR", "RSSI", "AODV"];
const SPEEDS = [0.25, 1, 5, 20, 100];
const HIDDEN_BY_DEFAULT = new Set(["CANDIDATE_EVALUATED", "NEIGHBOR_DISCOVERED"]);

function SessionPanel({ live, adapters }) {
  const [alg, setAlg] = useState("AEAR");
  const [adapter, setAdapter] = useState("simulator");
  const [sc, setSc] = useState({ num_nodes: 15, base_seed: 7, communication_range_m: 430 });
  const num = (k) => (e) => setSc((s) => ({ ...s, [k]: e.target.value === "" ? "" : Number(e.target.value) }));
  const create = () => live.send({ type: "session.create", algorithm: alg, adapter, scenario: sc });
  return (
    <section className="card">
      <h2>Session</h2>
      <div className="form-grid tight">
        <label className="field">Algorithm<select value={alg} onChange={(e) => setAlg(e.target.value)}>{ALGS.map((a) => <option key={a}>{a}</option>)}</select></label>
        <label className="field">Adapter<select value={adapter} onChange={(e) => setAdapter(e.target.value)}>
          {(adapters?.adapters || [{ kind: "simulator", available: true }]).map((a) => <option key={a.kind} value={a.kind} disabled={!a.available}>{a.kind}{a.available ? "" : " (stub)"}</option>)}</select></label>
        <label className="field">Nodes<input type="number" min="2" max="100" value={sc.num_nodes} onChange={num("num_nodes")} /></label>
        <label className="field">Seed<input type="number" value={sc.base_seed} onChange={num("base_seed")} /></label>
        <label className="field">Range (m)<input type="number" step="10" value={sc.communication_range_m} onChange={num("communication_range_m")} /></label>
      </div>
      <div className="actions">
        <button className="btn primary" onClick={create} disabled={live.conn !== "open"}>{live.session ? "Create new session" : "Create session"}</button>
        {live.session && <button className="btn" onClick={() => live.send({ type: "sim.reset" })}>Reset</button>}
      </div>
    </section>
  );
}

function Controls({ live, sel, setSource, setDest, source, dest }) {
  const caps = live.session.adapter;
  const ids = live.session.network.nodes.map((n) => n.node_id);
  const [ptype, setPtype] = useState("EMERGENCY");
  const [ttl, setTtl] = useState(12);
  const [speed, setSpeed] = useState(1);
  const [linkA, setLinkA] = useState("");
  const [linkB, setLinkB] = useState("");
  const st = live.state;
  return (
    <>
      <section className="card">
        <h2>Send packet</h2>
        <div className="form-grid tight">
          <label className="field">From<select value={source} onChange={(e) => setSource(e.target.value)}>{ids.map((i) => <option key={i}>{i}</option>)}</select></label>
          <label className="field">To<select value={dest} onChange={(e) => setDest(e.target.value)}>{ids.map((i) => <option key={i}>{i}</option>)}</select></label>
          <label className="field">Priority<select value={ptype} onChange={(e) => setPtype(e.target.value)}>{["NORMAL", "IMPORTANT", "EMERGENCY"].map((t) => <option key={t}>{t}</option>)}</select></label>
          <label className="field">TTL<input type="number" min="1" max="64" value={ttl} onChange={(e) => setTtl(Number(e.target.value))} /></label>
        </div>
        <button className="btn primary" onClick={() => live.send({ type: "packet.send", source, destination: dest, packet_type: ptype, ttl })}>Send</button>
        <span className="muted small"> Click a node on the map, then use it as From/To below.</span>
      </section>
      <section className="card">
        <h2>Time {st && <span className="muted mono">t={fmt(st.playhead, 2)}s · {st.pending_events} pending</span>}</h2>
        {!caps.simulated_time ? <p className="muted">This adapter has no simulated clock.</p> : (
          <div className="actions">
            <button className="btn" onClick={() => live.send({ type: "sim.step", count: 1 })}>Step</button>
            <button className="btn" onClick={() => live.send({ type: "sim.step", count: 10 })}>Step ×10</button>
            {live.playing
              ? <button className="btn primary" onClick={() => live.send({ type: "sim.pause" })}>Pause</button>
              : <button className="btn primary" onClick={() => live.send({ type: "sim.play", speed })}>Play</button>}
            <select value={speed} onChange={(e) => { const v = Number(e.target.value); setSpeed(v); if (live.playing) live.send({ type: "sim.play", speed: v }); }}>
              {SPEEDS.map((s) => <option key={s} value={s}>{s}× </option>)}</select>
          </div>
        )}
      </section>
      <section className="card">
        <h2>Failures</h2>
        {!caps.failure_injection ? <p className="muted">This adapter cannot inject failures.</p> : (
          <>
            <p className="small">Node <strong className="mono">{sel || "—"}</strong> {sel && st?.nodes?.[sel]?.failed && <span className="error">(failed)</span>}</p>
            <div className="actions tight">
              <button className="btn" disabled={!sel} onClick={() => live.send({ type: "failure.node_fail", node: sel })}>Fail node</button>
              <button className="btn" disabled={!sel} onClick={() => live.send({ type: "failure.node_restore", node: sel })}>Restore node</button>
            </div>
            <p className="small">Link</p>
            <div className="actions tight">
              <select value={linkA} onChange={(e) => setLinkA(e.target.value)}><option value="">A</option>{ids.map((i) => <option key={i}>{i}</option>)}</select>
              <select value={linkB} onChange={(e) => setLinkB(e.target.value)}><option value="">B</option>{ids.map((i) => <option key={i}>{i}</option>)}</select>
              <button className="btn" disabled={!linkA || !linkB} onClick={() => live.send({ type: "failure.link_fail", node_a: linkA, node_b: linkB })}>Fail</button>
              <button className="btn" disabled={!linkA || !linkB} onClick={() => live.send({ type: "failure.link_restore", node_a: linkA, node_b: linkB })}>Restore</button>
            </div>
            {st?.failed_links?.length > 0 && <p className="muted small">Failed links: {st.failed_links.map((l) => l.join("–")).join(", ")}</p>}
          </>
        )}
      </section>
    </>
  );
}

function DecisionPanel({ decisions, index, setIndex }) {
  if (!decisions.length) return <section className="card"><h2>Routing decision</h2><p className="muted">No decision yet. Send a packet and step or play.</p></section>;
  const d = decisions[Math.min(index, decisions.length - 1)];
  const i = Math.min(index, decisions.length - 1);
  const sel = d.decision.selected;
  const rows = [...d.candidates].sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
  const aear = rows.some((r) => r.weights);
  return (
    <section className="card">
      <h2>Routing decision — {d.packet_id} at {d.node_id}</h2>
      <div className="actions tight">
        <button className="btn" disabled={i === 0} onClick={() => setIndex(i - 1)}>◀</button>
        <span className="mono small">hop decision {i + 1} / {decisions.length} · t={fmt(d.timestamp, 3)}s · {d.algorithm}</span>
        <button className="btn" disabled={i >= decisions.length - 1} onClick={() => setIndex(i + 1)}>▶</button>
      </div>
      <p>Selected <strong className="text-online mono">{sel}</strong> {d.decision.score !== undefined && <span className="muted">(score {fmt(d.decision.score, 4)})</span>}
        {d.neighbors.length > 0 && <span className="muted"> · {d.neighbors.length} neighbours, {d.candidates.length} candidates</span>}</p>
      {!d.candidates.length ? (
        <p className="muted small">This algorithm reports no per-candidate scores. Decision details: <code>{JSON.stringify(d.decision)}</code></p>
      ) : (
        <div className="scroll"><table className="table cand"><thead><tr>
          <th>Cand.</th><th>Score</th><th>RSSI</th>{aear && <><th>Hops</th><th>Queue</th><th>Signal</th><th>Hop</th><th>Cong.</th></>}</tr></thead>
          <tbody>{rows.map((c) => (
            <tr key={c.candidate} className={c.candidate === sel ? "picked" : ""}>
              <td className="mono">{c.candidate}</td><td>{fmt(c.score, 4)}</td><td>{fmt(c.rssi_dbm, 1)}</td>
              {aear && <><td>{c.estimated_hops ?? "—"}</td><td>{c.queue_occupancy}/{c.queue_capacity}</td>
                <td>{fmt(c.normalized_rssi, 2)}</td><td>{fmt(c.hop_efficiency, 2)}</td><td>{fmt(c.congestion_availability, 2)}</td></>}
            </tr>))}</tbody></table></div>
      )}
      {aear && <p className="muted small">Factors are the normalised inputs the router reported (0–1). Weights for this priority: {Object.entries(rows[0].weights).map(([k, v]) => `${k} ${v}`).join(" · ")}. Score is as reported by the router.</p>}
    </section>
  );
}

function EventLog({ events, onPick }) {
  const [showAll, setShowAll] = useState(false);
  const [pause, setPause] = useState(false);
  const [frozen, setFrozen] = useState(null);
  const src = pause ? frozen : events;
  const rows = useMemo(() => (showAll ? src : src.filter((e) => !HIDDEN_BY_DEFAULT.has(e.event_type))).slice(-200).reverse(), [src, showAll]);
  return (
    <section className="card">
      <h2>Event log <span className="muted mono">{events.length}</span></h2>
      <div className="checks small">
        <label><input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> show candidate/neighbour events</label>
        <label><input type="checkbox" checked={pause} onChange={(e) => { setPause(e.target.checked); setFrozen(events); }} /> freeze</label>
      </div>
      <div className="log">
        {rows.length === 0 && <p className="muted">No events yet.</p>}
        {rows.map((e) => (
          <div key={e.event_id} className="log-row" onClick={() => e.packet_id && onPick(e.packet_id)}>
            <span className="mono muted">{fmt(e.timestamp, 3)}</span>
            <span className="mono" style={{ color: EVENT_COLORS[e.event_type] || "#7d8fa1" }}>{e.event_type}</span>
            <span className="mono">{e.node_id}{e.packet_id ? ` · ${e.packet_id}` : ""}</span>
            <span className="muted">{eventSummary(e)}</span>
          </div>))}
      </div>
    </section>
  );
}

export default function Live() {
  const live = useLiveSession();
  const adapters = useApi("/adapters");
  const [sel, setSel] = useState(null);
  const [source, setSource] = useState("N1");
  const [dest, setDest] = useState("N2");
  const [pid, setPid] = useState(null);
  const [dIndex, setDIndex] = useState(0);
  const decisionsAll = useMemo(() => buildDecisions(live.events), [live.events]);
  const decisions = useMemo(() => (pid ? decisionsAll.filter((d) => d.packet_id === pid) : []), [decisionsAll, pid]);
  const packets = live.state?.packets || {};
  useEffect(() => { setSel(null); setPid(null); }, [live.session]);
  // follow the newest packet (and its newest decision) unless the user picked another
  useEffect(() => {
    const ids = Object.keys(packets);
    if (!pid && ids.length) setPid(ids[ids.length - 1]);
  }, [packets, pid]);
  useEffect(() => { setDIndex(Math.max(0, decisions.length - 1)); }, [pid, decisions.length]);

  const decision = decisions[Math.min(dIndex, decisions.length - 1)];
  const packet = pid ? packets[pid] : null;
  const ids = live.session?.network.nodes.map((n) => n.node_id) || [];
  useEffect(() => { if (ids.length && !ids.includes(source)) setSource(ids[0]); if (ids.length && !ids.includes(dest)) setDest(ids[Math.min(1, ids.length - 1)]); }, [ids.join(",")]); // eslint-disable-line

  return (
    <div className="page wide">
      <header className="page-head">
        <h1>Live Network</h1><span className="pill">Mode 1</span>
        <span className={`mono small text-${live.conn === "open" ? "online" : live.conn === "closed" ? "offline" : "checking"}`}>socket {live.conn}</span>
        {live.conn === "closed" && <button className="btn" onClick={live.reconnect}>Reconnect</button>}
        {live.session && <span className="muted small">adapter: {live.session.adapter.kind}</span>}
      </header>
      {live.error && <div className="banner warn" onClick={live.clearError}><strong>Server:</strong> {live.error} <span className="muted small">(click to dismiss)</span></div>}
      <div className="live">
        <div className="col">
          <SessionPanel live={live} adapters={adapters.data} />
          {live.session && <Controls live={live} sel={sel} source={source} dest={dest} setSource={setSource} setDest={setDest} />}
        </div>
        <div className="col mid">
          {live.session ? (
            <section className="card">
              <NetworkCanvas network={live.session.network} state={live.state} selected={sel} onSelect={setSel}
                packet={packet} decision={decision} source={packet?.source} destination={packet?.destination} />
              {sel && <div className="actions tight small"><span className="mono">Node {sel}</span>
                <button className="btn" onClick={() => setSource(sel)}>Use as From</button>
                <button className="btn" onClick={() => setDest(sel)}>Use as To</button></div>}
              <p className="muted small">Blue path: route so far · purple: selected next hop · dashed grey: other candidates (opacity = score) · red dashed: failed link · green ring: packet is here.</p>
            </section>
          ) : <section className="card placeholder"><p>Create a session to see the network.</p></section>}
          {live.session && (
            <section className="card">
              <h2>Packets</h2>
              {Object.keys(packets).length === 0 ? <p className="muted">None sent yet.</p> : (
                <table className="table"><tbody>{Object.entries(packets).map(([id, p]) => (
                  <tr key={id} className={id === pid ? "picked" : ""} onClick={() => setPid(id)} style={{ cursor: "pointer" }}>
                    <td className="mono">{id}</td><td>{p.type}</td><td className="mono">{p.source}→{p.destination}</td>
                    <td className="mono muted">{p.path.join("›")}</td>
                    <td className={p.status === "DELIVERED" ? "text-online" : p.status.startsWith("DROPPED") ? "error" : "muted"}>{p.status}</td></tr>))}</tbody></table>
              )}
            </section>
          )}
        </div>
        <div className="col">
          {live.session && <DecisionPanel decisions={decisions} index={dIndex} setIndex={setDIndex} />}
          {live.session && <EventLog events={live.events} onPick={setPid} />}
        </div>
      </div>
    </div>
  );
}
