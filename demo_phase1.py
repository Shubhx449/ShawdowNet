"""
Phase 1 demo — no routing yet, just proves the models behave correctly.

Run: python3 demo_phase1.py
"""

from backend.simulation.network import Network
from backend.simulation.packets.packet import Packet, PacketType, reset_packet_id_counter

reset_packet_id_counter()

print("Generating a 10-node network (seed=12345, range=430m, area=1000x1000m)...")
net = Network.generate_random(num_nodes=10, seed=12345)
print(net)
for node in net.nodes.values():
    neighbor_ids = [n.node_id for n in net.neighbors_of(node.node_id)]
    print(f"  {node.node_id} at ({node.x:.0f},{node.y:.0f}) -> neighbors: {neighbor_ids}")

print("\nCreating an EMERGENCY packet N1 -> N5...")
p = Packet(source="N1", destination="N5", packet_type=PacketType.EMERGENCY,
           message="Emergency packet", ttl=12)
print(p)

print("\nSimulating manual forwarding N1 -> N2 -> N5...")
p.mark_forwarded(timestamp=0.1, next_node="N2")
print(p)
p.mark_forwarded(timestamp=0.3, next_node="N5")
print(p)
print(f"Delivered: {p.is_delivered}, delivery_time={p.delivery_time}")

print("\nFailing node N2 and checking neighbor availability from N1...")
net.fail_node("N2")
available = [n.node_id for n in net.available_neighbors_of("N1")]
print(f"  Available neighbors of N1 after N2 fails: {available}")

print("\nQueue overflow demo (capacity=2)...")
node = net.get_node("N1")
node.queue.capacity = 2
for i in range(3):
    ok = node.queue.enqueue(
        Packet(source="N1", destination="N9", packet_type=PacketType.NORMAL)
    )
    print(f"  enqueue #{i+1}: {'OK' if ok else 'OVERFLOW'}")
print(f"  total_overflow_count = {node.queue.total_overflow_count}")

print("\nPhase 1 demo complete.")
