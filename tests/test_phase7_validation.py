"""
Phase 7 tests: ValidationEngine and individual checks.

Run with:
    python -m pytest tests/test_phase7_validation.py -v
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.simulation.network.network import Network
from backend.simulation.engine.engine import SimulationEngine
from backend.simulation.events.event import Event, EventLog, EventType
from backend.simulation.packets.packet import PacketType, reset_packet_id_counter
from backend.simulation.routing.aear import AEARRouter
from backend.simulation.routing.aodv import AODVRouter
from backend.simulation.metrics.engine import MetricsEngine
from backend.scenarios.config import ScenarioConfig
from backend.experiments.runner import ExperimentRunner
from backend.validation.engine import ValidationEngine
from backend.validation.issues import Severity
from backend.validation import checks


def line_network(range_m=150):
    net = Network(communication_range_m=range_m, seed=1)
    net.add_node("N1", 0, 0)
    net.add_node("N2", 100, 0)
    net.add_node("N3", 200, 0)
    net.add_node("N4", 300, 0)
    return net


class TestCleanRunsProduceNoErrors(unittest.TestCase):
    def test_real_aear_run_is_valid(self):
        reset_packet_id_counter()
        net = line_network()
        engine = SimulationEngine(net)
        AEARRouter(net, engine)
        engine.generate_packet(0.0, "N1", "N4", PacketType.NORMAL, ttl=10)
        engine.run()

        metrics = MetricsEngine.compute(engine.event_log, simulation_duration_s=10.0)
        report = ValidationEngine.validate(engine.event_log, metrics=metrics, expected_duration_s=10.0)
        self.assertTrue(report.is_valid)
        self.assertEqual(report.errors, [])

    def test_real_aodv_run_is_valid(self):
        reset_packet_id_counter()
        net = line_network()
        engine = SimulationEngine(net)
        AODVRouter(net, engine)
        engine.generate_packet(0.0, "N1", "N4", PacketType.NORMAL, ttl=10)
        engine.run()

        metrics = MetricsEngine.compute(engine.event_log, simulation_duration_s=10.0)
        report = ValidationEngine.validate(engine.event_log, metrics=metrics, expected_duration_s=10.0)
        self.assertTrue(report.is_valid)

    def test_full_experiment_runner_produces_valid_reports_for_every_run(self):
        scenario = ScenarioConfig(num_nodes=15, simulation_duration_s=30, traffic_rate_pps=1.0,
                                  num_runs=3, algorithms=["AEAR", "RSSI", "AODV"])
        result = ExperimentRunner(scenario).run(save_raw_events=False)
        for run in result.runs:
            self.assertTrue(run.validation_report.is_valid,
                            f"{run.algorithm} run {run.run_number} had errors: "
                            f"{[i.message for i in run.validation_report.errors]}")


class TestMetricsLevelChecks(unittest.TestCase):
    def test_pdr_out_of_range_detected(self):
        from backend.simulation.metrics.engine import MetricsResult
        m = MetricsResult(
            generated_packets=1, delivered_packets=1, pdr_percent=101.0,
            avg_end_to_end_delay_s=None, avg_hop_count=None, packet_loss=0,
            packet_loss_rate_percent=0.0, throughput_packets_per_s=None,
            emergency_packets_generated=0, emergency_packets_delivered=0,
            emergency_pdr_percent=None, avg_emergency_delay_s=None,
            pdr_by_priority={}, avg_delay_by_priority={},
            routing_overhead_control_messages=0, recovery_events_count=0,
            avg_recovery_time_s=None, duplicate_forward_count=0,
            duplicate_packet_rate_percent=0.0, queue_overflow_count=0,
            avg_queue_length=None, max_queue_length=None, avg_queue_utilization_percent=None,
            forwarding_decisions=0, successful_forwarding_attempts=0,
            failed_forwarding_attempts=0, retransmissions=0,
            packets_buffered_events=0, unique_packets_buffered=0,
            successfully_recovered_buffered_packets=0,
        )
        issues = checks.check_pdr_bounds(m)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "PDR_OUT_OF_RANGE")
        self.assertEqual(issues[0].severity, Severity.ERROR)

    def test_throughput_mismatch_detected(self):
        reset_packet_id_counter()
        net = line_network()
        engine = SimulationEngine(net)
        AEARRouter(net, engine)
        engine.generate_packet(0.0, "N1", "N4", PacketType.NORMAL, ttl=10)
        engine.run()
        metrics = MetricsEngine.compute(engine.event_log, simulation_duration_s=10.0)

        # deliberately corrupt the computed throughput
        import dataclasses
        corrupted = dataclasses.replace(metrics, throughput_packets_per_s=999.0)
        issues = checks.check_throughput_consistency(corrupted, expected_duration_s=10.0)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "THROUGHPUT_FORMULA_MISMATCH")


class TestEventLogAudits(unittest.TestCase):
    """These construct deliberately corrupted synthetic event logs to prove
    each audit actually fires -- the real simulation should never produce
    these, which is exactly why we have to fake it to test the check."""

    def test_failed_node_forwarding_detected(self):
        log = EventLog()
        log.append(Event(log.next_event_id(), 1.0, EventType.NODE_FAILED, node_id="N2"))
        log.append(Event(log.next_event_id(), 2.0, EventType.PACKET_FORWARDED,
                         node_id="N2", packet_id="P001", metadata={"to_node": "N3"}))
        issues = checks.check_failed_nodes_never_forwarded(log)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "FAILED_NODE_FORWARDED")

    def test_forwarding_before_failure_is_fine(self):
        log = EventLog()
        log.append(Event(log.next_event_id(), 1.0, EventType.PACKET_FORWARDED,
                         node_id="N2", packet_id="P001", metadata={"to_node": "N3"}))
        log.append(Event(log.next_event_id(), 2.0, EventType.NODE_FAILED, node_id="N2"))
        issues = checks.check_failed_nodes_never_forwarded(log)
        self.assertEqual(issues, [])

    def test_forwarding_after_recovery_is_fine(self):
        log = EventLog()
        log.append(Event(log.next_event_id(), 1.0, EventType.NODE_FAILED, node_id="N2"))
        log.append(Event(log.next_event_id(), 2.0, EventType.NODE_RECOVERED, node_id="N2"))
        log.append(Event(log.next_event_id(), 3.0, EventType.PACKET_FORWARDED,
                         node_id="N2", packet_id="P001", metadata={"to_node": "N3"}))
        issues = checks.check_failed_nodes_never_forwarded(log)
        self.assertEqual(issues, [])

    def test_failed_link_used_detected(self):
        log = EventLog()
        log.append(Event(log.next_event_id(), 1.0, EventType.LINK_FAILED,
                         metadata={"node_a": "N2", "node_b": "N3"}))
        log.append(Event(log.next_event_id(), 2.0, EventType.PACKET_FORWARDED,
                         node_id="N2", packet_id="P001", metadata={"to_node": "N3"}))
        issues = checks.check_failed_links_never_used(log)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "FAILED_LINK_USED")

    def test_node_revisited_detected(self):
        log = EventLog()
        log.append(Event(log.next_event_id(), 1.0, EventType.PACKET_FORWARDED,
                         node_id="N1", packet_id="P001", metadata={"to_node": "N2"}))
        log.append(Event(log.next_event_id(), 2.0, EventType.PACKET_FORWARDED,
                         node_id="N2", packet_id="P001", metadata={"to_node": "N1"}))
        issues = checks.check_no_revisited_nodes(log)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "NODE_REVISITED")

    def test_clean_path_not_flagged_as_revisited(self):
        log = EventLog()
        log.append(Event(log.next_event_id(), 1.0, EventType.PACKET_FORWARDED,
                         node_id="N1", packet_id="P001", metadata={"to_node": "N2"}))
        log.append(Event(log.next_event_id(), 2.0, EventType.PACKET_FORWARDED,
                         node_id="N2", packet_id="P001", metadata={"to_node": "N3"}))
        issues = checks.check_no_revisited_nodes(log)
        self.assertEqual(issues, [])

    def test_negative_ttl_detected(self):
        log = EventLog()
        log.append(Event(log.next_event_id(), 1.0, EventType.PACKET_FORWARDED,
                         node_id="N1", packet_id="P001", metadata={"to_node": "N2", "ttl_remaining": -1}))
        issues = checks.check_ttl_never_negative(log)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "NEGATIVE_TTL")

    def test_aear_bad_weights_detected(self):
        log = EventLog()
        log.append(Event(log.next_event_id(), 1.0, EventType.CANDIDATE_EVALUATED,
                         node_id="N1", packet_id="P001", algorithm="AEAR",
                         metadata={"weights": {"signal": 0.5, "hop": 0.5, "congestion": 0.5}}))
        issues = checks.check_aear_weights_sum_to_one(log)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "AEAR_WEIGHTS_NOT_NORMALIZED")

    def test_aear_good_weights_pass(self):
        log = EventLog()
        log.append(Event(log.next_event_id(), 1.0, EventType.CANDIDATE_EVALUATED,
                         node_id="N1", packet_id="P001", algorithm="AEAR",
                         metadata={"weights": {"signal": 0.4, "hop": 0.3, "congestion": 0.3}}))
        issues = checks.check_aear_weights_sum_to_one(log)
        self.assertEqual(issues, [])

    def test_unresolved_failure_flagged_as_warning_not_error(self):
        log = EventLog()
        log.append(Event(log.next_event_id(), 1.0, EventType.NODE_FAILED, node_id="N2"))
        issues = checks.check_no_completed_recovery_cycles(log)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, Severity.WARNING)

    def test_resolved_failure_not_flagged(self):
        log = EventLog()
        log.append(Event(log.next_event_id(), 1.0, EventType.NODE_FAILED, node_id="N2"))
        log.append(Event(log.next_event_id(), 2.0, EventType.NODE_RECOVERED, node_id="N2"))
        issues = checks.check_no_completed_recovery_cycles(log)
        self.assertEqual(issues, [])


class TestValidationReport(unittest.TestCase):
    def test_is_valid_false_when_any_error_present(self):
        log = EventLog()
        log.append(Event(log.next_event_id(), 1.0, EventType.NODE_FAILED, node_id="N2"))
        log.append(Event(log.next_event_id(), 2.0, EventType.PACKET_FORWARDED,
                         node_id="N2", packet_id="P001", metadata={"to_node": "N3"}))
        report = ValidationEngine.validate(log)
        self.assertFalse(report.is_valid)
        self.assertEqual(len(report.errors), 1)

    def test_is_valid_true_with_only_warnings(self):
        log = EventLog()
        log.append(Event(log.next_event_id(), 1.0, EventType.NODE_FAILED, node_id="N2"))
        report = ValidationEngine.validate(log)
        self.assertTrue(report.is_valid)  # unresolved failure is a WARNING only
        self.assertEqual(len(report.warnings), 1)

    def test_empty_log_is_valid(self):
        report = ValidationEngine.validate(EventLog())
        self.assertTrue(report.is_valid)
        self.assertEqual(report.summary(), "No issues found.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
