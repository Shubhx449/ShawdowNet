"""
Phase 5 tests: MetricsEngine + QueueSampler.

Run with:
    python -m pytest tests/test_phase5_metrics.py -v
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.simulation.network.network import Network
from backend.simulation.engine.engine import SimulationEngine
from backend.simulation.events.event import EventType
from backend.simulation.packets.packet import PacketType, PacketStatus, reset_packet_id_counter
from backend.simulation.routing.aear import AEARRouter
from backend.simulation.routing.rssi import RSSIRouter
from backend.simulation.routing.aodv import AODVRouter
from backend.simulation.failures.recovery import FailureRecoveryManager
from backend.simulation.failures.schedule import apply_failure_schedule, node_failure_scenario
from backend.simulation.metrics.engine import MetricsEngine, MetricsIntegrityError
from backend.simulation.metrics.sampler import QueueSampler


def line_network(range_m=150):
    net = Network(communication_range_m=range_m, seed=1)
    net.add_node("N1", 0, 0)
    net.add_node("N2", 100, 0)
    net.add_node("N3", 200, 0)
    net.add_node("N4", 300, 0)
    return net


class TestBasicDeliveryMetrics(unittest.TestCase):
    def test_all_delivered_gives_100_percent_pdr(self):
        reset_packet_id_counter()
        net = line_network()
        engine = SimulationEngine(net)
        AEARRouter(net, engine)
        for _ in range(3):
            engine.generate_packet(engine.clock, "N1", "N4", PacketType.NORMAL, ttl=10)
            engine.run()

        m = MetricsEngine.compute(engine.event_log)
        self.assertEqual(m.generated_packets, 3)
        self.assertEqual(m.delivered_packets, 3)
        self.assertEqual(m.pdr_percent, 100.0)
        self.assertEqual(m.packet_loss, 0)
        self.assertEqual(m.packet_loss_rate_percent, 0.0)
        self.assertEqual(m.avg_hop_count, 3.0)  # line topology, 3 hops every time
        self.assertIsNotNone(m.avg_end_to_end_delay_s)
        self.assertGreater(m.avg_end_to_end_delay_s, 0)

    def test_partial_delivery_pdr_matches_manual_calculation(self):
        reset_packet_id_counter()
        net = line_network()
        engine = SimulationEngine(net)
        AEARRouter(net, engine)

        # deliver 2, then permanently fail N2 so a 3rd packet cannot be delivered
        engine.generate_packet(0.0, "N1", "N4", PacketType.NORMAL, ttl=10)
        engine.run()
        engine.generate_packet(engine.clock, "N1", "N4", PacketType.NORMAL, ttl=10)
        engine.run()
        net.fail_node("N2")
        p3 = engine.generate_packet(engine.clock, "N1", "N4", PacketType.NORMAL, ttl=10)
        engine.run()
        self.assertFalse(p3.is_delivered)

        m = MetricsEngine.compute(engine.event_log)
        self.assertEqual(m.generated_packets, 3)
        self.assertEqual(m.delivered_packets, 2)
        self.assertAlmostEqual(m.pdr_percent, 2 / 3 * 100)
        self.assertEqual(m.packet_loss, 1)

    def test_no_packets_generated_gives_zero_not_a_crash(self):
        net = line_network()
        engine = SimulationEngine(net)
        m = MetricsEngine.compute(engine.event_log)
        self.assertEqual(m.generated_packets, 0)
        self.assertEqual(m.pdr_percent, 0.0)
        self.assertIsNone(m.avg_end_to_end_delay_s)


class TestPriorityBreakdown(unittest.TestCase):
    def test_emergency_pdr_and_delay_isolated_from_normal_traffic(self):
        reset_packet_id_counter()
        net = line_network()
        engine = SimulationEngine(net)
        AEARRouter(net, engine)

        engine.generate_packet(0.0, "N1", "N4", PacketType.EMERGENCY, ttl=10)
        engine.run()
        engine.generate_packet(engine.clock, "N1", "N4", PacketType.NORMAL, ttl=10)
        engine.run()

        m = MetricsEngine.compute(engine.event_log)
        self.assertEqual(m.emergency_packets_generated, 1)
        self.assertEqual(m.emergency_packets_delivered, 1)
        self.assertEqual(m.emergency_pdr_percent, 100.0)
        self.assertIsNotNone(m.avg_emergency_delay_s)
        self.assertEqual(m.pdr_by_priority["EMERGENCY"], 100.0)
        self.assertEqual(m.pdr_by_priority["NORMAL"], 100.0)
        self.assertIsNone(m.pdr_by_priority["IMPORTANT"])  # none generated

    def test_no_emergency_traffic_gives_none_not_zero(self):
        reset_packet_id_counter()
        net = line_network()
        engine = SimulationEngine(net)
        AEARRouter(net, engine)
        engine.generate_packet(0.0, "N1", "N4", PacketType.NORMAL, ttl=10)
        engine.run()

        m = MetricsEngine.compute(engine.event_log)
        self.assertEqual(m.emergency_packets_generated, 0)
        self.assertIsNone(m.emergency_pdr_percent)  # None, not 0 -- no traffic, not 0% delivery


class TestRoutingOverhead(unittest.TestCase):
    def test_aear_and_rssi_have_zero_control_plane_overhead(self):
        for router_cls in (AEARRouter, RSSIRouter):
            reset_packet_id_counter()
            net = line_network()
            engine = SimulationEngine(net)
            router_cls(net, engine)
            engine.generate_packet(0.0, "N1", "N4", PacketType.NORMAL, ttl=10)
            engine.run()
            m = MetricsEngine.compute(engine.event_log)
            self.assertEqual(m.routing_overhead_control_messages, 0)

    def test_aodv_has_nonzero_control_plane_overhead(self):
        reset_packet_id_counter()
        net = line_network()
        engine = SimulationEngine(net)
        AODVRouter(net, engine)
        engine.generate_packet(0.0, "N1", "N4", PacketType.NORMAL, ttl=10)
        engine.run()
        m = MetricsEngine.compute(engine.event_log)
        self.assertGreater(m.routing_overhead_control_messages, 0)


class TestRecoveryTime(unittest.TestCase):
    def test_recovery_time_matches_scheduled_outage_duration(self):
        net = line_network()
        engine = SimulationEngine(net)
        apply_failure_schedule(engine, node_failure_scenario("N2", fail_at=1.0, restore_at=4.0))
        engine.run()
        m = MetricsEngine.compute(engine.event_log)
        self.assertEqual(m.recovery_events_count, 1)
        self.assertAlmostEqual(m.avg_recovery_time_s, 3.0)

    def test_multiple_failure_cycles_paired_correctly(self):
        net = line_network()
        engine = SimulationEngine(net)
        events = (node_failure_scenario("N2", fail_at=1.0, restore_at=2.0)
                  + node_failure_scenario("N2", fail_at=5.0, restore_at=8.0))
        apply_failure_schedule(engine, events)
        engine.run()
        m = MetricsEngine.compute(engine.event_log)
        self.assertEqual(m.recovery_events_count, 2)
        self.assertAlmostEqual(m.avg_recovery_time_s, (1.0 + 3.0) / 2)

    def test_no_failures_gives_none(self):
        net = line_network()
        engine = SimulationEngine(net)
        m = MetricsEngine.compute(engine.event_log)
        self.assertEqual(m.recovery_events_count, 0)
        self.assertIsNone(m.avg_recovery_time_s)
        self.assertIn("recovery_time", m.notes)


class TestHonestZeroMetrics(unittest.TestCase):
    """These must report 0 (not crash, not fabricate a plausible-looking
    number) given what the current routing engines actually model."""

    def test_no_retransmissions_with_explanatory_note(self):
        reset_packet_id_counter()
        net = line_network()
        engine = SimulationEngine(net)
        AEARRouter(net, engine)
        engine.generate_packet(0.0, "N1", "N4", PacketType.NORMAL, ttl=10)
        engine.run()
        m = MetricsEngine.compute(engine.event_log)
        self.assertEqual(m.retransmissions, 0)
        self.assertEqual(m.failed_forwarding_attempts, 0)
        self.assertIn("retransmissions", m.notes)

    def test_no_duplicate_forwards_with_explanatory_note(self):
        reset_packet_id_counter()
        net = line_network()
        engine = SimulationEngine(net)
        AEARRouter(net, engine)
        engine.generate_packet(0.0, "N1", "N4", PacketType.NORMAL, ttl=10)
        engine.run()
        m = MetricsEngine.compute(engine.event_log)
        self.assertEqual(m.duplicate_forward_count, 0)
        self.assertEqual(m.duplicate_packet_rate_percent, 0.0)
        self.assertIn("duplicate_packet_rate", m.notes)


class TestBufferingMetrics(unittest.TestCase):
    def test_successfully_recovered_buffered_packet_counted(self):
        reset_packet_id_counter()
        net = line_network()
        engine = SimulationEngine(net)
        router = AEARRouter(net, engine)
        FailureRecoveryManager(engine, [router])

        net.fail_node("N2")
        p = engine.generate_packet(0.0, "N1", "N4", PacketType.NORMAL, ttl=10)
        engine.run()
        engine.restore_node_at(engine.clock + 0.01, "N2")
        engine.run()
        self.assertTrue(p.is_delivered)

        m = MetricsEngine.compute(engine.event_log)
        self.assertGreaterEqual(m.packets_buffered_events, 1)
        self.assertEqual(m.unique_packets_buffered, 1)
        self.assertEqual(m.successfully_recovered_buffered_packets, 1)


class TestQueueSampler(unittest.TestCase):
    def test_without_sampler_queue_length_metrics_are_none_with_note(self):
        reset_packet_id_counter()
        net = line_network()
        engine = SimulationEngine(net)
        AEARRouter(net, engine)
        engine.generate_packet(0.0, "N1", "N4", PacketType.NORMAL, ttl=10)
        engine.run()
        m = MetricsEngine.compute(engine.event_log)
        self.assertIsNone(m.avg_queue_length)
        self.assertIsNone(m.max_queue_length)
        self.assertIn("queue_length_metrics", m.notes)

    def test_with_sampler_queue_length_metrics_are_populated(self):
        reset_packet_id_counter()
        net = line_network()
        engine = SimulationEngine(net)
        AEARRouter(net, engine)
        QueueSampler(engine, net, interval_s=0.5, end_time=5.0)
        engine.generate_packet(0.0, "N1", "N4", PacketType.NORMAL, ttl=10)
        engine.run(until=5.0)

        m = MetricsEngine.compute(engine.event_log)
        self.assertIsNotNone(m.avg_queue_length)
        self.assertIsNotNone(m.max_queue_length)
        self.assertIsNotNone(m.avg_queue_utilization_percent)
        self.assertNotIn("queue_length_metrics", m.notes)

    def test_sampler_requires_positive_interval_and_end_time(self):
        net = line_network()
        engine = SimulationEngine(net)
        with self.assertRaises(ValueError):
            QueueSampler(engine, net, interval_s=0.0, end_time=5.0)
        with self.assertRaises(ValueError):
            QueueSampler(engine, net, interval_s=1.0, end_time=0.0)

    def test_sampler_run_terminates_and_stays_bounded(self):
        """The sampler reschedules itself -- verify it actually stops at
        end_time rather than looping forever (this would hang the test
        suite if broken, which is itself the point of the assertion)."""
        net = line_network()
        engine = SimulationEngine(net)
        QueueSampler(engine, net, interval_s=1.0, end_time=3.0)
        processed = engine.run()
        self.assertGreater(processed, 0)
        self.assertTrue(engine.scheduler.is_empty())


class TestMetricsIntegrityChecks(unittest.TestCase):
    def test_validate_catches_impossible_pdr(self):
        from backend.simulation.metrics.engine import MetricsResult
        bad = MetricsResult(
            generated_packets=1, delivered_packets=1, pdr_percent=150.0,
            avg_end_to_end_delay_s=None, avg_hop_count=None, packet_loss=0,
            packet_loss_rate_percent=0.0, throughput_packets_per_s=None,
            emergency_packets_generated=0,
            emergency_packets_delivered=0, emergency_pdr_percent=None,
            avg_emergency_delay_s=None, pdr_by_priority={}, avg_delay_by_priority={},
            routing_overhead_control_messages=0, recovery_events_count=0,
            avg_recovery_time_s=None, duplicate_forward_count=0,
            duplicate_packet_rate_percent=0.0, queue_overflow_count=0,
            avg_queue_length=None, max_queue_length=None, avg_queue_utilization_percent=None,
            forwarding_decisions=0, successful_forwarding_attempts=0,
            failed_forwarding_attempts=0, retransmissions=0,
            packets_buffered_events=0, unique_packets_buffered=0,
            successfully_recovered_buffered_packets=0,
        )
        with self.assertRaises(MetricsIntegrityError):
            MetricsEngine._validate(bad)


class TestFullPipelineIntegration(unittest.TestCase):
    def test_metrics_computed_end_to_end_for_all_three_algorithms(self):
        for router_cls in (AEARRouter, RSSIRouter, AODVRouter):
            reset_packet_id_counter()
            net = Network.generate_random(num_nodes=20, communication_range_m=350, seed=99)
            engine = SimulationEngine(net)
            router_cls(net, engine)
            QueueSampler(engine, net, interval_s=2.0, end_time=50.0)
            for _ in range(5):
                engine.generate_packet(engine.clock, "N1", "N20", PacketType.NORMAL, ttl=20)
                engine.run(until=engine.clock + 1.0)
            engine.run(until=50.0)

            m = MetricsEngine.compute(engine.event_log)
            self.assertEqual(m.generated_packets, 5)
            self.assertGreaterEqual(m.delivered_packets, 0)
            self.assertLessEqual(m.delivered_packets, m.generated_packets)
            # to_dict must not raise and must be JSON-shaped (plain dict)
            d = m.to_dict()
            self.assertIsInstance(d, dict)
            self.assertIn("pdr_percent", d)


if __name__ == "__main__":
    unittest.main(verbosity=2)
