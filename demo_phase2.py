"""
Phase 2 demo — the discrete-event engine driving a packet across multiple
hops via a simple hand-written "relay" handler (real AEAR/RSSI/AODV logic
arrives in Phase 3). Also demonstrates node failure interrupting a flow.

Run: python3 demo_phase2.py
"""

from backend.simulation.network import Network
from backend.simulation.engine.engine import SimulationEngine
from backend.simulation.events.event import EventType
from backend.simulation.packets.packet import PacketType, PacketStatus, reset_packet_id_counter

reset_packet_id_counter()

print("=== Building a small line topology: N1 - N2 - N3 - N4 ===")
net = Network(communication_range_m=150, seed=1)
net.add_node("N1", 0, 0)
net.add_node("N2", 100, 0)
net.add_node("N3", 200, 0)
net.add_node("N4", 300, 0)
engine = SimulationEngine(net)


def log_watcher(event, eng):
    print(f"  [{event.timestamp:6.2f}s] {event.event_type.value:<20} "
          f"node={event.node_id} packet={event.packet_id} meta={event.metadata}")


for et in EventType:
    engine.register_handler(et, log_watcher)


def naive_relay(event, eng: SimulationEngine):
    """Toy 'routing': always forward toward the next node in a fixed line,
    one hop per event, purely to exercise the engine end-to-end."""
    packet = eng.packets[event.packet_id]
    if packet.is_terminal:
        return
    line = ["N1", "N2", "N3", "N4"]
    idx = line.index(packet.current_node)
    if idx + 1 >= len(line):
        return
    next_node = line[idx + 1]
    available = [n.node_id for n in eng.network.available_neighbors_of(packet.current_node)]
    if next_node not in available:
        eng.buffer_packet(packet.packet_id, reason=f"{next_node} unavailable")
        return
    eng.forward_packet(packet.packet_id, next_node, transmission_delay=1.0)


engine.register_handler(EventType.PACKET_GENERATED, naive_relay)
engine.register_handler(EventType.PACKET_RECEIVED, naive_relay)

print("\n=== Scenario A: normal delivery, N1 -> N4 ===")
p1 = engine.generate_packet(0.0, "N1", "N4", PacketType.EMERGENCY, message="help", ttl=10)
engine.run()
print(f"Result: {p1}")
print(f"Hops: {p1.hop_count}, delivered at t={p1.delivery_time}")

print("\n=== Scenario B: N3 fails mid-flight, packet gets buffered ===")
# NOTE: engine.reset() deliberately keeps handlers registered (so a routing
# engine registers once and is reused across many experiment runs) — so we
# must NOT re-register naive_relay/log_watcher here, or every event fires
# twice. This is intentional engine behavior; just a thing to remember.
engine.reset(network=net)

engine.fail_node_at(0.5, "N3")  # fails after N1->N2 forward, before N2->N3
p2 = engine.generate_packet(0.0, "N1", "N4", PacketType.NORMAL, message="routine", ttl=10)
engine.run()
print(f"Result: {p2}")
print(f"Status: {p2.status.value}")

print("\n=== Event log summary (Scenario B) ===")
for event_type, count in engine.event_log.count_by_type().items():
    print(f"  {event_type}: {count}")

print("\nPhase 2 demo complete.")
