// A JavaScript mirror of the AEAR scoring in backend/simulation/routing/aear.py, for the Algorithm Explorer.
// It only re-implements the formula; the router remains the source of truth (a check compares the two).
export const DEFAULT_WEIGHTS = {
  NORMAL: { signal: 0.4, hop: 0.3, congestion: 0.3 },
  IMPORTANT: { signal: 0.35, hop: 0.35, congestion: 0.3 },
  EMERGENCY: { signal: 0.3, hop: 0.5, congestion: 0.2 },
};

const clip = (v) => Math.max(0, Math.min(1, v));
// hi === lo -> 1.0 (neutral), exactly as the router does
const minMax = (v, lo, hi) => (hi === lo ? 1 : clip((v - lo) / (hi - lo)));

export function aearScores(cands, w) {
  const rssi = cands.map((c) => c.rssi_dbm), hops = cands.map((c) => c.estimated_hops);
  const [rLo, rHi, hLo, hHi] = [Math.min(...rssi), Math.max(...rssi), Math.min(...hops), Math.max(...hops)];
  return cands.map((c) => {
    const S = minMax(c.rssi_dbm, rLo, rHi);
    const H = 1 - minMax(c.estimated_hops, hLo, hHi);
    const cap = c.queue_capacity > 0 ? c.queue_capacity : 1;
    const C = 1 - clip(c.queue_occupancy / cap);
    return { ...c, S, H, C, score: w.signal * S + w.hop * H + w.congestion * C };
  });
}
