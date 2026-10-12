"""
Phase 3a demo — AEAR routing engine.

Shows exactly the kind of decision panel Mode 1's UI will eventually render:
for each hop, the candidate set, their normalized factors, weights, scores,
and the selected next hop -- all computed live, nothing hardcoded.

Run: python3 demo_phase3_aear.py
"""

from backend.simulation.network import Network
from backend.simulation.engine.engine import SimulationEngine
from backend.simulation.events.event import EventType
from backend.simulation.packets.packet import PacketType, reset_packet_id_counter
from backend.simulation.routing.aear import AEARRouter, AEARConfig, AEARWeights

reset_packet_id_counter()

print("=== Generating a 15-node network (seed=42, range=350m, 1000x1000m) ===")
net = Network.generate_random(num_nodes=15, communication_range_m=350, seed=42)
engine = SimulationEngine(net)
router = AEARRouter(net, engine)  # default priority-dependent weights

decisions_seen = set()


def print_decision_panel(event, eng: SimulationEngine):
    if event.event_type != EventType.FORWARDING_DECISION:
        return
    key = (event.node_id, event.packet_id, event.timestamp)
    if key in decisions_seen:
        return
    decisions_seen.add(key)
    packet = eng.packets[event.packet_id]
    candidates = router.last_candidates.get(event.packet_id, [])

    print(f"\n--- Decision at {event.node_id} for {event.packet_id} "
          f"(priority={packet.packet_type.value}, ttl={packet.ttl}) ---")
    for c in sorted(candidates, key=lambda c: -c.score):
        marker = "-> SELECTED" if c.status == "SELECTED" else ""
        print(f"  {c.node_id:>5}  RSSI={c.rssi_dbm:6.1f}dBm  normRSSI={c.normalized_rssi:.2f}  "
              f"hops~{c.estimated_hops}  hopEff={c.hop_efficiency:.2f}  "
              f"queue={c.queue_occupancy}/{c.queue_capacity}  cong={c.congestion_availability:.2f}  "
              f"SCORE={c.score:.3f} {marker}")


engine.register_handler(EventType.FORWARDING_DECISION, print_decision_panel)

print("\n=== Sending an EMERGENCY packet N1 -> N15 ===")
p = engine.generate_packet(0.0, "N1", "N15", PacketType.EMERGENCY,
                            message="Emergency packet", ttl=15)
engine.run()

print(f"\nFinal result: {p}")
print(f"Path taken: {' -> '.join(p.visited_nodes)}")
print(f"Hops: {p.hop_count}   Delivered: {p.is_delivered}   "
      f"Delay: {(p.delivery_time - p.creation_time) if p.delivery_time else 'N/A'}s")

print("\n=== Same network, NORMAL priority packet (different weights) ===")
reset_packet_id_counter()
engine.reset(network=net)
decisions_seen.clear()
p2 = engine.generate_packet(0.0, "N1", "N15", PacketType.NORMAL, ttl=15)
engine.run()
print(f"\nFinal result: {p2}")
print(f"Path taken: {' -> '.join(p2.visited_nodes)}")

print("\nPhase 3a (AEAR) demo complete.")
