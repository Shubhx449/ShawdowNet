"""
Phase 2 tests: EventScheduler, EventLog, SimulationEngine.

Run with:
    python -m pytest tests/test_phase2_engine.py -v
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.simulation.events.scheduler import EventScheduler
from backend.simulation.events.event import EventLog, EventType, Event
from backend.simulation.engine.engine import SimulationEngine
from backend.simulation.network.network import Network
from backend.simulation.packets.packet import PacketType, PacketStatus, reset_packet_id_counter


class TestEventScheduler(unittest.TestCase):
    def test_pops_in_time_order_regardless_of_insertion_order(self):
        s = EventScheduler()
        s.schedule(5.0, "fifth")
        s.schedule(1.0, "first")
        s.schedule(3.0, "third")
        times_and_payloads = [s.pop_next() for _ in range(3)]
        self.assertEqual([p for _, p in times_and_payloads], ["first", "third", "fifth"])

    def test_fifo_tie_break_for_equal_times(self):
        s = EventScheduler()
        s.schedule(2.0, "a")
        s.schedule(2.0, "b")
        s.schedule(2.0, "c")
        payloads = [s.pop_next()[1] for _ in range(3)]
        self.assertEqual(payloads, ["a", "b", "c"])

    def test_empty_scheduler_returns_none(self):
        s = EventScheduler()
        self.assertIsNone(s.pop_next())
        self.assertIsNone(s.peek_next_time())
        self.assertTrue(s.is_empty())

    def test_peek_does_not_remove(self):
        s = EventScheduler()
        s.schedule(1.0, "x")
        self.assertEqual(s.peek_next_time(), 1.0)
        self.assertEqual(len(s), 1)


class TestEventLog(unittest.TestCase):
    def test_ids_are_sequential_and_independent_per_log(self):
        log = EventLog()
        e1 = Event(event_id=log.next_event_id(), timestamp=0, event_type=EventType.NODE_FAILED)
        e2 = Event(event_id=log.next_event_id(), timestamp=1, event_type=EventType.NODE_RECOVERED)
        self.assertEqual((e1.event_id, e2.event_id), (1, 2))

    def test_filters(self):
        log = EventLog()
        log.append(Event(1, 0.0, EventType.PACKET_GENERATED, packet_id="P001", node_id="N1"))
        log.append(Event(2, 1.0, EventType.PACKET_FORWARDED, packet_id="P001", node_id="N1"))
        log.append(Event(3, 1.0, EventType.NODE_FAILED, node_id="N2"))
        self.assertEqual(len(log.filter_by_packet("P001")), 2)
        self.assertEqual(len(log.filter_by_node("N2")), 1)
        self.assertEqual(len(log.filter_by_type(EventType.NODE_FAILED)), 1)
        counts = log.count_by_type()
        self.assertEqual(counts["PACKET_GENERATED"], 1)

    def test_clear_resets_ids(self):
        log = EventLog()
        log.append(Event(log.next_event_id(), 0.0, EventType.NODE_FAILED))
        log.clear()
        self.assertEqual(log.count(), 0)
        new_id = log.next_event_id()
        self.assertEqual(new_id, 1)


class TestSimulationEngineCore(unittest.TestCase):
    def setUp(self):
        self.net = Network(communication_range_m=200, seed=1)
        self.net.add_node("N1", 0, 0)
        self.net.add_node("N2", 100, 0)
        self.net.add_node("N3", 200, 0)
        self.engine = SimulationEngine(self.net)

    def test_clock_advances_in_event_time_order(self):
        self.engine.schedule(5.0, EventType.NODE_FAILED, node_id="N1")
        self.engine.schedule(1.0, EventType.NODE_RECOVERED, node_id="N1")
        self.engine.step()
        self.assertEqual(self.engine.clock, 1.0)
        self.engine.step()
        self.assertEqual(self.engine.clock, 5.0)

    def test_cannot_schedule_before_current_clock(self):
        self.engine.schedule(5.0, EventType.NODE_FAILED, node_id="N1")
        self.engine.step()
        with self.assertRaises(ValueError):
            self.engine.schedule(2.0, EventType.NODE_FAILED, node_id="N2")

    def test_run_respects_until(self):
        for t in [1, 2, 3, 4, 5]:
            self.engine.schedule(float(t), EventType.NODE_FAILED, node_id="N1")
        processed = self.engine.run(until=3.0)
        self.assertEqual(processed, 3)
        self.assertEqual(self.engine.clock, 3.0)

    def test_run_respects_max_events(self):
        for t in [1, 2, 3]:
            self.engine.schedule(float(t), EventType.NODE_FAILED, node_id="N1")
        processed = self.engine.run(max_events=2)
        self.assertEqual(processed, 2)

    def test_node_failed_event_actually_fails_node_in_network(self):
        self.engine.fail_node_at(1.0, "N2")
        self.engine.run()
        self.assertTrue(self.net.get_node("N2").is_failed)

    def test_node_recovered_event_restores_node(self):
        self.engine.fail_node_at(1.0, "N2")
        self.engine.restore_node_at(2.0, "N2")
        self.engine.run()
        self.assertFalse(self.net.get_node("N2").is_failed)

    def test_link_failed_and_restored(self):
        self.engine.fail_link_at(1.0, "N1", "N2")
        self.engine.run(until=1.0)
        self.assertFalse(self.net.get_link("N1", "N2").is_active)
        self.engine.restore_link_at(2.0, "N1", "N2")
        self.engine.run()
        self.assertTrue(self.net.get_link("N1", "N2").is_active)

    def test_handler_is_dispatched_on_matching_event(self):
        seen = []
        self.engine.register_handler(
            EventType.NODE_FAILED, lambda event, eng: seen.append(event.node_id)
        )
        self.engine.fail_node_at(1.0, "N2")
        self.engine.run()
        self.assertEqual(seen, ["N2"])

    def test_handler_not_called_for_other_event_types(self):
        seen = []
        self.engine.register_handler(
            EventType.NODE_RECOVERED, lambda event, eng: seen.append(event.node_id)
        )
        self.engine.fail_node_at(1.0, "N2")
        self.engine.run()
        self.assertEqual(seen, [])

    def test_reset_clears_state_and_packet_ids(self):
        reset_packet_id_counter()
        self.engine.generate_packet(0.0, "N1", "N3", PacketType.NORMAL)
        self.engine.run()
        self.assertEqual(len(self.engine.packets), 1)
        self.engine.reset(network=self.net)
        self.assertEqual(len(self.engine.packets), 0)
        self.assertEqual(self.engine.clock, 0.0)
        self.assertEqual(self.engine.event_log.count(), 0)
        p = self.engine.generate_packet(0.0, "N1", "N3", PacketType.NORMAL)
        self.assertEqual(p.packet_id, "P001")  # counter reset


class TestPacketFlowThroughEngine(unittest.TestCase):
    def setUp(self):
        reset_packet_id_counter()
        self.net = Network(communication_range_m=200, seed=1)
        self.net.add_node("N1", 0, 0)
        self.net.add_node("N2", 100, 0)
        self.net.add_node("N3", 200, 0)
        self.engine = SimulationEngine(self.net)

    def test_generate_packet_creates_packet_and_event(self):
        p = self.engine.generate_packet(0.0, "N1", "N3", PacketType.EMERGENCY, ttl=5)
        self.engine.run(max_events=1)
        events = self.engine.event_log.filter_by_type(EventType.PACKET_GENERATED)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].packet_id, p.packet_id)
        self.assertEqual(events[0].node_id, "N1")

    def test_forward_packet_logs_forwarded_and_schedules_received(self):
        p = self.engine.generate_packet(0.0, "N1", "N3", PacketType.NORMAL, ttl=5)
        self.engine.run(max_events=1)  # process PACKET_GENERATED
        self.engine.forward_packet(p.packet_id, "N2", transmission_delay=0.5)
        forwarded = self.engine.event_log.filter_by_type(EventType.PACKET_FORWARDED)
        self.assertEqual(len(forwarded), 1)
        # a PACKET_RECEIVED should now be pending at clock + 0.5
        self.assertEqual(self.engine.scheduler.peek_next_time(), 0.5)

    def test_forward_to_destination_marks_delivered(self):
        p = self.engine.generate_packet(0.0, "N1", "N2", PacketType.NORMAL, ttl=5)
        self.engine.run(max_events=1)
        self.engine.forward_packet(p.packet_id, "N2")
        self.assertTrue(p.is_delivered)
        delivered_events = self.engine.event_log.filter_by_type(EventType.PACKET_DELIVERED)
        self.assertEqual(len(delivered_events), 1)

    def test_full_multi_hop_journey_via_run(self):
        """Manually drive forwarding N1->N2->N3 using PACKET_RECEIVED as the
        trigger point, proving the engine's scheduling/dispatch loop supports
        a real multi-hop journey without any routing algorithm involved."""
        p = self.engine.generate_packet(0.0, "N1", "N3", PacketType.NORMAL, ttl=5)

        def relay_handler(event, engine: SimulationEngine):
            packet = engine.packets[event.packet_id]
            if packet.current_node == "N1":
                engine.forward_packet(packet.packet_id, "N2", transmission_delay=1.0)
            elif packet.current_node == "N2":
                engine.forward_packet(packet.packet_id, "N3", transmission_delay=1.0)

        self.engine.register_handler(EventType.PACKET_GENERATED, relay_handler)
        self.engine.register_handler(EventType.PACKET_RECEIVED, relay_handler)
        self.engine.run()

        self.assertTrue(p.is_delivered)
        self.assertEqual(p.hop_count, 2)
        self.assertEqual(p.visited_nodes, ["N1", "N2", "N3"])
        delivered = self.engine.event_log.filter_by_type(EventType.PACKET_DELIVERED)
        self.assertEqual(len(delivered), 1)

    def test_drop_packet_ttl_expired(self):
        p = self.engine.generate_packet(0.0, "N1", "N3", PacketType.NORMAL, ttl=1)
        self.engine.run(max_events=1)
        self.engine.forward_packet(p.packet_id, "N2")  # ttl 1 -> 0, not destination
        self.engine.drop_packet(p.packet_id, PacketStatus.DROPPED_TTL_EXPIRED, reason="ttl hit 0")
        self.assertTrue(p.is_dropped)
        dropped_events = self.engine.event_log.filter_by_type(EventType.PACKET_DROPPED)
        self.assertEqual(len(dropped_events), 1)

    def test_buffer_and_unbuffer(self):
        p = self.engine.generate_packet(0.0, "N1", "N3", PacketType.IMPORTANT)
        self.engine.run(max_events=1)
        self.engine.buffer_packet(p.packet_id, reason="No valid forwarding candidate")
        self.assertEqual(p.status.value, "BUFFERED")
        buffered_events = self.engine.event_log.filter_by_type(EventType.PACKET_BUFFERED)
        self.assertEqual(len(buffered_events), 1)
        self.engine.unbuffer_packet(p.packet_id)
        self.assertEqual(p.status.value, "IN_TRANSIT")

    def test_retransmission_and_exhaustion(self):
        p = self.engine.generate_packet(0.0, "N1", "N3", PacketType.NORMAL, max_retransmissions=2)
        self.engine.run(max_events=1)
        can_retry_1 = self.engine.attempt_retransmission(p.packet_id)
        can_retry_2 = self.engine.attempt_retransmission(p.packet_id)
        self.assertTrue(can_retry_1)
        self.assertFalse(can_retry_2)
        events = self.engine.event_log.filter_by_type(EventType.RETRANSMISSION)
        self.assertEqual(len(events), 2)

    def test_queue_overflow_logs_event(self):
        p = self.engine.generate_packet(0.0, "N1", "N3", PacketType.NORMAL)
        node = self.net.get_node("N1")
        node.queue.capacity = 1
        node.queue.enqueue(object())  # fill it artificially with a placeholder
        ok = self.engine.enqueue_or_overflow("N1", p.packet_id)
        self.assertFalse(ok)
        overflow_events = self.engine.event_log.filter_by_type(EventType.QUEUE_OVERFLOW)
        self.assertEqual(len(overflow_events), 1)

    def test_routing_visibility_logging_helpers(self):
        p = self.engine.generate_packet(0.0, "N1", "N3", PacketType.NORMAL)
        self.engine.run(max_events=1)
        self.engine.log_neighbor_discovery("N1", p.packet_id, ["N2"], algorithm="AEAR")
        self.engine.log_candidate_evaluated("N1", p.packet_id, "N2", 0.85, algorithm="AEAR")
        self.engine.log_forwarding_decision("N1", p.packet_id, "N2", algorithm="AEAR")
        self.assertEqual(len(self.engine.event_log.filter_by_type(EventType.NEIGHBOR_DISCOVERED)), 1)
        self.assertEqual(len(self.engine.event_log.filter_by_type(EventType.CANDIDATE_EVALUATED)), 1)
        self.assertEqual(len(self.engine.event_log.filter_by_type(EventType.FORWARDING_DECISION)), 1)
        fd = self.engine.event_log.filter_by_type(EventType.FORWARDING_DECISION)[0]
        self.assertEqual(fd.algorithm, "AEAR")
        self.assertEqual(fd.metadata["selected"], "N2")


if __name__ == "__main__":
    unittest.main(verbosity=2)
