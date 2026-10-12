const NODE_FILL = { NORMAL: "#1e3a4f", CONGESTED: "#5a4515", FAILED: "#4a1d1d" };
const NODE_STROKE = { NORMAL: "#38bdf8", CONGESTED: "#fbbf24", FAILED: "#f87171" };

// SVG map of the network. Everything drawn comes from the server: positions/links from session.created,
// node status / failed links / packet paths from the latest snapshot, arrows from the selected decision.
export default function NetworkCanvas({ network, state, selected, onSelect, packet, decision, source, destination }) {
  const { width_m: W, height_m: H, nodes, links } = network;
  const pos = Object.fromEntries(nodes.map((n) => [n.node_id, n]));
  const failedLinks = new Set((state?.failed_links || []).map(([a, b]) => [a, b].sort().join("|")));
  const path = packet?.path || [];
  const chosen = decision?.decision?.selected;
  const maxScore = Math.max(...(decision?.candidates || []).map((c) => c.score ?? 0), 0.0001);

  return (
    <svg viewBox={`-30 -30 ${W + 60} ${H + 60}`} className="canvas" role="img" aria-label="Network topology">
      <rect x="0" y="0" width={W} height={H} fill="none" stroke="#223040" strokeDasharray="6 6" />
      {links.map((l) => {
        const a = pos[l.node_a], b = pos[l.node_b];
        const dead = failedLinks.has([l.node_a, l.node_b].sort().join("|"));
        return <line key={l.node_a + l.node_b} x1={a.x} y1={a.y} x2={b.x} y2={b.y}
          stroke={dead ? "#f87171" : "#223040"} strokeWidth={dead ? 3 : 1.5} strokeDasharray={dead ? "8 6" : undefined} />;
      })}
      {path.slice(1).map((id, i) => (
        <line key={"p" + i} x1={pos[path[i]].x} y1={pos[path[i]].y} x2={pos[id].x} y2={pos[id].y} stroke="#38bdf8" strokeWidth="5" opacity="0.8" />
      ))}
      {decision && decision.candidates.map((c) => {
        const from = pos[decision.node_id], to = pos[c.candidate];
        if (!to) return null;
        const pick = c.candidate === chosen;
        return <line key={"c" + c.candidate} x1={from.x} y1={from.y} x2={to.x} y2={to.y}
          stroke={pick ? "#a78bfa" : "#7d8fa1"} strokeWidth={pick ? 6 : 3} strokeDasharray={pick ? undefined : "3 5"}
          opacity={pick ? 1 : 0.25 + 0.6 * Math.max(0, (c.score ?? 0)) / maxScore} />;
      })}
      {decision && !decision.candidates.length && chosen && pos[chosen] && (
        <line x1={pos[decision.node_id].x} y1={pos[decision.node_id].y} x2={pos[chosen].x} y2={pos[chosen].y} stroke="#a78bfa" strokeWidth="6" />
      )}
      {nodes.map((n) => {
        const s = state?.nodes?.[n.node_id];
        const status = s?.status || n.status;
        const isSel = selected === n.node_id;
        const role = n.node_id === source ? "S" : n.node_id === destination ? "D" : null;
        const here = packet && packet.current_node === n.node_id && ["IN_TRANSIT", "BUFFERED", "GENERATED"].includes(packet.status);
        return (
          <g key={n.node_id} onClick={() => onSelect(n.node_id)} style={{ cursor: "pointer" }}>
            {here && <circle cx={n.x} cy={n.y} r="34" fill="none" stroke="#34d399" strokeWidth="4" />}
            <circle cx={n.x} cy={n.y} r="24" fill={NODE_FILL[status] || NODE_FILL.NORMAL}
              stroke={isSel ? "#fff" : NODE_STROKE[status] || NODE_STROKE.NORMAL} strokeWidth={isSel ? 5 : 3} />
            <text x={n.x} y={n.y + 5} textAnchor="middle" fontSize="18" fill="#d6e0ea" fontFamily="monospace">{n.node_id.replace("N", "")}</text>
            {role && <text x={n.x + 26} y={n.y - 20} fontSize="20" fontWeight="700" fill="#34d399">{role}</text>}
            {s && s.queue > 0 && <text x={n.x} y={n.y + 44} textAnchor="middle" fontSize="15" fill="#fbbf24" fontFamily="monospace">q{s.queue}</text>}
          </g>
        );
      })}
    </svg>
  );
}
