"""
MetricsEngine.

Every number this module produces is derived ONLY from an EventLog -- never
by reading Packet or Node objects directly. That's a deliberate structural
choice, not just a convention: `compute()` takes an EventLog and nothing
else that could leak live simulation state, so there's no way to
accidentally compute a metric from something other than what actually got
logged. This is the project's core scientific-integrity requirement made
literal: if it isn't in the event log, it isn't in the metrics.

Some metrics are honestly zero given what AEAR/RSSI/AODV currently model
(no probabilistic per-hop transmission failure, no data-packet duplication)
-- `MetricsResult.notes` explains exactly which ones and why, so a zero
here always reads as "correctly reflects the simulation" rather than
"something wasn't wired up".
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from backend.simulation.events.event import Event, EventLog, EventType


class MetricsIntegrityError(Exception):
    """Raised when computed metrics fail an internal self-consistency check
    (e.g. delivered > generated). Indicates a bug in event logging, not in
    the metrics math itself -- these invariants should be impossible to
    violate if events were logged correctly."""


@dataclass
class MetricsResult:
    generated_packets: int
    delivered_packets: int
    pdr_percent: float
    avg_end_to_end_delay_s: Optional[float]
    avg_hop_count: Optional[float]
    packet_loss: int
    packet_loss_rate_percent: float
    throughput_packets_per_s: Optional[float]

    emergency_packets_generated: int
    emergency_packets_delivered: int
    emergency_pdr_percent: Optional[float]
    avg_emergency_delay_s: Optional[float]

    pdr_by_priority: Dict[str, Optional[float]]
    avg_delay_by_priority: Dict[str, Optional[float]]

    routing_overhead_control_messages: int

    recovery_events_count: int
    avg_recovery_time_s: Optional[float]

    duplicate_forward_count: int
    duplicate_packet_rate_percent: float

    queue_overflow_count: int
    avg_queue_length: Optional[float]
    max_queue_length: Optional[int]
    avg_queue_utilization_percent: Optional[float]

    forwarding_decisions: int
    successful_forwarding_attempts: int
    failed_forwarding_attempts: int
    retransmissions: int

    packets_buffered_events: int
    unique_packets_buffered: int
    successfully_recovered_buffered_packets: int

    notes: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "generated_packets": self.generated_packets,
            "delivered_packets": self.delivered_packets,
            "pdr_percent": round(self.pdr_percent, 4),
            "avg_end_to_end_delay_s": _round_or_none(self.avg_end_to_end_delay_s),
            "avg_hop_count": _round_or_none(self.avg_hop_count),
            "packet_loss": self.packet_loss,
            "packet_loss_rate_percent": round(self.packet_loss_rate_percent, 4),
            "throughput_packets_per_s": _round_or_none(self.throughput_packets_per_s),
            "emergency_packets_generated": self.emergency_packets_generated,
            "emergency_packets_delivered": self.emergency_packets_delivered,
            "emergency_pdr_percent": _round_or_none(self.emergency_pdr_percent),
            "avg_emergency_delay_s": _round_or_none(self.avg_emergency_delay_s),
            "pdr_by_priority": {k: _round_or_none(v) for k, v in self.pdr_by_priority.items()},
            "avg_delay_by_priority": {k: _round_or_none(v) for k, v in self.avg_delay_by_priority.items()},
            "routing_overhead_control_messages": self.routing_overhead_control_messages,
            "recovery_events_count": self.recovery_events_count,
            "avg_recovery_time_s": _round_or_none(self.avg_recovery_time_s),
            "duplicate_forward_count": self.duplicate_forward_count,
            "duplicate_packet_rate_percent": round(self.duplicate_packet_rate_percent, 4),
            "queue_overflow_count": self.queue_overflow_count,
            "avg_queue_length": _round_or_none(self.avg_queue_length),
            "max_queue_length": self.max_queue_length,
            "avg_queue_utilization_percent": _round_or_none(self.avg_queue_utilization_percent),
            "forwarding_decisions": self.forwarding_decisions,
            "successful_forwarding_attempts": self.successful_forwarding_attempts,
            "failed_forwarding_attempts": self.failed_forwarding_attempts,
            "retransmissions": self.retransmissions,
            "packets_buffered_events": self.packets_buffered_events,
            "unique_packets_buffered": self.unique_packets_buffered,
            "successfully_recovered_buffered_packets": self.successfully_recovered_buffered_packets,
            "notes": self.notes,
        }


def _round_or_none(value: Optional[float], digits: int = 4) -> Optional[float]:
    return round(value, digits) if value is not None else None


def _avg(values: List[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def _pair_durations(started: List[Event], ended: List[Event],
                     key_fn: Callable[[Event], object]) -> List[float]:
    """
    Pair each `started` event with the next unconsumed `ended` event sharing
    the same key (by timestamp order), and return the list of durations.
    Handles multiple fail/recover cycles for the same node or link key
    correctly by consuming ended-events in order and never reusing one.
    """
    ended_by_key: Dict[object, List[float]] = defaultdict(list)
    for e in ended:
        ended_by_key[key_fn(e)].append(e.timestamp)
    for lst in ended_by_key.values():
        lst.sort()

    next_index: Dict[object, int] = defaultdict(int)
    durations: List[float] = []
    for e in sorted(started, key=lambda ev: ev.timestamp):
        key = key_fn(e)
        candidates = ended_by_key.get(key, [])
        idx = next_index[key]
        while idx < len(candidates) and candidates[idx] < e.timestamp:
            idx += 1
        if idx < len(candidates):
            durations.append(candidates[idx] - e.timestamp)
            next_index[key] = idx + 1
    return durations


class MetricsEngine:
    """Stateless: compute() takes an EventLog and returns a fresh MetricsResult."""

    @staticmethod
    def compute(event_log: EventLog, simulation_duration_s: Optional[float] = None) -> MetricsResult:
        notes: Dict[str, str] = {}

        generated = event_log.filter_by_type(EventType.PACKET_GENERATED)
        delivered = event_log.filter_by_type(EventType.PACKET_DELIVERED)
        buffered = event_log.filter_by_type(EventType.PACKET_BUFFERED)
        forwarded = event_log.filter_by_type(EventType.PACKET_FORWARDED)
        decisions = event_log.filter_by_type(EventType.FORWARDING_DECISION)
        retransmissions = event_log.filter_by_type(EventType.RETRANSMISSION)
        overflow = event_log.filter_by_type(EventType.QUEUE_OVERFLOW)
        snapshots = event_log.filter_by_type(EventType.QUEUE_SNAPSHOT)
        rreq_sent = event_log.filter_by_type(EventType.RREQ_SENT)
        rrep_sent = event_log.filter_by_type(EventType.RREP_SENT)
        rerr_sent = event_log.filter_by_type(EventType.RERR_SENT)
        node_failed = event_log.filter_by_type(EventType.NODE_FAILED)
        node_recovered = event_log.filter_by_type(EventType.NODE_RECOVERED)
        link_failed = event_log.filter_by_type(EventType.LINK_FAILED)
        link_restored = event_log.filter_by_type(EventType.LINK_RESTORED)

        # -- basic delivery metrics --------------------------------------------
        generated_packets = len(generated)
        delivered_packets = len(delivered)
        pdr = (delivered_packets / generated_packets * 100) if generated_packets else 0.0
        packet_loss = generated_packets - delivered_packets
        loss_rate = (packet_loss / generated_packets * 100) if generated_packets else 0.0

        throughput = None
        if simulation_duration_s is not None and simulation_duration_s > 0:
            throughput = delivered_packets / simulation_duration_s
        else:
            notes["throughput"] = ("No simulation_duration_s was provided to MetricsEngine.compute() -- "
                                    "throughput requires knowing the run's total duration, not just "
                                    "event timestamps (the last event may occur well before the "
                                    "configured duration if traffic ended early).")

        delays = [e.metadata["end_to_end_delay"] for e in delivered if "end_to_end_delay" in e.metadata]
        avg_delay = _avg(delays)
        hop_counts = [e.metadata["hop_count"] for e in delivered if "hop_count" in e.metadata]
        avg_hops = _avg(hop_counts)

        # -- priority breakdown (from PACKET_GENERATED metadata only) ---------
        priority_by_packet: Dict[str, str] = {
            e.packet_id: e.metadata["packet_type"]
            for e in generated if e.packet_id and "packet_type" in e.metadata
        }
        pdr_by_priority: Dict[str, Optional[float]] = {}
        delay_by_priority: Dict[str, Optional[float]] = {}
        for ptype in ("NORMAL", "IMPORTANT", "EMERGENCY"):
            ids = {pid for pid, t in priority_by_packet.items() if t == ptype}
            if not ids:
                pdr_by_priority[ptype] = None
                delay_by_priority[ptype] = None
                continue
            delivered_this = [e for e in delivered if e.packet_id in ids]
            pdr_by_priority[ptype] = len(delivered_this) / len(ids) * 100
            d = [e.metadata["end_to_end_delay"] for e in delivered_this if "end_to_end_delay" in e.metadata]
            delay_by_priority[ptype] = _avg(d)

        emergency_ids = {pid for pid, t in priority_by_packet.items() if t == "EMERGENCY"}
        emergency_generated = len(emergency_ids)
        emergency_delivered_events = [e for e in delivered if e.packet_id in emergency_ids]
        emergency_delivered = len(emergency_delivered_events)
        emergency_pdr = (emergency_delivered / emergency_generated * 100) if emergency_generated else None
        emergency_delay = _avg([e.metadata["end_to_end_delay"] for e in emergency_delivered_events
                                 if "end_to_end_delay" in e.metadata])

        # -- routing overhead: real transmissions only (SENT, not RECEIVED) ---
        # AEAR/RSSI make purely local decisions with zero control-plane radio
        # transmissions, so this will correctly read 0 for them -- that's a
        # real, meaningful difference from AODV, not a gap.
        routing_overhead = len(rreq_sent) + len(rrep_sent) + len(rerr_sent)

        # -- recovery time: pair each failure with its next recovery ----------
        recovery_durations = _pair_durations(node_failed, node_recovered, key_fn=lambda e: ("node", e.node_id))
        recovery_durations += _pair_durations(
            link_failed, link_restored,
            key_fn=lambda e: ("link", tuple(sorted((e.metadata.get("node_a"), e.metadata.get("node_b"))))),
        )
        avg_recovery_time = _avg(recovery_durations)
        if not recovery_durations:
            notes["recovery_time"] = ("No completed failure/recovery cycle found in this run -- "
                                       "either nothing failed, or nothing failed AND recovered "
                                       "within the simulated duration.")

        # -- duplicate data-packet transmissions -------------------------------
        forward_keys = [(e.packet_id, e.node_id, e.metadata.get("to_node")) for e in forwarded]
        key_counts = Counter(forward_keys)
        duplicate_forward_count = sum(c - 1 for c in key_counts.values() if c > 1)
        duplicate_rate = (duplicate_forward_count / len(forwarded) * 100) if forwarded else 0.0
        if duplicate_forward_count == 0:
            notes["duplicate_packet_rate"] = ("0% is expected with the current routing engines -- "
                                               "loop prevention via visited-node tracking rules out "
                                               "a data packet ever being sent over the same link twice.")

        # -- queue metrics: only available if a QueueSampler was attached -----
        samples = [e for e in snapshots if e.metadata.get("kind") == "sample"]
        if samples:
            occ = [e.metadata["occupancy"] for e in samples]
            cap = [e.metadata["capacity"] for e in samples]
            avg_queue_length = _avg(occ)
            max_queue_length = max(occ)
            utils = [o / c * 100 for o, c in zip(occ, cap) if c > 0]
            avg_utilization = _avg(utils)
        else:
            avg_queue_length = max_queue_length = avg_utilization = None
            notes["queue_length_metrics"] = ("No QUEUE_SNAPSHOT samples found -- attach a QueueSampler "
                                              "to the engine before running to enable Queue Utilization / "
                                              "Average / Maximum Length. Queue Overflow count below is "
                                              "accurate regardless (it has its own event, independent of sampling).")

        # -- forwarding / retransmission counts --------------------------------
        forwarding_decisions = len(decisions)
        successful_forwarding_attempts = len(forwarded)
        failed_forwarding_attempts = len([e for e in retransmissions if e.metadata.get("exhausted")])
        retransmission_count = len(retransmissions)
        if retransmission_count == 0:
            notes["retransmissions"] = ("0 is expected with the current routing engines -- none of "
                                         "AEAR/RSSI/AODV currently model a probabilistic per-hop "
                                         "transmission failure that would trigger a retry, so this "
                                         "reflects the actual simulation rather than a missing data source.")

        # -- buffering / store-and-forward recovery -----------------------------
        packets_buffered_events = len(buffered)
        unique_buffered_ids = {e.packet_id for e in buffered if e.packet_id}
        delivered_ids = {e.packet_id for e in delivered if e.packet_id}
        successfully_recovered = len(unique_buffered_ids & delivered_ids)

        result = MetricsResult(
            generated_packets=generated_packets,
            delivered_packets=delivered_packets,
            pdr_percent=pdr,
            avg_end_to_end_delay_s=avg_delay,
            avg_hop_count=avg_hops,
            packet_loss=packet_loss,
            packet_loss_rate_percent=loss_rate,
            throughput_packets_per_s=throughput,
            emergency_packets_generated=emergency_generated,
            emergency_packets_delivered=emergency_delivered,
            emergency_pdr_percent=emergency_pdr,
            avg_emergency_delay_s=emergency_delay,
            pdr_by_priority=pdr_by_priority,
            avg_delay_by_priority=delay_by_priority,
            routing_overhead_control_messages=routing_overhead,
            recovery_events_count=len(recovery_durations),
            avg_recovery_time_s=avg_recovery_time,
            duplicate_forward_count=duplicate_forward_count,
            duplicate_packet_rate_percent=duplicate_rate,
            queue_overflow_count=len(overflow),
            avg_queue_length=avg_queue_length,
            max_queue_length=max_queue_length,
            avg_queue_utilization_percent=avg_utilization,
            forwarding_decisions=forwarding_decisions,
            successful_forwarding_attempts=successful_forwarding_attempts,
            failed_forwarding_attempts=failed_forwarding_attempts,
            retransmissions=retransmission_count,
            packets_buffered_events=packets_buffered_events,
            unique_packets_buffered=len(unique_buffered_ids),
            successfully_recovered_buffered_packets=successfully_recovered,
            notes=notes,
        )
        MetricsEngine._validate(result)
        return result

    @staticmethod
    def _validate(result: MetricsResult) -> None:
        """Lightweight internal self-consistency checks (full validation
        layer with richer, run-config-aware checks is Phase 7)."""
        if not (0.0 <= result.pdr_percent <= 100.0):
            raise MetricsIntegrityError(f"PDR out of range: {result.pdr_percent}")
        if result.delivered_packets > result.generated_packets:
            raise MetricsIntegrityError("delivered_packets exceeds generated_packets")
        if result.packet_loss != result.generated_packets - result.delivered_packets:
            raise MetricsIntegrityError("packet_loss does not equal generated - delivered")
        if result.avg_end_to_end_delay_s is not None and result.avg_end_to_end_delay_s < 0:
            raise MetricsIntegrityError("negative avg_end_to_end_delay_s")
        if result.avg_hop_count is not None and result.avg_hop_count < 0:
            raise MetricsIntegrityError("negative avg_hop_count")
        if result.successfully_recovered_buffered_packets > result.unique_packets_buffered:
            raise MetricsIntegrityError("successfully_recovered_buffered_packets exceeds unique_packets_buffered")
