import { describe, expect, it } from "vitest";
import { buildDecisions, eventSummary, EVENT_COLORS } from "../live/decisions";
import aear from "../__fixtures__/events_aear.json";
import rssi from "../__fixtures__/events_rssi.json";
import aodv from "../__fixtures__/events_aodv.json";

const ev = (id, type, node, packet, metadata = {}) => ({ event_id: id, timestamp: id, event_type: type, node_id: node, packet_id: packet, algorithm: "AEAR", metadata });

describe("buildDecisions on real router output", () => {
  it("AEAR: one decision per forwarding decision, each with exactly the candidates the router counted", () => {
    const d = buildDecisions(aear);
    expect(d.length).toBe(aear.filter((e) => e.event_type === "FORWARDING_DECISION").length);
    expect(d.length).toBeGreaterThan(1);
    for (const x of d) {
      expect(x.candidates.length).toBe(x.decision.candidate_count);
      expect(x.candidates.map((c) => c.candidate)).toContain(x.decision.selected);
    }
  });
  it("AEAR: consecutive decisions follow the packet hop by hop", () => {
    const d = buildDecisions(aear);
    for (let i = 1; i < d.length; i++) expect(d[i].node_id).toBe(d[i - 1].decision.selected);
  });
  it("RSSI: every decision has candidates and picks one of them", () => {
    for (const x of buildDecisions(rssi)) expect(x.candidates.map((c) => c.candidate)).toContain(x.decision.selected);
  });
  it("AODV: reports decisions without candidate scores instead of failing", () => {
    const d = buildDecisions(aodv);
    expect(d.length).toBeGreaterThan(0);
    for (const x of d) { expect(x.candidates).toEqual([]); expect(x.decision.selected).toBeTruthy(); }
  });
});

describe("buildDecisions on synthetic streams", () => {
  it("keeps interleaved packets at the same node apart", () => {
    const d = buildDecisions([
      ev(1, "NEIGHBOR_DISCOVERED", "N1", "P1", { neighbors: ["N2", "N3"] }), ev(2, "NEIGHBOR_DISCOVERED", "N1", "P2", { neighbors: ["N2"] }),
      ev(3, "CANDIDATE_EVALUATED", "N1", "P1", { candidate: "N2", score: 0.5 }), ev(4, "CANDIDATE_EVALUATED", "N1", "P2", { candidate: "N2", score: 0.9 }),
      ev(5, "CANDIDATE_EVALUATED", "N1", "P1", { candidate: "N3", score: 0.7 }),
      ev(6, "FORWARDING_DECISION", "N1", "P2", { selected: "N2" }), ev(7, "FORWARDING_DECISION", "N1", "P1", { selected: "N3" }),
    ]);
    expect(d.map((x) => [x.packet_id, x.candidates.length, x.decision.selected])).toEqual([["P2", 1, "N2"], ["P1", 2, "N3"]]);
  });
  it("a packet decided twice at the same node gives two separate decisions, not one merged one", () => {
    const stream = [1, 2].flatMap((k) => [
      ev(k * 10, "NEIGHBOR_DISCOVERED", "N1", "P1", { neighbors: ["N2"] }), ev(k * 10 + 1, "CANDIDATE_EVALUATED", "N1", "P1", { candidate: "N2" }),
      ev(k * 10 + 2, "FORWARDING_DECISION", "N1", "P1", { selected: "N2" })]);
    expect(buildDecisions(stream).map((x) => x.candidates.length)).toEqual([1, 1]);
  });
  it("candidates that never reach a decision (packet buffered) produce no decision", () => {
    expect(buildDecisions([ev(1, "NEIGHBOR_DISCOVERED", "N1", "P1", { neighbors: [] }), ev(2, "PACKET_BUFFERED", "N1", "P1")])).toEqual([]);
  });
  it("ignores events that carry no packet id, and an empty stream", () => {
    expect(buildDecisions([ev(1, "NODE_FAILED", "N3", null)])).toEqual([]);
    expect(buildDecisions([])).toEqual([]);
  });
});

describe("eventSummary", () => {
  it("never throws on any real event, and always returns a string", () => {
    for (const e of [...aear, ...rssi, ...aodv]) expect(typeof eventSummary(e)).toBe("string");
  });
  it("tolerates a missing metadata object", () => {
    expect(eventSummary({ event_type: "NODE_FAILED" })).toBe("");
  });
  it("every event type in real streams has a colour or falls back cleanly", () => {
    const types = new Set([...aear, ...rssi, ...aodv].map((e) => e.event_type));
    expect(types.size).toBeGreaterThan(5);
    for (const t of types) expect(EVENT_COLORS[t] ?? "#7d8fa1").toMatch(/^#[0-9a-f]{6}$/i);
  });
});
