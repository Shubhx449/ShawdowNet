// Turns the raw event stream into one "decision" per routing decision (pure, no React).
// A decision = the neighbours found + every candidate scored + what was selected, at one node for one packet.
// Works for all three algorithms: AEAR/RSSI emit CANDIDATE_EVALUATED, AODV emits only the decision.
export function buildDecisions(events) {
  const pending = new Map();
  const out = [];
  for (const e of events) {
    if (!e.packet_id) continue;
    const key = `${e.packet_id}|${e.node_id}`;
    if (e.event_type === "NEIGHBOR_DISCOVERED") {
      pending.set(key, { neighbors: e.metadata.neighbors || [], candidates: [] });
    } else if (e.event_type === "CANDIDATE_EVALUATED") {
      if (!pending.has(key)) pending.set(key, { neighbors: [], candidates: [] });
      pending.get(key).candidates.push(e.metadata);
    } else if (e.event_type === "FORWARDING_DECISION") {
      const p = pending.get(key) || { neighbors: [], candidates: [] };
      pending.delete(key);
      out.push({ id: e.event_id, packet_id: e.packet_id, node_id: e.node_id, timestamp: e.timestamp,
        algorithm: e.algorithm, neighbors: p.neighbors, candidates: p.candidates, decision: e.metadata });
    }
  }
  return out;
}

export const EVENT_COLORS = {
  PACKET_GENERATED: "#38bdf8", PACKET_FORWARDED: "#38bdf8", PACKET_RECEIVED: "#7d8fa1", PACKET_DELIVERED: "#34d399",
  PACKET_BUFFERED: "#fbbf24", PACKET_DROPPED: "#f87171", NODE_FAILED: "#f87171", LINK_FAILED: "#f87171",
  NODE_RECOVERED: "#34d399", LINK_RECOVERED: "#34d399", FORWARDING_DECISION: "#a78bfa",
};

export function eventSummary(e) {
  const m = e.metadata || {};
  switch (e.event_type) {
    case "PACKET_GENERATED": return `${m.packet_type} → ${m.destination}`;
    case "PACKET_FORWARDED": return `→ ${m.to_node} (hop ${m.hop_count}, ttl ${m.ttl_remaining})`;
    case "FORWARDING_DECISION": return `selected ${m.selected}`;
    case "CANDIDATE_EVALUATED": return `${m.candidate} score ${m.score}`;
    case "NEIGHBOR_DISCOVERED": return `${(m.neighbors || []).length} neighbours`;
    case "PACKET_BUFFERED": return m.reason || "";
    default: return Object.keys(m).length ? JSON.stringify(m).slice(0, 90) : "";
  }
}
