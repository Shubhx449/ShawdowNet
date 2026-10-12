"""
Phase 4 tests: FailureRecoveryManager + FailureSchedule.

Run with:
    python -m pytest tests/test_phase4_failures.py -v
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.simulation.network.network import Network
from backend.simulation.engine.engine import SimulationEngine
from backend.simulation.events.event import EventType
from backend.simulation.packets.packet import PacketType, reset_packet_id_counter
from backend.simulation.routing.aear import AEARRouter
from backend.simulation.routing.rssi import RSSIRouter
from backend.simulation.routing.aodv import AODVRouter
from backend.simulation.failures.recovery import FailureRecoveryManager
from backend.simulation.failures.schedule import (
    FailureEvent, apply_failure_schedule, node_failure_scenario, link_disconnection_scenario,
)


def line_network(range_m=150):
    net = Network(communication_range_m=range_m, seed=1)
    net.add_node("N1", 0, 0)
    net.add_node("N2", 100, 0)
    net.add_node("N3", 200, 0)
    net.add_node("N4", 300, 0)
    return net


class TestFailureSchedule(unittest.TestCase):
    def test_node_failure_scenario_shape(self):
        events = node_failure_scenario("N2", fail_at=1.0, restore_at=5.0)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].action, "fail_node")
        self.assertEqual(events[1].action, "restore_node")

    def test_node_failure_scenario_without_restore(self):
        events = node_failure_scenario("N2", fail_at=1.0)
        self.assertEqual(len(events), 1)

    def test_link_disconnection_scenario_shape(self):
        events = link_disconnection_scenario("N1", "N2", fail_at=1.0, restore_at=3.0)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].target, ("N1", "N2"))

    def test_restore_before_fail_raises(self):
        with self.assertRaises(ValueError):
            node_failure_scenario("N2", fail_at=5.0, restore_at=1.0)
        with self.assertRaises(ValueError):
            link_disconnection_scenario("N1", "N2", fail_at=5.0, restore_at=1.0)

    def test_apply_failure_schedule_actually_mutates_network_on_run(self):
        net = line_network()
        engine = SimulationEngine(net)
        events = node_failure_scenario("N2", fail_at=1.0, restore_at=2.0)
        apply_failure_schedule(engine, events)
        engine.run(until=1.0)
        self.assertTrue(net.get_node("N2").is_failed)
        engine.run()
        self.assertFalse(net.get_node("N2").is_failed)


class TestAEARRecovery(unittest.TestCase):
    def test_buffered_packet_delivered_after_node_recovery(self):
        reset_packet_id_counter()
        net = line_network()
        engine = SimulationEngine(net)
        router = AEARRouter(net, engine)
        FailureRecoveryManager(engine, [router])

        net.fail_node("N2")  # N2 is the only path from N1 onward
        p = engine.generate_packet(0.0, "N1", "N4", PacketType.NORMAL, ttl=10)
        engine.run()
        self.assertEqual(p.status.value, "BUFFERED")

        engine.restore_node_at(engine.clock + 0.01, "N2")
        engine.run()

        self.assertTrue(p.is_delivered)
        self.assertEqual(p.visited_nodes, ["N1", "N2", "N3", "N4"])

    def test_buffered_packet_delivered_after_link_restoration(self):
        reset_packet_id_counter()
        net = line_network()
        engine = SimulationEngine(net)
        router = AEARRouter(net, engine)
        FailureRecoveryManager(engine, [router])

        net.fail_link("N2", "N3")
        p = engine.generate_packet(0.0, "N1", "N4", PacketType.NORMAL, ttl=10)
        engine.run()
        self.assertEqual(p.status.value, "BUFFERED")
        self.assertEqual(p.current_node, "N2")  # got as far as N2, stuck there

        engine.restore_link_at(engine.clock + 0.01, "N2", "N3")
        engine.run()

        self.assertTrue(p.is_delivered)


class TestRSSIRecovery(unittest.TestCase):
    def test_buffered_packet_delivered_after_node_recovery(self):
        reset_packet_id_counter()
        net = line_network()
        engine = SimulationEngine(net)
        router = RSSIRouter(net, engine)
        FailureRecoveryManager(engine, [router])

        net.fail_node("N3")
        p = engine.generate_packet(0.0, "N1", "N4", PacketType.NORMAL, ttl=10)
        engine.run()
        self.assertEqual(p.status.value, "BUFFERED")

        engine.restore_node_at(engine.clock + 0.01, "N3")
        engine.run()
        self.assertTrue(p.is_delivered)


class TestAODVRecovery(unittest.TestCase):
    def test_timed_out_packet_delivered_after_recovery(self):
        """A packet whose route discovery already timed out (fully given up)
        must still be retried once the network recovers."""
        reset_packet_id_counter()
        net = line_network()
        engine = SimulationEngine(net)
        router = AODVRouter(net, engine)
        FailureRecoveryManager(engine, [router])

        net.fail_node("N3")  # only path is broken from the start
        p = engine.generate_packet(0.0, "N1", "N4", PacketType.NORMAL, ttl=10)
        engine.run()  # runs past the route_discovery_timeout -> gives up, stays BUFFERED
        self.assertEqual(p.status.value, "BUFFERED")
        self.assertNotIn(p.packet_id, [pid for pids in router.pending_packets.values() for pid in pids])

        engine.restore_node_at(engine.clock + 0.01, "N3")
        engine.run()
        self.assertTrue(p.is_delivered)

    def test_recovery_does_not_disturb_a_packet_still_mid_discovery(self):
        """
        Safety check: if a NODE_RECOVERED/LINK_RESTORED event fires
        elsewhere in the network WHILE a different packet's AODV route
        discovery is still legitimately in flight (hasn't timed out yet),
        the recovery manager must NOT touch that packet -- touching it would
        create a duplicate `pending_packets` entry and risk double-routing.
        """
        reset_packet_id_counter()
        net = line_network()
        # Add a disconnected fifth node with no path to anything, so its
        # "recovery" is unrelated to the N1->N4 discovery in flight.
        net.add_node("N5", 900, 900)
        engine = SimulationEngine(net)
        router = AODVRouter(net, engine)
        FailureRecoveryManager(engine, [router])

        p = engine.generate_packet(0.0, "N1", "N4", PacketType.NORMAL, ttl=10)
        # Run only far enough for RREQ to be in flight, route discovery NOT
        # yet timed out (discovery timeout default is 2.0s; RREQ propagation
        # is 0.05s/hop, so at t=0.2s we're still well within discovery).
        engine.run(until=0.2)
        key = ("N1", "N4")
        self.assertIn(p.packet_id, router.pending_packets.get(key, []))

        # Trigger an unrelated recovery event (N5 was never failed, but
        # NODE_RECOVERED handling doesn't care -- it just calls
        # retry_buffered_packets(), which is exactly what we're testing stays safe).
        engine.restore_node_at(engine.clock + 0.001, "N5")
        engine.run(until=0.21)

        # The packet must NOT have been duplicated in pending_packets, and
        # must still be legitimately mid-discovery (not force-unbuffered).
        occurrences = router.pending_packets.get(key, []).count(p.packet_id)
        self.assertLessEqual(occurrences, 1)

        # Let the real discovery finish normally.
        engine.run()
        self.assertTrue(p.is_delivered)
        self.assertEqual(p.hop_count, 3)  # delivered exactly once, via the real path


    def test_recovery_and_discovery_timeout_racing_at_the_same_instant(self):
        """
        Regression test for a real race found while building this phase's
        demo: if a link/node recovers at EXACTLY the same simulated instant
        that AODV's route-discovery timeout would otherwise give up, the
        packet must still eventually be delivered -- not stranded because
        the give-up processed a tick "before" the recovery-triggered retry
        got a chance. Fixed via bounded RREQ retries (AODVConfig.max_rreq_retries)
        rather than a one-shot give-up.
        """
        reset_packet_id_counter()
        net = line_network()
        engine = SimulationEngine(net)
        router = AODVRouter(net, engine)
        FailureRecoveryManager(engine, [router])

        net.fail_link("N2", "N3")
        p = engine.generate_packet(0.0, "N1", "N4", PacketType.IMPORTANT, ttl=15)
        # Restore at exactly the discovery timeout boundary (default 2.0s).
        engine.restore_link_at(router.config.route_discovery_timeout_s, "N2", "N3")
        engine.run()

        self.assertTrue(p.is_delivered)
        self.assertEqual(p.visited_nodes, ["N1", "N2", "N3", "N4"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
