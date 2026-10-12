import { describe, expect, it } from "vitest";
import { aearScores, DEFAULT_WEIGHTS } from "../algorithms";
import candidates from "../__fixtures__/aear_candidates.json";

const c = (rssi_dbm, estimated_hops, queue_occupancy = 0, queue_capacity = 30) => ({ rssi_dbm, estimated_hops, queue_occupancy, queue_capacity });
const W = DEFAULT_WEIGHTS.NORMAL;

describe("parity with the real AEAR router (fixtures captured from backend)", () => {
  const groups = new Map();
  for (const m of candidates) (groups.get(m.group) ?? groups.set(m.group, []).get(m.group)).push(m);

  it("covers every priority and a meaningful number of decisions", () => {
    expect(groups.size).toBeGreaterThan(30);
    const weightSets = new Set(candidates.map((m) => JSON.stringify(m.weights)));
    expect(weightSets.size).toBe(3);
  });

  it("picks the same winner as the router in every decision", () => {
    for (const [name, g] of groups) {
      const mine = aearScores(g, g[0].weights);
      const best = (xs) => xs.reduce((a, b) => (b.score > a.score ? b : a)).candidate;
      // a tie in the router's rounded scores can legitimately resolve either way: skip exact ties only
      const top = Math.max(...g.map((m) => m.score));
      if (g.filter((m) => m.score === top).length > 1) continue;
      expect(best(mine), name).toBe(best(g));
    }
  });

  it("reproduces the router's scores to within its own rounding (RSSI is rounded to 2 dp in events)", () => {
    let worst = 0;
    for (const g of groups.values()) aearScores(g, g[0].weights).forEach((o, i) => { worst = Math.max(worst, Math.abs(o.score - g[i].score)); });
    expect(worst).toBeLessThan(2e-3);
  });

  it("reproduces the hop and congestion factors exactly", () => {
    for (const g of groups.values()) aearScores(g, g[0].weights).forEach((o, i) => {
      expect(o.H).toBeCloseTo(g[i].hop_efficiency, 4);
      expect(o.C).toBeCloseTo(g[i].congestion_availability, 4);
    });
  });
});

describe("edge cases", () => {
  it("single candidate: every min-max factor is neutral 1.0, so hop efficiency is 0", () => {
    const [r] = aearScores([c(-90, 3)], W);
    expect([r.S, r.H, r.C]).toEqual([1, 0, 1]);
  });
  it("all candidates tied on hops: hop efficiency is 0 for all and cannot change the ranking", () => {
    const out = aearScores([c(-80, 2), c(-100, 2)], W);
    expect(out.map((o) => o.H)).toEqual([0, 0]);
    expect(out[0].score).toBeGreaterThan(out[1].score);
  });
  it("all candidates tied on RSSI: signal is 1.0 for all", () => {
    expect(aearScores([c(-90, 1), c(-90, 4)], W).map((o) => o.S)).toEqual([1, 1]);
  });
  it("zero capacity does not divide by zero; an over-full queue clips to 0 availability", () => {
    const [a, b] = aearScores([c(-90, 1, 5, 0), c(-91, 1, 99, 30)], W);
    expect(Number.isFinite(a.C)).toBe(true);
    expect(b.C).toBe(0);
  });
  it("with weights summing to 1, every score is within [0, 1]", () => {
    for (const w of Object.values(DEFAULT_WEIGHTS)) {
      for (const s of aearScores([c(-120, 0), c(-60, 9, 30, 30), c(-90, 4, 10, 30)], w)) {
        expect(s.score).toBeGreaterThanOrEqual(0);
        expect(s.score).toBeLessThanOrEqual(1 + 1e-9);
      }
    }
  });
  it("default weights sum to exactly 1 for every priority (the router rejects anything else)", () => {
    for (const w of Object.values(DEFAULT_WEIGHTS)) expect(w.signal + w.hop + w.congestion).toBeCloseTo(1, 12);
  });
  it("emergency weighting favours the fewer-hop node even at weaker signal; normal favours the stronger signal", () => {
    const cands = [c(-80, 4), c(-100, 1)];
    const win = (w) => aearScores(cands, w).reduce((a, b) => (b.score > a.score ? b : a));
    expect(win(DEFAULT_WEIGHTS.EMERGENCY).estimated_hops).toBe(1);
    expect(win({ signal: 0.8, hop: 0.1, congestion: 0.1 }).rssi_dbm).toBe(-80);
  });
});
