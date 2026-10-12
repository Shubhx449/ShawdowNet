"""
Phase 1 tests: Node, Link, Network, PacketQueue, Packet.

Run with:
    python -m pytest tests/test_phase1_models.py -v
or, with no pytest installed:
    python tests/test_phase1_models.py
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.simulation.network import Network, NodeStatus, LinkStatus
from backend.simulation.queues.queue_model import PacketQueue
from backend.simulation.packets.packet import (
    Packet, PacketType, PacketStatus, reset_packet_id_counter,
)


class TestPacketQueue(unittest.TestCase):
    def test_enqueue_dequeue_fifo(self):
        q = PacketQueue(capacity=2)
        reset_packet_id_counter()
        p1 = Packet(source="N1", destination="N2", packet_type=PacketType.NORMAL)
        p2 = Packet(source="N1", destination="N2", packet_type=PacketType.NORMAL)
        self.assertTrue(q.enqueue(p1))
        self.assertTrue(q.enqueue(p2))
        self.assertEqual(q.dequeue().packet_id, p1.packet_id)
        self.assertEqual(q.dequeue().packet_id, p2.packet_id)
        self.assertIsNone(q.dequeue())

    def test_overflow_when_full(self):
        q = PacketQueue(capacity=1)
        reset_packet_id_counter()
        p1 = Packet(source="N1", destination="N2", packet_type=PacketType.NORMAL)
        p2 = Packet(source="N1", destination="N2", packet_type=PacketType.NORMAL)
        self.assertTrue(q.enqueue(p1))
        self.assertFalse(q.enqueue(p2))  # queue full -> overflow
        self.assertEqual(q.total_overflow_count, 1)

    def test_occupancy_ratio(self):
        q = PacketQueue(capacity=4)
        reset_packet_id_counter()
        for _ in range(2):
            q.enqueue(Packet(source="N1", destination="N2", packet_type=PacketType.NORMAL))
        self.assertAlmostEqual(q.occupancy_ratio(), 0.5)


class TestNetworkTopology(unittest.TestCase):
    def test_reproducible_topology_with_same_seed(self):
        net1 = Network.generate_random(num_nodes=10, seed=42)
        net2 = Network.generate_random(num_nodes=10, seed=42)
        pos1 = sorted((n.node_id, round(n.x, 6), round(n.y, 6)) for n in net1.nodes.values())
        pos2 = sorted((n.node_id, round(n.x, 6), round(n.y, 6)) for n in net2.nodes.values())
        self.assertEqual(pos1, pos2)
        self.assertEqual(sorted(net1.links.keys()), sorted(net2.links.keys()))

    def test_different_seed_gives_different_topology(self):
        net1 = Network.generate_random(num_nodes=10, seed=1)
        net2 = Network.generate_random(num_nodes=10, seed=2)
        pos1 = [(n.x, n.y) for n in net1.nodes.values()]
        pos2 = [(n.x, n.y) for n in net2.nodes.values()]
        self.assertNotEqual(pos1, pos2)

    def test_nodes_beyond_range_are_not_linked(self):
        net = Network(communication_range_m=100, seed=1)
        net.add_node("A", 0, 0)
        net.add_node("B", 50, 0)     # within range
        net.add_node("C", 500, 500)  # far outside range
        self.assertIsNotNone(net.get_link("A", "B"))
        self.assertIsNone(net.get_link("A", "C"))

    def test_manual_node_add_creates_correct_links(self):
        net = Network(communication_range_m=200, seed=1)
        net.add_node("A", 0, 0)
        net.add_node("B", 100, 0)
        neighbors_of_a = [n.node_id for n in net.neighbors_of("A")]
        self.assertIn("B", neighbors_of_a)


class TestAvailability(unittest.TestCase):
    def setUp(self):
        self.net = Network(communication_range_m=200, seed=1)
        self.net.add_node("A", 0, 0)
        self.net.add_node("B", 100, 0)
        self.net.add_node("C", 0, 100)

    def test_failed_node_excluded_from_available_neighbors(self):
        self.net.fail_node("B")
        available = [n.node_id for n in self.net.available_neighbors_of("A")]
        self.assertNotIn("B", available)
        self.assertIn("C", available)
        self.assertEqual(self.net.get_node("B").status, NodeStatus.FAILED)

    def test_restored_node_reappears_as_available(self):
        self.net.fail_node("B")
        self.net.restore_node("B")
        available = [n.node_id for n in self.net.available_neighbors_of("A")]
        self.assertIn("B", available)

    def test_failed_source_node_has_no_available_neighbors(self):
        self.net.fail_node("A")
        self.assertEqual(self.net.available_neighbors_of("A"), [])

    def test_failed_link_excludes_that_neighbor_only(self):
        self.net.fail_link("A", "B")
        available = [n.node_id for n in self.net.available_neighbors_of("A")]
        self.assertNotIn("B", available)
        self.assertIn("C", available)

    def test_restored_link_reappears(self):
        self.net.fail_link("A", "B")
        self.net.restore_link("A", "B")
        available = [n.node_id for n in self.net.available_neighbors_of("A")]
        self.assertIn("B", available)

    def test_congestion_status_updates_from_queue(self):
        node = self.net.get_node("A")
        node.queue.capacity = 4
        for _ in range(3):  # 3/4 = 0.75 >= default threshold 0.75
            node.queue.enqueue(
                Packet(source="A", destination="B", packet_type=PacketType.NORMAL)
            )
        node.on_queue_changed()
        self.assertEqual(node.status, NodeStatus.CONGESTED)


class TestPacketLifecycle(unittest.TestCase):
    def setUp(self):
        reset_packet_id_counter()

    def test_packet_starts_at_source_with_visited_list(self):
        p = Packet(source="N1", destination="N5", packet_type=PacketType.EMERGENCY, ttl=5)
        self.assertEqual(p.current_node, "N1")
        self.assertEqual(p.visited_nodes, ["N1"])
        self.assertEqual(p.priority, 2)  # EMERGENCY -> priority 2
        self.assertEqual(p.status, PacketStatus.IN_TRANSIT)

    def test_forwarding_decrements_ttl_and_tracks_visited(self):
        p = Packet(source="N1", destination="N5", packet_type=PacketType.NORMAL, ttl=3)
        p.mark_forwarded(timestamp=1.0, next_node="N2")
        self.assertEqual(p.ttl, 2)
        self.assertEqual(p.current_node, "N2")
        self.assertEqual(p.previous_node, "N1")
        self.assertEqual(p.hop_count, 1)
        self.assertIn("N2", p.visited_nodes)

    def test_delivery_sets_status_and_timestamp(self):
        p = Packet(source="N1", destination="N2", packet_type=PacketType.NORMAL, ttl=3)
        p.mark_forwarded(timestamp=2.5, next_node="N2")
        self.assertTrue(p.is_delivered)
        self.assertEqual(p.delivery_time, 2.5)

    def test_cannot_revisit_a_node_is_checkable_via_has_visited(self):
        p = Packet(source="N1", destination="N5", packet_type=PacketType.NORMAL, ttl=5)
        p.mark_forwarded(timestamp=1.0, next_node="N2")
        self.assertTrue(p.has_visited("N1"))
        self.assertTrue(p.has_visited("N2"))
        self.assertFalse(p.has_visited("N3"))

    def test_ttl_expiry_drop(self):
        p = Packet(source="N1", destination="N9", packet_type=PacketType.NORMAL, ttl=1)
        p.mark_forwarded(timestamp=1.0, next_node="N2")  # ttl 1 -> 0
        self.assertEqual(p.ttl, 0)
        p.mark_dropped(timestamp=1.1, status=PacketStatus.DROPPED_TTL_EXPIRED, reason="TTL hit 0")
        self.assertTrue(p.is_dropped)
        self.assertTrue(p.is_terminal)

    def test_retransmission_limit(self):
        p = Packet(source="N1", destination="N9", packet_type=PacketType.NORMAL,
                   max_retransmissions=2)
        self.assertTrue(p.mark_retransmission(timestamp=1.0))   # attempt 1 < 2
        self.assertFalse(p.mark_retransmission(timestamp=1.1))  # attempt 2 >= 2 -> exhausted

    def test_buffer_and_unbuffer_cycle(self):
        p = Packet(source="N1", destination="N9", packet_type=PacketType.IMPORTANT)
        p.mark_buffered(timestamp=5.0, reason="No valid forwarding candidate")
        self.assertEqual(p.status, PacketStatus.BUFFERED)
        self.assertEqual(p.buffered_at, "N1")
        p.mark_unbuffered()
        self.assertEqual(p.status, PacketStatus.IN_TRANSIT)
        self.assertIsNone(p.buffered_at)

    def test_forwarding_history_is_recorded(self):
        p = Packet(source="N1", destination="N3", packet_type=PacketType.NORMAL, ttl=5)
        p.mark_forwarded(timestamp=1.0, next_node="N2")
        p.mark_forwarded(timestamp=2.0, next_node="N3")
        actions = [e.action for e in p.forwarding_history]
        self.assertIn("FORWARDED", actions)
        self.assertIn("DELIVERED", actions)

    def test_packet_ids_are_unique_and_sequential(self):
        p1 = Packet(source="N1", destination="N2", packet_type=PacketType.NORMAL)
        p2 = Packet(source="N1", destination="N2", packet_type=PacketType.NORMAL)
        self.assertNotEqual(p1.packet_id, p2.packet_id)


if __name__ == "__main__":
    unittest.main(verbosity=2)
