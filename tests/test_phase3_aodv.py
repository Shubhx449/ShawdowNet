"""
Phase 3c tests: AODV routing engine.

Run with:
    python -m pytest tests/test_phase3_aodv.py -v
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.simulation.network.network import Network
from backend.simulation.engine.engine import SimulationEngine
from backend.simulation.events.event import EventType
from backend.simulation.packets.packet import PacketType, reset_packet_id_counter
from backend.simulation.routing.aodv import AODVRouter, AODVConfig


def line_network(range_m=150):
    """N1 - N2 - N3 - N4, only adjacent nodes in range."""
    net = Network(communication_range_m=range_m, seed=1)
    net.add_node("N1", 0, 0)
    net.add_node("N2", 100, 0)
    net.add_node("N3", 200, 0)
    net.add_node("N4", 300, 0)
    return net


def diamond_network(range_m=120):
    """N1 -> {N2, N3} -> N4, with N1-N4 and N2-N3 out of range (two disjoint
    2-hop paths from N1 to N4), used to test RREQ duplicate suppression."""
    net = Network(communication_range_m=range_m, seed=1)
    net.add_node("N1", 0, 0)
    net.add_node("N2", 100, 0)
    net.add_node("N3", 0, 100)
    net.add_node("N4", 100, 100)
    return net


class TestAODVRouteDiscoveryAndDelivery(unittest.TestCase):
    def test_line_topology_discovers_route_and_delivers(self):
        reset_packet_id_counter()
        net = line_network()
        engine = SimulationEngine(net)
        AODVRouter(net, engine)

        p = engine.generate_packet(0.0, "N1", "N4", PacketType.NORMAL, ttl=10)
        engine.run()

        self.assertTrue(p.is_delivered)
        self.assertEqual(p.hop_count, 3)
        self.assertEqual(p.visited_nodes, ["N1", "N2", "N3", "N4"])

        for e in engine.event_log.filter_by_type(EventType.PACKET_FORWARDED):
            self.assertEqual(e.algorithm, "AODV")

        self.assertGreater(len(engine.event_log.filter_by_type(EventType.RREQ_SENT)), 0)
        self.assertGreater(len(engine.event_log.filter_by_type(EventType.RREQ_RECEIVED)), 0)
        self.assertGreater(len(engine.event_log.filter_by_type(EventType.RREP_SENT)), 0)
        self.assertGreater(len(engine.event_log.filter_by_type(EventType.RREP_RECEIVED)), 0)

    def test_routing_table_populated_correctly_after_discovery(self):
        reset_packet_id_counter()
        net = line_network()
        engine = SimulationEngine(net)
        router = AODVRouter(net, engine)

        engine.generate_packet(0.0, "N1", "N4", PacketType.NORMAL, ttl=10)
        engine.run()

        n1_route = router.routing_tables["N1"]["N4"]
        self.assertEqual(n1_route.next_hop, "N2")
        self.assertTrue(n1_route.valid)
        self.assertEqual(n1_route.hop_count, 3)

        n2_route = router.routing_tables["N2"]["N4"]
        self.assertEqual(n2_route.next_hop, "N3")
        self.assertEqual(n2_route.hop_count, 2)

        n3_route = router.routing_tables["N3"]["N4"]
        self.assertEqual(n3_route.next_hop, "N4")
        self.assertEqual(n3_route.hop_count, 1)

    def test_precursors_recorded_along_the_path(self):
        reset_packet_id_counter()
        net = line_network()
        engine = SimulationEngine(net)
        router = AODVRouter(net, engine)
        engine.generate_packet(0.0, "N1", "N4", PacketType.NORMAL, ttl=10)
        engine.run()

        # N2's route to N4 should list N1 as a precursor (N1 uses N2 as next hop)
        self.assertIn("N1", router.routing_tables["N2"]["N4"].precursors)
        # N3's route to N4 should list N2 as a precursor
        self.assertIn("N2", router.routing_tables["N3"]["N4"].precursors)


class TestAODVDuplicateSuppression(unittest.TestCase):
    def test_destination_only_replies_once_despite_two_arrival_paths(self):
        reset_packet_id_counter()
        net = diamond_network()
        engine = SimulationEngine(net)
        AODVRouter(net, engine)

        engine.generate_packet(0.0, "N1", "N4", PacketType.NORMAL, ttl=10)
        engine.run()

        rrep_sent_at_n4 = [e for e in engine.event_log.filter_by_type(EventType.RREP_SENT)
                           if e.node_id == "N4"]
        self.assertEqual(len(rrep_sent_at_n4), 1)  # not 2, despite arriving via both N2 and N3


class TestAODVRouteMaintenance(unittest.TestCase):
    def test_node_failure_invalidates_dependent_routes(self):
        reset_packet_id_counter()
        net = line_network()
        engine = SimulationEngine(net)
        router = AODVRouter(net, engine)

        engine.generate_packet(0.0, "N1", "N4", PacketType.NORMAL, ttl=10)
        engine.run()
        self.assertTrue(router.routing_tables["N1"]["N4"].valid)

        engine.fail_node_at(engine.clock + 0.01, "N2")
        engine.run()

        # N1's route to N4 goes via N2 -- N2 failing must invalidate it.
        self.assertFalse(router.routing_tables["N1"]["N4"].valid)
        # N2's OWN route to N4 (next_hop=N3) is untouched by N2 itself
        # failing -- failure invalidates routes that use the failed node as
        # their next hop, not the failed node's own internal table state.
        self.assertTrue(router.routing_tables["N2"]["N4"].valid)

    def test_new_discovery_attempted_after_route_break(self):
        reset_packet_id_counter()
        net = line_network()
        engine = SimulationEngine(net)
        router = AODVRouter(net, engine)

        engine.generate_packet(0.0, "N1", "N4", PacketType.NORMAL, ttl=10)
        engine.run()
        first_rreq_count = router.rreq_counters["N1"]
        self.assertEqual(first_rreq_count, 1)

        engine.fail_node_at(engine.clock + 0.01, "N2")
        engine.run()

        p2 = engine.generate_packet(engine.clock, "N1", "N4", PacketType.NORMAL, ttl=10)
        engine.run()

        # N2 is failed and is the only intermediate on this line topology, so
        # N1 cannot reach N4 -- the packet should end up buffered, and N1
        # should have attempted a fresh route discovery (rreq counter grew).
        self.assertEqual(p2.status.value, "BUFFERED")
        self.assertGreater(router.rreq_counters["N1"], first_rreq_count)

    def test_rerr_cascades_to_precursors(self):
        reset_packet_id_counter()
        net = line_network()
        engine = SimulationEngine(net)
        router = AODVRouter(net, engine)

        engine.generate_packet(0.0, "N1", "N4", PacketType.NORMAL, ttl=10)
        engine.run()

        engine.fail_node_at(engine.clock + 0.01, "N3")
        engine.run()

        # N3 failing breaks N2's route to N4 (next_hop=N3) which should
        # cascade RERR back to N2's precursor N1, invalidating N1's route too.
        self.assertFalse(router.routing_tables["N2"]["N4"].valid)
        self.assertFalse(router.routing_tables["N1"]["N4"].valid)
        rerr_events = engine.event_log.filter_by_type(EventType.RERR_SENT)
        self.assertGreater(len(rerr_events), 0)


class TestAODVIntegration(unittest.TestCase):
    def test_packet_reaches_terminal_state_with_consistent_bookkeeping(self):
        reset_packet_id_counter()
        net = Network.generate_random(num_nodes=20, communication_range_m=350,
                                       width_m=1000, height_m=1000, seed=99)
        engine = SimulationEngine(net)
        AODVRouter(net, engine)
        p = engine.generate_packet(0.0, "N1", "N20", PacketType.NORMAL, ttl=20)
        engine.run()

        self.assertTrue(p.is_terminal or p.status.value == "BUFFERED")
        self.assertEqual(len(p.visited_nodes), len(set(p.visited_nodes)))
        self.assertGreaterEqual(p.ttl, 0)
        self.assertEqual(p.hop_count, len(p.visited_nodes) - 1)
        for e in engine.event_log.filter_by_type(EventType.PACKET_FORWARDED):
            self.assertEqual(e.algorithm, "AODV")

    def test_reproducible_outcome_with_same_seed(self):
        def run_once():
            reset_packet_id_counter()
            net = Network.generate_random(num_nodes=15, communication_range_m=400, seed=7)
            engine = SimulationEngine(net)
            AODVRouter(net, engine)
            p = engine.generate_packet(0.0, "N1", "N15", PacketType.NORMAL, ttl=15)
            engine.run()
            return p.status.value, p.hop_count, tuple(p.visited_nodes)

        self.assertEqual(run_once(), run_once())


if __name__ == "__main__":
    unittest.main(verbosity=2)
