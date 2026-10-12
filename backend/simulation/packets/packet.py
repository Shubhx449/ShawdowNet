"""
Packet model.

A Packet is the unit that moves through the network. It carries everything
needed for loop prevention (visited-node list), TTL expiry, retransmission
counting, and store-and-forward (status = BUFFERED), plus a full forwarding
history so any metric can be reconstructed from the packet's own record
after the fact (per the "everything must be traceable to raw events"
requirement).

Note: routing algorithms (AEAR/RSSI/AODV) do NOT live here. This module only
models the packet's data and lifecycle bookkeeping.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class PacketType(str, Enum):
    NORMAL = "NORMAL"
    IMPORTANT = "IMPORTANT"
    EMERGENCY = "EMERGENCY"

    @property
    def priority(self) -> int:
        """Numeric priority P(p) in {0, 1, 2} as defined in the paper."""
        return {"NORMAL": 0, "IMPORTANT": 1, "EMERGENCY": 2}[self.value]


class PacketStatus(str, Enum):
    IN_TRANSIT = "IN_TRANSIT"
    BUFFERED = "BUFFERED"
    DELIVERED = "DELIVERED"
    DROPPED_TTL_EXPIRED = "DROPPED_TTL_EXPIRED"
    DROPPED_NO_CANDIDATE = "DROPPED_NO_CANDIDATE"
    DROPPED_MAX_RETRANSMISSIONS = "DROPPED_MAX_RETRANSMISSIONS"


@dataclass
class ForwardingEvent:
    """One hop in a packet's journey, recorded for traceability/debugging."""

    timestamp: float
    from_node: Optional[str]
    to_node: Optional[str]
    action: str  # e.g. "FORWARDED", "BUFFERED", "RETRANSMIT", "DELIVERED", "DROPPED"
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "from_node": self.from_node,
            "to_node": self.to_node,
            "action": self.action,
            "detail": self.detail,
        }


class _PacketIdGenerator:
    """Monotonic, human-readable packet IDs: P001, P002, ..."""

    def __init__(self):
        self._counter = itertools.count(1)

    def next_id(self) -> str:
        return f"P{next(self._counter):03d}"


_id_generator = _PacketIdGenerator()


def reset_packet_id_counter() -> None:
    """
    Reset the global packet ID counter. Call this at the start of each
    experiment run so packet IDs are reproducible/comparable across runs
    (important for the fair-comparison requirement across algorithms).
    """
    global _id_generator
    _id_generator = _PacketIdGenerator()


@dataclass
class Packet:
    """
    A single packet traveling through the mesh.

    Attributes:
        packet_id: Unique ID, auto-generated (P001, P002, ...) unless supplied.
        source: Node ID where the packet originated.
        destination: Node ID the packet is trying to reach.
        packet_type: NORMAL / IMPORTANT / EMERGENCY (also determines priority).
        message: Human-readable payload, for display purposes only.
        ttl: Remaining hop budget. Decremented on every forward; packet is
            dropped when it reaches 0 before delivery.
        max_retransmissions: Cap on retransmission attempts at a single hop
            before the packet is buffered or dropped.
        creation_time: Simulated time the packet was generated.
    """

    source: str
    destination: str
    packet_type: PacketType
    message: str = ""
    ttl: int = 12
    max_retransmissions: int = 3
    creation_time: float = 0.0
    packet_id: str = field(default_factory=lambda: _id_generator.next_id())

    # -- mutable lifecycle state --------------------------------------------
    current_node: str = field(init=False)
    previous_node: Optional[str] = field(default=None, init=False)
    visited_nodes: List[str] = field(default_factory=list, init=False)
    hop_count: int = field(default=0, init=False)
    retransmission_count: int = field(default=0, init=False)
    status: PacketStatus = field(default=PacketStatus.IN_TRANSIT, init=False)
    delivery_time: Optional[float] = field(default=None, init=False)
    forwarding_history: List[ForwardingEvent] = field(default_factory=list, init=False)
    buffered_at: Optional[str] = field(default=None, init=False)
    buffer_since: Optional[float] = field(default=None, init=False)

    def __post_init__(self):
        self.current_node = self.source
        self.visited_nodes = [self.source]

    # -- derived properties ---------------------------------------------------

    @property
    def priority(self) -> int:
        return self.packet_type.priority

    @property
    def is_emergency(self) -> bool:
        return self.packet_type == PacketType.EMERGENCY

    @property
    def is_delivered(self) -> bool:
        return self.status == PacketStatus.DELIVERED

    @property
    def is_dropped(self) -> bool:
        return self.status.value.startswith("DROPPED")

    @property
    def is_terminal(self) -> bool:
        """True once the packet has reached a final state (no longer routable)."""
        return self.is_delivered or self.is_dropped

    # -- lifecycle mutators (called by the simulation engine, not routing) ----

    def record_event(self, timestamp: float, from_node: Optional[str],
                      to_node: Optional[str], action: str, **detail) -> None:
        self.forwarding_history.append(
            ForwardingEvent(timestamp=timestamp, from_node=from_node,
                             to_node=to_node, action=action, detail=detail)
        )

    def mark_forwarded(self, timestamp: float, next_node: str) -> None:
        """Advance the packet one hop toward next_node."""
        if self.ttl <= 0:
            raise ValueError("Cannot forward a packet with TTL already at 0")
        self.record_event(timestamp, self.current_node, next_node, "FORWARDED")
        self.previous_node = self.current_node
        self.current_node = next_node
        self.visited_nodes.append(next_node)
        self.hop_count += 1
        self.ttl -= 1
        self.retransmission_count = 0  # reset per-hop retransmission counter
        if next_node == self.destination:
            self.status = PacketStatus.DELIVERED
            self.delivery_time = timestamp
            self.record_event(timestamp, next_node, None, "DELIVERED")

    def mark_retransmission(self, timestamp: float) -> bool:
        """
        Register a retransmission attempt at the current hop.

        Returns:
            True if another attempt is allowed, False if max_retransmissions
            has been exhausted (caller should then buffer or drop).
        """
        self.retransmission_count += 1
        self.record_event(timestamp, self.current_node, None, "RETRANSMISSION",
                           attempt=self.retransmission_count)
        return self.retransmission_count < self.max_retransmissions

    def mark_buffered(self, timestamp: float, reason: str = "No valid forwarding candidate") -> None:
        self.status = PacketStatus.BUFFERED
        self.buffered_at = self.current_node
        self.buffer_since = timestamp
        self.record_event(timestamp, self.current_node, None, "BUFFERED", reason=reason)

    def mark_unbuffered(self) -> None:
        """Called when re-evaluation begins for a previously buffered packet."""
        self.status = PacketStatus.IN_TRANSIT
        self.buffered_at = None
        self.buffer_since = None

    def mark_dropped(self, timestamp: float, status: PacketStatus, reason: str = "") -> None:
        if status not in (
            PacketStatus.DROPPED_TTL_EXPIRED,
            PacketStatus.DROPPED_NO_CANDIDATE,
            PacketStatus.DROPPED_MAX_RETRANSMISSIONS,
        ):
            raise ValueError(f"{status} is not a valid drop status")
        self.status = status
        self.record_event(timestamp, self.current_node, None, "DROPPED",
                           status=status.value, reason=reason)

    def has_visited(self, node_id: str) -> bool:
        return node_id in self.visited_nodes

    def to_dict(self) -> dict:
        return {
            "packet_id": self.packet_id,
            "source": self.source,
            "destination": self.destination,
            "packet_type": self.packet_type.value,
            "priority": self.priority,
            "message": self.message,
            "ttl": self.ttl,
            "max_retransmissions": self.max_retransmissions,
            "creation_time": self.creation_time,
            "current_node": self.current_node,
            "previous_node": self.previous_node,
            "visited_nodes": list(self.visited_nodes),
            "hop_count": self.hop_count,
            "retransmission_count": self.retransmission_count,
            "status": self.status.value,
            "delivery_time": self.delivery_time,
            "buffered_at": self.buffered_at,
            "buffer_since": self.buffer_since,
            "forwarding_history": [e.to_dict() for e in self.forwarding_history],
        }

    def __repr__(self) -> str:  # pragma: no cover
        return (f"Packet({self.packet_id}, {self.source}->{self.destination}, "
                f"type={self.packet_type.value}, at={self.current_node}, "
                f"ttl={self.ttl}, status={self.status.value})")
