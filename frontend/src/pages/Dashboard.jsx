import { Link } from "react-router-dom";
import { useApi, useBackendStatus } from "../useBackend";

function Card({ title, children }) {
  return (
    <section className="card">
      <h2>{title}</h2>
      {children}
    </section>
  );
}

function Async({ state, children }) {
  if (state.loading && !state.data) return <p className="muted">Loading…</p>;
  if (state.error) return <p className="error">{state.error.message}</p>;
  return children(state.data);
}

export default function Dashboard() {
  const backend = useBackendStatus();
  const adapters = useApi("/adapters");
  const experiments = useApi("/experiments");

  return (
    <div className="page">
      <header className="page-head">
        <h1>Dashboard</h1>
        <span className="muted">Adaptive Emergency-Aware Routing for LoRa mesh networks</span>
      </header>

      <div className="grid">
        <Card title="Modes">
          <p><Link to="/experiments">Mode 2 — Experiments</Link><br />
            <span className="muted">Batch runs, AEAR vs RSSI vs AODV, aggregated statistics.</span></p>
          <p><Link to="/live">Mode 1 — Live network</Link><br />
            <span className="muted">Step through packet forwarding and inject failures.</span></p>
        </Card>

        <Card title="Backend">
          <p>Status: <strong className={`text-${backend}`}>{backend}</strong></p>
          <Async state={adapters}>
            {(d) => (
              <ul className="plain">
                {d.adapters.map((a) => (
                  <li key={a.kind}>
                    <code>{a.kind}</code>{" "}
                    <span className={a.available ? "text-online" : "muted"}>
                      {a.available ? "available" : "not available (stub)"}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Async>
        </Card>

        <Card title="Experiments">
          <Async state={experiments}>
            {(d) => {
              const list = d.experiments;
              if (!list.length) return <p className="muted">None yet. Runs will appear here.</p>;
              return (
                <ul className="plain">
                  {list.slice(-5).reverse().map((e) => (
                    <li key={e.id}>
                      <Link to={`/experiments/${e.id}`}><code>{e.id}</code></Link>{" "}
                      <span className="muted">{e.status}</span>
                    </li>
                  ))}
                </ul>
              );
            }}
          </Async>
        </Card>
      </div>

    </div>
  );
}
