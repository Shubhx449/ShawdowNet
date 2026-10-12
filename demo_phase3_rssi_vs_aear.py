"""
Phase 3b demo — AEAR vs RSSI, same network, same packet, only the algorithm
differs. This is the exact "fair comparison" pattern Mode 2's experiment
runner will use at scale in Phase 6: identical topology/seed/traffic, swap
only the routing module.

Run: python3 demo_phase3_rssi_vs_aear.py
"""

from backend.simulation.network import Network
from backend.simulation.engine.engine import SimulationEngine
from backend.simulation.events.event import EventType
from backend.simulation.packets.packet import PacketType, reset_packet_id_counter
from backend.simulation.routing.aear import AEARRouter
from backend.simulation.routing.rssi import RSSIRouter


def run_algorithm(router_cls, seed, num_nodes, source, destination, packet_type, **router_kwargs):
    reset_packet_id_counter()
    net = Network.generate_random(num_nodes=num_nodes, communication_range_m=350,
                                   width_m=1000, height_m=1000, seed=seed)
    engine = SimulationEngine(net)
    router_cls(net, engine, **router_kwargs)
    p = engine.generate_packet(0.0, source, destination, packet_type, ttl=20)
    engine.run()
    return p, engine


SEED = 2024
NUM_NODES = 20
SOURCE, DEST = "N1", "N20"

print(f"=== Fair comparison: seed={SEED}, {NUM_NODES} nodes, {SOURCE} -> {DEST} ===\n")

for label, router_cls in [("AEAR", AEARRouter), ("RSSI", RSSIRouter)]:
    p, engine = run_algorithm(router_cls, SEED, NUM_NODES, SOURCE, DEST, PacketType.EMERGENCY)
    print(f"[{label}]")
    print(f"  Status:    {p.status.value}")
    print(f"  Hops:      {p.hop_count}")
    print(f"  Path:      {' -> '.join(p.visited_nodes)}")
    if p.is_delivered:
        print(f"  Delay:     {p.delivery_time - p.creation_time:.2f}s")
    forwarded = len(engine.event_log.filter_by_type(EventType.PACKET_FORWARDED))
    print(f"  Forward events logged: {forwarded}")
    print()

print("Same topology, same seed, same source/destination, same packet — the")
print("only difference between these two runs is the routing algorithm. This")
print("is exactly the isolation Mode 2's experiment runner needs for a valid")
print("AEAR vs RSSI vs AODV comparison later (Phase 6).")
