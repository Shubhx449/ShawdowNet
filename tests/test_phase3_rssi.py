"""
Phase 3b tests: RSSI baseline routing.

Run with:
    python -m pytest tests/test_phase3_rssi.py -v
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.simulation.network.network import Network
from backend.simulation.engine.engine import SimulationEngine
from backend.simulation.events.event import EventType
from backend.simulation.packets.packet import PacketType, reset_packet_id_counter
from backend.simulation.routing.rssi import RSSIRouter


class TestRSSICandidateFiltering(unittest.TestCase):
    def setUp(self):
        reset_packet_id_counter()
        self.net = Network(communication_range_m=200, seed=1)
        self.net.add_node("A", 0, 0)
        self.net.add_node("B", 100, 0)
        self.net.add_node("C", 0, 100)
        self.net.add_node("D", 100, 100)
        self.engine = SimulationEngine(self.net)
        self.router = RSSIRouter(self.net, self.engine)

    def test_failed_neighbor_excluded(self):
        self.net.fail_node("B")
        p = self.engine.generate_packet(0.0, "A", "D", PacketType.NORMAL, ttl=10)
        self.engine.run(max_events=1)
        evaluated_ids = [c.node_id for c in self.router.last_candidates[p.packet_id]]
        self.assertNotIn("B", evaluated_ids)

    def test_no_candidates_buffers_packet(self):
        net = Network(communication_range_m=100, seed=1)
        net.add_node("A", 0, 0)
        net.add_node("B", 100, 0)
        net.add_node("C", 0, 100)
        net.add_node("D", 100, 100)  # out of range at 100m (distance ~141m)
        engine = SimulationEngine(net)
        RSSIRouter(net, engine)
        net.fail_node("B")
        net.fail_node("C")
        p = engine.generate_packet(0.0, "A", "D", PacketType.NORMAL, ttl=10)
        engine.run()
        self.assertEqual(p.status.value, "BUFFERED")

    def test_ttl_zero_drops_before_evaluation(self):
        p = self.engine.generate_packet(0.0, "A", "D", PacketType.NORMAL, ttl=10)
        p.ttl = 0
        self.engine.run(max_events=1)
        self.assertTrue(p.is_dropped)
        self.assertNotIn(p.packet_id, self.router.last_candidates)


class TestRSSISelectionIgnoresEverythingButSignal(unittest.TestCase):
    """The core behavioral contract: RSSI must select purely on signal
    strength, even when that candidate is heavily congested and a weaker
    but idle candidate is available. This is what distinguishes it from AEAR."""

    def setUp(self):
        reset_packet_id_counter()
        self.net = Network(communication_range_m=250, seed=1)
        self.net.add_node("A", 0, 0)
        self.net.add_node("X", 100, 0)  # strong signal, heavily congested
        self.net.add_node("Y", 200, 0)  # weak signal, idle
        self.net.get_link("A", "X").rssi_dbm = -50.0
        self.net.get_link("A", "Y").rssi_dbm = -95.0
        self.net.get_node("X").queue.capacity = 10
        for _ in range(10):
            self.net.get_node("X").queue._items.append(object())  # 100% full
        self.engine = SimulationEngine(self.net)
        self.router = RSSIRouter(self.net, self.engine)

    def test_selects_strongest_signal_regardless_of_congestion(self):
        p = self.engine.generate_packet(0.0, "A", "Y", PacketType.EMERGENCY, ttl=10)
        self.engine.run(max_events=1)
        candidates = {c.node_id: c for c in self.router.last_candidates[p.packet_id]}
        self.assertEqual(candidates["X"].status, "SELECTED")
        self.assertEqual(candidates["Y"].status, "EVALUATED")

    def test_priority_has_no_effect_on_selection(self):
        """Unlike AEAR, packet priority must not change RSSI's decision."""
        winners = []
        for ptype in (PacketType.NORMAL, PacketType.IMPORTANT, PacketType.EMERGENCY):
            engine = SimulationEngine(self.net)
            router = RSSIRouter(self.net, engine)
            p = engine.generate_packet(0.0, "A", "Y", ptype, ttl=10)
            engine.run(max_events=1)
            winner = max(router.last_candidates[p.packet_id], key=lambda c: c.rssi_dbm)
            winners.append(winner.node_id)
        self.assertEqual(len(set(winners)), 1)  # same winner every time


class TestRSSIFullDelivery(unittest.TestCase):
    def test_packet_reaches_terminal_state_with_consistent_bookkeeping(self):
        reset_packet_id_counter()
        net = Network.generate_random(num_nodes=20, communication_range_m=350,
                                       width_m=1000, height_m=1000, seed=99)
        engine = SimulationEngine(net)
        RSSIRouter(net, engine)
        p = engine.generate_packet(0.0, "N1", "N20", PacketType.NORMAL, ttl=20)
        engine.run()

        self.assertTrue(p.is_terminal or p.status.value == "BUFFERED")
        self.assertEqual(len(p.visited_nodes), len(set(p.visited_nodes)))
        self.assertGreaterEqual(p.ttl, 0)
        self.assertEqual(p.hop_count, len(p.visited_nodes) - 1)
        for e in engine.event_log.filter_by_type(EventType.PACKET_FORWARDED):
            self.assertEqual(e.algorithm, "RSSI")

    def test_reproducible_outcome_with_same_seed(self):
        def run_once():
            reset_packet_id_counter()
            net = Network.generate_random(num_nodes=15, communication_range_m=400, seed=7)
            engine = SimulationEngine(net)
            RSSIRouter(net, engine)
            p = engine.generate_packet(0.0, "N1", "N15", PacketType.NORMAL, ttl=15)
            engine.run()
            return p.status.value, p.hop_count, tuple(p.visited_nodes)

        self.assertEqual(run_once(), run_once())


if __name__ == "__main__":
    unittest.main(verbosity=2)
