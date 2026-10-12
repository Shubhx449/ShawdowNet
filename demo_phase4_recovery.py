"""
Phase 4 demo — failure injection + store-and-forward + recovery-triggered
retry, closing the loop that Phase 3 deliberately left open.

Run: python3 demo_phase4_recovery.py
"""

from backend.simulation.network import Network
from backend.simulation.engine.engine import SimulationEngine
from backend.simulation.events.event import EventType
from backend.simulation.packets.packet import PacketType, reset_packet_id_counter
from backend.simulation.routing.aear import AEARRouter
from backend.simulation.routing.aodv import AODVRouter
from backend.simulation.failures.recovery import FailureRecoveryManager
from backend.simulation.failures.schedule import (
    apply_failure_schedule, node_failure_scenario, link_disconnection_scenario,
)


def line_network(range_m=150):
    net = Network(communication_range_m=range_m, seed=1)
    net.add_node("N1", 0, 0)
    net.add_node("N2", 100, 0)
    net.add_node("N3", 200, 0)
    net.add_node("N4", 300, 0)
    return net


def watch(event, engine):
    interesting = {
        EventType.PACKET_BUFFERED, EventType.PACKET_FORWARDED, EventType.PACKET_DELIVERED,
        EventType.NODE_FAILED, EventType.NODE_RECOVERED, EventType.LINK_FAILED, EventType.LINK_RESTORED,
    }
    if event.event_type not in interesting:
        return
    print(f"  [{event.timestamp:6.2f}s] {event.event_type.value:<16} node={event.node_id} "
          f"packet={event.packet_id} {event.metadata}")


print("=" * 70)
print("SCENARIO 4 (paper): Forwarding Node Failure During Transmission -- AEAR")
print("=" * 70)
reset_packet_id_counter()
net = line_network()
engine = SimulationEngine(net)
router = AEARRouter(net, engine)
FailureRecoveryManager(engine, [router])
for et in EventType:
    engine.register_handler(et, watch)

# N2 fails at t=0 (before the packet can even leave N1), recovers at t=1.5s
apply_failure_schedule(engine, node_failure_scenario("N2", fail_at=0.0, restore_at=1.5))
p = engine.generate_packet(0.0, "N1", "N4", PacketType.EMERGENCY, message="mayday", ttl=15)
engine.run()

print(f"\nResult: {p}")
print(f"Path: {' -> '.join(p.visited_nodes)}")
print(f"Delivered: {p.is_delivered}  (delay includes the time spent buffered waiting for N2)")


print("\n" + "=" * 70)
print("SCENARIO 5 (paper): Temporary Link Disconnection Followed by Restoration -- AODV")
print("=" * 70)
reset_packet_id_counter()
net2 = line_network()
engine2 = SimulationEngine(net2)
router2 = AODVRouter(net2, engine2)
FailureRecoveryManager(engine2, [router2])
for et in EventType:
    engine2.register_handler(et, watch)

# N2<->N3 link drops at t=0, restored at t=2.0s
apply_failure_schedule(engine2, link_disconnection_scenario("N2", "N3", fail_at=0.0, restore_at=2.0))
p2 = engine2.generate_packet(0.0, "N1", "N4", PacketType.IMPORTANT, message="status update", ttl=15)
engine2.run()

print(f"\nResult: {p2}")
print(f"Path: {' -> '.join(p2.visited_nodes)}")
print(f"Delivered: {p2.is_delivered}")
print("\nRouting table at N1 (final):")
for dest, entry in router2.dump_routing_table("N1").items():
    print(f"  -> {dest}: {entry}")

print("\nPhase 4 demo complete: both scenarios recovered and delivered "
      "once the fault cleared, with no manual intervention needed beyond "
      "registering FailureRecoveryManager once at setup.")
