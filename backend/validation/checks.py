"""
Individual validation checks.

Each function takes what it needs (an EventLog, sometimes a MetricsResult,
sometimes an expected duration) and returns a list of ValidationIssue --
never raises, never mutates anything. The ValidationEngine (engine.py) runs
all of them and merges the results into one ValidationReport.

Where a check has an obvious severity ("delivered exceeds generated" is
categorically impossible and always an ERROR; "no failure ever recovered"
is informational and a WARNING), that's fixed. A few checks accept a
`severity` override for cases where the caller has more context than this
module does.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from backend.simulation.events.event import EventLog, EventType
from backend.simulation.metrics.engine import MetricsResult
from backend.validation.issues import Severity, ValidationIssue


# -- metrics-level checks -----------------------------------------------------

def check_pdr_bounds(metrics: MetricsResult) -> List[ValidationIssue]:
    if not (0.0 <= metrics.pdr_percent <= 100.0):
        return [ValidationIssue(Severity.ERROR, "PDR_OUT_OF_RANGE",
                                f"PDR {metrics.pdr_percent}% is outside the valid 0-100% range",
                                {"pdr_percent": metrics.pdr_percent})]
    return []


def check_delivered_le_generated(metrics: MetricsResult) -> List[ValidationIssue]:
    if metrics.delivered_packets > metrics.generated_packets:
        return [ValidationIssue(Severity.ERROR, "DELIVERED_EXCEEDS_GENERATED",
                                f"{metrics.delivered_packets} delivered exceeds "
                                f"{metrics.generated_packets} generated",
                                {"delivered": metrics.delivered_packets, "generated": metrics.generated_packets})]
    return []


def check_packet_loss_formula(metrics: MetricsResult) -> List[ValidationIssue]:
    expected = metrics.generated_packets - metrics.delivered_packets
    if metrics.packet_loss != expected:
        return [ValidationIssue(Severity.ERROR, "PACKET_LOSS_FORMULA_MISMATCH",
                                f"packet_loss={metrics.packet_loss} but generated-delivered={expected}",
                                {"packet_loss": metrics.packet_loss, "expected": expected})]
    return []


def check_avg_delay_nonnegative(metrics: MetricsResult) -> List[ValidationIssue]:
    if metrics.avg_end_to_end_delay_s is not None and metrics.avg_end_to_end_delay_s < 0:
        return [ValidationIssue(Severity.ERROR, "NEGATIVE_DELAY",
                                f"avg_end_to_end_delay_s is negative: {metrics.avg_end_to_end_delay_s}",
                                {"avg_end_to_end_delay_s": metrics.avg_end_to_end_delay_s})]
    return []


def check_avg_hop_count_nonnegative(metrics: MetricsResult) -> List[ValidationIssue]:
    if metrics.avg_hop_count is not None and metrics.avg_hop_count < 0:
        return [ValidationIssue(Severity.ERROR, "NEGATIVE_HOP_COUNT",
                                f"avg_hop_count is negative: {metrics.avg_hop_count}",
                                {"avg_hop_count": metrics.avg_hop_count})]
    return []


def check_throughput_consistency(metrics: MetricsResult,
                                  expected_duration_s: Optional[float]) -> List[ValidationIssue]:
    """Throughput must equal delivered_packets / simulation duration."""
    if expected_duration_s is None or expected_duration_s <= 0:
        return []  # nothing to check against
    if metrics.throughput_packets_per_s is None:
        return [ValidationIssue(Severity.WARNING, "THROUGHPUT_NOT_COMPUTED",
                                "A simulation duration is available but MetricsEngine.compute() "
                                "wasn't given one, so throughput_packets_per_s is None",
                                {"expected_duration_s": expected_duration_s})]
    expected = metrics.delivered_packets / expected_duration_s
    if abs(metrics.throughput_packets_per_s - expected) > 1e-9:
        return [ValidationIssue(Severity.ERROR, "THROUGHPUT_FORMULA_MISMATCH",
                                f"throughput {metrics.throughput_packets_per_s} != "
                                f"delivered/duration {expected}",
                                {"throughput": metrics.throughput_packets_per_s, "expected": expected})]
    return []


# -- raw event-log audits (independent of MetricsResult) -----------------------

def _build_intervals(event_log: EventLog, start_type: EventType, end_type: EventType,
                      key_fn) -> Dict[object, List[Tuple[float, float]]]:
    """
    Build [start, end) unavailability intervals per key (node_id, or a link
    key) from paired start/end events, in chronological order. An unmatched
    trailing start (never recovered/restored by the end of the run) gets an
    interval extending to +inf.
    """
    starts = event_log.filter_by_type(start_type)
    ends = event_log.filter_by_type(end_type)
    ends_by_key: Dict[object, List[float]] = defaultdict(list)
    for e in ends:
        ends_by_key[key_fn(e)].append(e.timestamp)
    for lst in ends_by_key.values():
        lst.sort()

    intervals: Dict[object, List[Tuple[float, float]]] = defaultdict(list)
    next_index: Dict[object, int] = defaultdict(int)
    for e in sorted(starts, key=lambda ev: ev.timestamp):
        key = key_fn(e)
        candidates = ends_by_key.get(key, [])
        idx = next_index[key]
        while idx < len(candidates) and candidates[idx] < e.timestamp:
            idx += 1
        if idx < len(candidates):
            intervals[key].append((e.timestamp, candidates[idx]))
            next_index[key] = idx + 1
        else:
            intervals[key].append((e.timestamp, math.inf))
    return intervals


def _in_any_interval(t: float, intervals: List[Tuple[float, float]]) -> bool:
    return any(start <= t < end for start, end in intervals)


def check_failed_nodes_never_forwarded(event_log: EventLog) -> List[ValidationIssue]:
    """A node must never appear as the sender of a PACKET_FORWARDED event
    while it is within a NODE_FAILED..NODE_RECOVERED window."""
    intervals = _build_intervals(event_log, EventType.NODE_FAILED, EventType.NODE_RECOVERED,
                                 key_fn=lambda e: e.node_id)
    issues = []
    for e in event_log.filter_by_type(EventType.PACKET_FORWARDED):
        node_intervals = intervals.get(e.node_id)
        if node_intervals and _in_any_interval(e.timestamp, node_intervals):
            issues.append(ValidationIssue(
                Severity.ERROR, "FAILED_NODE_FORWARDED",
                f"Node {e.node_id} forwarded packet {e.packet_id} at t={e.timestamp} while failed",
                {"node_id": e.node_id, "packet_id": e.packet_id, "timestamp": e.timestamp},
            ))
    return issues


def check_failed_links_never_used(event_log: EventLog) -> List[ValidationIssue]:
    """A (node_a, node_b) link must never carry a forward while it is within
    a LINK_FAILED..LINK_RESTORED window."""
    def link_key(e):
        return tuple(sorted((e.metadata.get("node_a"), e.metadata.get("node_b"))))

    intervals = _build_intervals(event_log, EventType.LINK_FAILED, EventType.LINK_RESTORED,
                                 key_fn=link_key)
    issues = []
    for e in event_log.filter_by_type(EventType.PACKET_FORWARDED):
        to_node = e.metadata.get("to_node")
        if to_node is None:
            continue
        key = tuple(sorted((e.node_id, to_node)))
        link_intervals = intervals.get(key)
        if link_intervals and _in_any_interval(e.timestamp, link_intervals):
            issues.append(ValidationIssue(
                Severity.ERROR, "FAILED_LINK_USED",
                f"Link {key} carried packet {e.packet_id} at t={e.timestamp} while failed",
                {"link": key, "packet_id": e.packet_id, "timestamp": e.timestamp},
            ))
    return issues


def check_no_revisited_nodes(event_log: EventLog) -> List[ValidationIssue]:
    """No packet's visited-node path (reconstructed from PACKET_FORWARDED's
    node_id -> to_node chain) may contain the same node twice."""
    hops_by_packet: Dict[str, List[Tuple[float, str, str]]] = defaultdict(list)
    for e in event_log.filter_by_type(EventType.PACKET_FORWARDED):
        to_node = e.metadata.get("to_node")
        if e.packet_id and to_node:
            hops_by_packet[e.packet_id].append((e.timestamp, e.node_id, to_node))

    issues = []
    for packet_id, hops in hops_by_packet.items():
        hops.sort(key=lambda h: h[0])
        path = [hops[0][1]] + [h[2] for h in hops]
        if len(path) != len(set(path)):
            issues.append(ValidationIssue(
                Severity.ERROR, "NODE_REVISITED",
                f"Packet {packet_id} revisited a node -- path: {' -> '.join(path)}",
                {"packet_id": packet_id, "path": path},
            ))
    return issues


def check_ttl_never_negative(event_log: EventLog) -> List[ValidationIssue]:
    issues = []
    for e in event_log.filter_by_type(EventType.PACKET_FORWARDED):
        ttl = e.metadata.get("ttl_remaining")
        if ttl is not None and ttl < 0:
            issues.append(ValidationIssue(
                Severity.ERROR, "NEGATIVE_TTL",
                f"Packet {e.packet_id} had negative TTL ({ttl}) after forwarding at t={e.timestamp}",
                {"packet_id": e.packet_id, "ttl_remaining": ttl},
            ))
    return issues


def check_aear_weights_sum_to_one(event_log: EventLog, tolerance: float = 1e-6) -> List[ValidationIssue]:
    """Every AEAR weight set actually recorded in CANDIDATE_EVALUATED events
    must sum to 1.0 -- audits what actually ran, not just what AEARConfig
    validated at construction time."""
    issues = []
    seen = set()
    for e in event_log.filter_by_types([EventType.CANDIDATE_EVALUATED]):
        if e.algorithm != "AEAR":
            continue
        weights = e.metadata.get("weights")
        if not weights:
            continue
        key = (weights.get("signal"), weights.get("hop"), weights.get("congestion"))
        if key in seen:
            continue
        seen.add(key)
        total = sum(v for v in key if v is not None)
        if abs(total - 1.0) > tolerance:
            issues.append(ValidationIssue(
                Severity.ERROR, "AEAR_WEIGHTS_NOT_NORMALIZED",
                f"AEAR weights {weights} sum to {total}, not 1.0",
                {"weights": weights, "sum": total, "event_id": e.event_id},
            ))
    return issues


def check_no_completed_recovery_cycles(event_log: EventLog) -> List[ValidationIssue]:
    """Informational only: flags a scenario where failures were injected but
    none ever completed a recover cycle within the run -- often just means
    the run ended before restoration, worth a WARNING not an ERROR."""
    node_intervals = _build_intervals(event_log, EventType.NODE_FAILED, EventType.NODE_RECOVERED,
                                      key_fn=lambda e: e.node_id)
    link_intervals = _build_intervals(event_log, EventType.LINK_FAILED, EventType.LINK_RESTORED,
                                      key_fn=lambda e: tuple(sorted((e.metadata.get("node_a"), e.metadata.get("node_b")))))
    all_intervals = list(node_intervals.values()) + list(link_intervals.values())
    if not all_intervals:
        return []
    unresolved = [iv for intervals in all_intervals for iv in intervals if iv[1] == math.inf]
    if unresolved:
        return [ValidationIssue(
            Severity.WARNING, "UNRESOLVED_FAILURE_AT_RUN_END",
            f"{len(unresolved)} failure(s) never recovered before the run ended",
            {"count": len(unresolved)},
        )]
    return []
