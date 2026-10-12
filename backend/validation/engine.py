"""
ValidationEngine.

Runs every check in checks.py against a completed run's EventLog (and,
where relevant, its MetricsResult) and merges the results into one
ValidationReport. This is meant to be called once per run -- Phase 6's
ExperimentRunner attaches a report to each RunResult automatically.
"""

from __future__ import annotations

from typing import Optional

from backend.simulation.events.event import EventLog
from backend.simulation.metrics.engine import MetricsResult
from backend.validation import checks
from backend.validation.issues import ValidationReport


class ValidationEngine:
    @staticmethod
    def validate(event_log: EventLog, metrics: Optional[MetricsResult] = None,
                 expected_duration_s: Optional[float] = None) -> ValidationReport:
        report = ValidationReport()

        if metrics is not None:
            report.extend(checks.check_pdr_bounds(metrics))
            report.extend(checks.check_delivered_le_generated(metrics))
            report.extend(checks.check_packet_loss_formula(metrics))
            report.extend(checks.check_avg_delay_nonnegative(metrics))
            report.extend(checks.check_avg_hop_count_nonnegative(metrics))
            report.extend(checks.check_throughput_consistency(metrics, expected_duration_s))

        report.extend(checks.check_failed_nodes_never_forwarded(event_log))
        report.extend(checks.check_failed_links_never_used(event_log))
        report.extend(checks.check_no_revisited_nodes(event_log))
        report.extend(checks.check_ttl_never_negative(event_log))
        report.extend(checks.check_aear_weights_sum_to_one(event_log))
        report.extend(checks.check_no_completed_recovery_cycles(event_log))

        return report
