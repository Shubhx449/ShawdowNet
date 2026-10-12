"""
Phase 3c demo — AODV route discovery, data delivery, and route maintenance
under a mid-route node failure. Shows the full control-plane event trail
(RREQ/RREP/RERR) the way Developer/Debug mode will surface it later.

Run: python3 demo_phase3_aodv.py
"""

from backend.simulation.network import Network
from backend.simulation.engine.engine import SimulationEngine
from backend.simulation.events.event import EventType
from backend.simulation.packets.packet import PacketType, reset_packet_id_counter
from backend.simulation.routing.aodv import AODVRouter

CONTROL_EVENTS = {
    EventType.RREQ_SENT, EventType.RREQ_RECEIVED,
    EventType.RREP_SENT, EventType.RREP_RECEIVED,
    EventType.RERR_SENT,
}


def print_control_event(event, engine):
    if event.event_type not in CONTROL_EVENTS and event.event_type not in (
        EventType.PACKET_FORWARDED, EventType.PACKET_DELIVERED, EventType.PACKET_BUFFERED,
        EventType.NODE_FAILED,
    ):
        return
    meta = {k: v for k, v in event.metadata.items()}
    print(f"  [{event.timestamp:6.3f}s] {event.event_type.value:<18} node={event.node_id!s:<5} {meta}")


print("=== Building a 4-node line: N1 - N2 - N3 - N4 (range=150m) ===")
net = Network(communication_range_m=150, seed=1)
net.add_node("N1", 0, 0)
net.add_node("N2", 100, 0)
net.add_node("N3", 200, 0)
net.add_node("N4", 300, 0)

reset_packet_id_counter()
engine = SimulationEngine(net)
router = AODVRouter(net, engine)
for et in list(CONTROL_EVENTS) + [EventType.PACKET_FORWARDED, EventType.PACKET_DELIVERED,
                                   EventType.PACKET_BUFFERED, EventType.NODE_FAILED]:
    engine.register_handler(et, print_control_event)

print("\n=== Sending N1 -> N4 (no route known yet -> triggers RREQ flood) ===")
p1 = engine.generate_packet(0.0, "N1", "N4", PacketType.NORMAL, message="hello", ttl=10)
engine.run()

print(f"\nResult: {p1}")
print(f"Path: {' -> '.join(p1.visited_nodes)}")
print("\nRouting table at N1:")
for dest, entry in router.dump_routing_table("N1").items():
    print(f"  -> {dest}: {entry}")

print("\n=== Sending a SECOND packet N1 -> N4 (route already known -> no new RREQ) ===")
rreq_count_before = len(engine.event_log.filter_by_type(EventType.RREQ_SENT))
p2 = engine.generate_packet(engine.clock, "N1", "N4", PacketType.NORMAL, message="hi again", ttl=10)
engine.run()
rreq_count_after = len(engine.event_log.filter_by_type(EventType.RREQ_SENT))
print(f"Result: {p2}")
print(f"RREQ_SENT events before={rreq_count_before}, after={rreq_count_after} "
      f"(unchanged means the cached route was reused, as expected)")

print("\n=== Failing N3 mid-network (breaks the only path) and sending a THIRD packet ===")
engine.fail_node_at(engine.clock + 0.01, "N3")
engine.run()
p3 = engine.generate_packet(engine.clock, "N1", "N4", PacketType.EMERGENCY, message="sos", ttl=10)
engine.run()
print(f"\nResult: {p3}")
print(f"Status: {p3.status.value} (expected BUFFERED -- N3 was the only path to N4)")

print("\nRouting table at N1 after N3 fails:")
for dest, entry in router.dump_routing_table("N1").items():
    print(f"  -> {dest}: {entry}")

print(f"\nTotal RREQ attempts by N1 across this run: {router.rreq_counters['N1']}")
print("\nPhase 3c (AODV) demo complete.")
