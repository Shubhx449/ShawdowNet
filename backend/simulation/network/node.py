"""
Node model.

A Node is a physical point in the mesh network (a LoRa-equipped device in
the real system, a simulated endpoint here). It knows nothing about routing
algorithms, packets in flight, or the rest of the network graph — it only
tracks its own identity, position, availability, and its local packet queue.

Keeping Node "dumb" like this is deliberate: routing engines (AEAR/RSSI/AODV)
read node state to make decisions, but the node itself never makes decisions.
This keeps the simulation modular per the project architecture.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from backend.simulation.queues.queue_model import PacketQueue


class NodeStatus(str, Enum):
    """Visual/operational state of a node at a point in simulated time."""

    NORMAL = "NORMAL"
    CONGESTED = "CONGESTED"      # queue occupancy above a soft threshold
    FAILED = "FAILED"            # manually or randomly failed; cannot forward
    SOURCE = "SOURCE"            # role marker for the active packet, not exclusive
    DESTINATION = "DESTINATION"  # role marker for the active packet, not exclusive


@dataclass
class Node:
    """
    A single mesh node.

    Attributes:
        node_id: Unique identifier, e.g. "N1". Stable for the node's lifetime.
        x, y: Position in meters within the simulation area.
        status: Current NodeStatus. FAILED nodes are excluded from routing.
        queue: This node's outgoing packet queue (see queue_model.PacketQueue).
        congestion_threshold: Fraction of queue capacity (0-1) above which the
            node is considered CONGESTED for visualization purposes. This is
            a display concern, NOT the same as the AEAR congestion-availability
            factor, which is computed directly from queue occupancy/capacity
            by the routing engine in a later phase.
    """

    node_id: str
    x: float
    y: float
    status: NodeStatus = NodeStatus.NORMAL
    queue: PacketQueue = field(default_factory=lambda: PacketQueue(capacity=30))
    congestion_threshold: float = 0.75

    # -- availability -----------------------------------------------------

    @property
    def is_failed(self) -> bool:
        return self.status == NodeStatus.FAILED

    @property
    def is_available(self) -> bool:
        """A node is available for routing/forwarding if it has not failed."""
        return not self.is_failed

    def fail(self) -> None:
        """Mark this node as failed. It can no longer forward or receive."""
        self.status = NodeStatus.FAILED

    def restore(self) -> None:
        """Bring a failed node back online. Queue contents are preserved."""
        if self.status == NodeStatus.FAILED:
            self.status = NodeStatus.NORMAL
        self._refresh_congestion_status()

    # -- congestion ---------------------------------------------------------

    def _refresh_congestion_status(self) -> None:
        """Recompute NORMAL vs CONGESTED based on current queue occupancy."""
        if self.status == NodeStatus.FAILED:
            return
        if self.queue.occupancy_ratio() >= self.congestion_threshold:
            self.status = NodeStatus.CONGESTED
        elif self.status == NodeStatus.CONGESTED:
            self.status = NodeStatus.NORMAL

    def on_queue_changed(self) -> None:
        """Call after any enqueue/dequeue so visual status stays in sync."""
        self._refresh_congestion_status()

    # -- geometry -------------------------------------------------------------

    def distance_to(self, other: "Node") -> float:
        """Euclidean distance in meters to another node."""
        return ((self.x - other.x) ** 2 + (self.y - other.y) ** 2) ** 0.5

    def position(self) -> tuple[float, float]:
        return (self.x, self.y)

    def to_dict(self) -> dict:
        """Serializable snapshot, used by the API/WebSocket layers later."""
        return {
            "node_id": self.node_id,
            "x": self.x,
            "y": self.y,
            "status": self.status.value,
            "queue_occupancy": self.queue.size(),
            "queue_capacity": self.queue.capacity,
            "is_failed": self.is_failed,
        }

    def __repr__(self) -> str:  # pragma: no cover - debug convenience only
        return f"Node({self.node_id}, pos=({self.x:.1f},{self.y:.1f}), status={self.status.value})"
