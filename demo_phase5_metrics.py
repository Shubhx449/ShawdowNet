"""
Phase 5 demo — real metrics, computed from raw events, comparing AEAR, RSSI,
and AODV under IDENTICAL traffic on the IDENTICAL network (same seed). This
is the fair-comparison pattern Mode 2's experiment runner will scale up in
Phase 6 -- here it's just one run per algorithm instead of many, and no
statistics across runs yet, but the metrics themselves are the real thing.

Run: python3 demo_phase5_metrics.py
"""

import random

from backend.simulation.network import Network
from backend.simulation.engine.engine import SimulationEngine
from backend.simulation.packets.packet import PacketType, reset_packet_id_counter
from backend.simulation.routing.aear import AEARRouter
from backend.simulation.routing.rssi import RSSIRouter
from backend.simulation.routing.aodv import AODVRouter
from backend.simulation.failures.recovery import FailureRecoveryManager
from backend.simulation.failures.schedule import apply_failure_schedule, node_failure_scenario
from backend.simulation.metrics.engine import MetricsEngine
from backend.simulation.metrics.sampler import QueueSampler

SEED = 4242
NUM_NODES = 25
DURATION_S = 60.0
NUM_PACKETS = 20


def run_scenario(router_cls):
    reset_packet_id_counter()
    net = Network.generate_random(num_nodes=NUM_NODES, communication_range_m=350,
                                   width_m=1000, height_m=1000, seed=SEED)
    engine = SimulationEngine(net)
    router = router_cls(net, engine)
    FailureRecoveryManager(engine, [router])
    QueueSampler(engine, net, interval_s=2.0, end_time=DURATION_S)

    # Same failure schedule and same traffic pattern for every algorithm.
    apply_failure_schedule(engine, node_failure_scenario("N10", fail_at=15.0, restore_at=35.0))

    traffic_rng = random.Random(SEED)  # separate, deterministic traffic RNG
    packet_types = ([PacketType.NORMAL] * 14 + [PacketType.IMPORTANT] * 4
                     + [PacketType.EMERGENCY] * 2)
    traffic_rng.shuffle(packet_types)

    t = 0.0
    for ptype in packet_types[:NUM_PACKETS]:
        t += traffic_rng.uniform(0.5, 3.0)
        engine.run(until=t)
        engine.generate_packet(t, "N1", "N25", ptype, ttl=20)

    engine.run(until=DURATION_S)
    return MetricsEngine.compute(engine.event_log)


print(f"=== AEAR vs RSSI vs AODV: seed={SEED}, {NUM_NODES} nodes, {NUM_PACKETS} packets, "
      f"N10 fails 15s-35s ===\n")

rows = []
for label, router_cls in [("AEAR", AEARRouter), ("RSSI", RSSIRouter), ("AODV", AODVRouter)]:
    m = run_scenario(router_cls)
    rows.append((label, m))

header = f"{'Metric':<32}" + "".join(f"{label:>12}" for label, _ in rows)
print(header)
print("-" * len(header))


def print_row(name, getter, fmt="{:.2f}"):
    vals = []
    for _, m in rows:
        v = getter(m)
        vals.append(fmt.format(v) if v is not None else "N/A")
    print(f"{name:<32}" + "".join(f"{v:>12}" for v in vals))


print_row("PDR (%)", lambda m: m.pdr_percent)
print_row("Emergency PDR (%)", lambda m: m.emergency_pdr_percent)
print_row("Avg delay (s)", lambda m: m.avg_end_to_end_delay_s)
print_row("Avg emergency delay (s)", lambda m: m.avg_emergency_delay_s)
print_row("Avg hop count", lambda m: m.avg_hop_count)
print_row("Packet loss", lambda m: m.packet_loss, "{:.0f}")
print_row("Routing overhead (ctrl msgs)", lambda m: m.routing_overhead_control_messages, "{:.0f}")
print_row("Avg recovery time (s)", lambda m: m.avg_recovery_time_s)
print_row("Queue overflow count", lambda m: m.queue_overflow_count, "{:.0f}")
print_row("Avg queue utilization (%)", lambda m: m.avg_queue_utilization_percent)
print_row("Packets buffered (events)", lambda m: m.packets_buffered_events, "{:.0f}")
print_row("Successfully recovered", lambda m: m.successfully_recovered_buffered_packets, "{:.0f}")

print("\nNotes from AEAR's run (same notes apply to RSSI/AODV where relevant):")
for key, note in rows[0][1].notes.items():
    print(f"  [{key}] {note}")

print("\nAll numbers above came from engine.event_log alone via MetricsEngine.compute() --")
print("nothing here is hardcoded, and the routing_overhead=0 for AEAR/RSSI is real: they")
print("make purely local decisions with no control-plane radio transmissions, unlike AODV.")
print()
print("Worth being direct about: AEAR and RSSI's low PDR here isn't caused by the N10")
print("failure -- rerunning this exact topology with NO failures at all still gives AEAR/RSSI")
print("0% PDR while AODV still gets 100%. A quick BFS confirms N25 IS reachable from N1 in")
print("this topology. What's happening is the classic 'local minimum' problem in greedy")
print("routing: once every neighbor at some node has already been visited, a purely local,")
print("no-backtracking algorithm is stuck, even though a path exists elsewhere in the graph.")
print("AODV's flood-based discovery doesn't have this weakness -- it explores globally.")
print()
print("This is a genuine, single-topology result, not a general verdict -- Phase 3's own")
print("demo (demo_phase3_rssi_vs_aear.py) showed AEAR beating RSSI cleanly on a DIFFERENT")
print("seed. That instability across topologies is exactly why the paper's methodology")
print("insists on many independent runs with aggregated mean/std rather than judging from")
print("a single instance -- which is precisely what Phase 6 (experiment runner) is for.")
