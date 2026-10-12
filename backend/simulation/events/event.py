"""
Event model + EventLog.

Every meaningful thing that happens during a simulation run — a packet being
generated, forwarded, buffered, dropped; a node/link failing or recovering;
a routing decision being made — becomes one Event appended to the EventLog.

This is the single source of truth the Metrics engine (Phase 5) will read
from later: "all metrics must be traceable to raw simulation events" means
metrics code should never touch Packet/Node state directly to compute a
number — it should derive everything from the EventLog. Phase 2 doesn't
compute metrics yet, but it must record events completely and honestly
enough that Phase 5 can.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class EventType(str, Enum):
    # Packet lifecycle
    PACKET_GENERATED = "PACKET_GENERATED"
    PACKET_RECEIVED = "PACKET_RECEIVED"
    PACKET_FORWARDED = "PACKET_FORWARDED"
    PACKET_DELIVERED = "PACKET_DELIVERED"
    PACKET_DROPPED = "PACKET_DROPPED"
    PACKET_BUFFERED = "PACKET_BUFFERED"
    RETRANSMISSION = "RETRANSMISSION"
    QUEUE_OVERFLOW = "QUEUE_OVERFLOW"
    QUEUE_SNAPSHOT = "QUEUE_SNAPSHOT"  # periodic occupancy sample (Phase 5: QueueSampler),
                                       # needed for Queue Utilization / Avg / Max Length
                                       # metrics -- not in the original event list, added
                                       # because those metrics have no other raw-event source

    # Routing-decision visibility (used by Mode 1's live log, Phase 3 onward)
    NEIGHBOR_DISCOVERED = "NEIGHBOR_DISCOVERED"
    CANDIDATE_EVALUATED = "CANDIDATE_EVALUATED"
    FORWARDING_DECISION = "FORWARDING_DECISION"
    ROUTE_CHANGED = "ROUTE_CHANGED"

    # AODV control plane (emitted by the AODV routing module in Phase 3)
    RREQ_SENT = "RREQ_SENT"
    RREQ_RECEIVED = "RREQ_RECEIVED"
    RREP_SENT = "RREP_SENT"
    RREP_RECEIVED = "RREP_RECEIVED"
    RERR_SENT = "RERR_SENT"

    # Network state changes
    NODE_FAILED = "NODE_FAILED"
    NODE_RECOVERED = "NODE_RECOVERED"
    LINK_FAILED = "LINK_FAILED"
    LINK_RESTORED = "LINK_RESTORED"


@dataclass
class Event:
    """
    A single, immutable record of something that happened at a specific
    simulated time. Every field except event_type and timestamp is optional
    since not every event concerns a node+packet+algorithm all at once.
    """

    event_id: int
    timestamp: float
    event_type: EventType
    node_id: Optional[str] = None
    packet_id: Optional[str] = None
    algorithm: Optional[str] = None  # "AEAR" | "RSSI" | "AODV" | None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "event_type": self.event_type.value,
            "node_id": self.node_id,
            "packet_id": self.packet_id,
            "algorithm": self.algorithm,
            "metadata": self.metadata,
        }

    def __repr__(self) -> str:  # pragma: no cover
        parts = [f"t={self.timestamp:.3f}", self.event_type.value]
        if self.node_id:
            parts.append(f"node={self.node_id}")
        if self.packet_id:
            parts.append(f"packet={self.packet_id}")
        return f"Event(#{self.event_id}, " + ", ".join(parts) + ")"


class _EventIdGenerator:
    def __init__(self):
        self._counter = itertools.count(1)

    def next_id(self) -> int:
        return next(self._counter)


class EventLog:
    """
    Append-only, queryable record of every Event processed by the
    simulation engine, in the order they actually occurred (i.e. in
    non-decreasing timestamp order — the engine guarantees this, not
    the log itself).

    Each EventLog owns its own event-ID sequence, so two independent
    simulation runs (e.g. AEAR vs AODV on the same scenario) each start
    numbering events from 1 — reproducible and easy to diff.
    """

    def __init__(self):
        self._id_gen = _EventIdGenerator()
        self._events: List[Event] = []

    def next_event_id(self) -> int:
        return self._id_gen.next_id()

    def append(self, event: Event) -> None:
        self._events.append(event)

    def all(self) -> List[Event]:
        return list(self._events)

    def count(self) -> int:
        return len(self._events)

    def filter_by_type(self, event_type: EventType) -> List[Event]:
        return [e for e in self._events if e.event_type == event_type]

    def filter_by_types(self, event_types) -> List[Event]:
        types = set(event_types)
        return [e for e in self._events if e.event_type in types]

    def filter_by_packet(self, packet_id: str) -> List[Event]:
        return [e for e in self._events if e.packet_id == packet_id]

    def filter_by_node(self, node_id: str) -> List[Event]:
        return [e for e in self._events if e.node_id == node_id]

    def filter_by_algorithm(self, algorithm: str) -> List[Event]:
        return [e for e in self._events if e.algorithm == algorithm]

    def count_by_type(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for e in self._events:
            counts[e.event_type.value] = counts.get(e.event_type.value, 0) + 1
        return counts

    def to_list_of_dicts(self) -> List[dict]:
        return [e.to_dict() for e in self._events]

    def clear(self) -> None:
        """Reset the log (and its ID sequence) for a fresh simulation run."""
        self._id_gen = _EventIdGenerator()
        self._events = []

    def __len__(self) -> int:
        return len(self._events)

    def __repr__(self) -> str:  # pragma: no cover
        return f"EventLog({len(self._events)} events)"
