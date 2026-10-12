"""
Queue model.

Every node has one outgoing packet queue. This is what AEAR's congestion
factor (C_j = 1 - clip(q_j / q_max, 0, 1)) reads from, and what the
metrics engine later uses for queue-utilization / queue-overflow stats.

Deliberately simple: FIFO, fixed capacity, no priority reordering inside the
queue itself (packet PRIORITY affects routing decisions upstream, not queue
ordering — that's a design choice we can revisit in the experimental phase
if the paper calls for a priority queue instead of FIFO).
"""

from __future__ import annotations

from collections import deque
from typing import Deque, List, TYPE_CHECKING

if TYPE_CHECKING:
    from backend.simulation.packets.packet import Packet


class QueueOverflowError(Exception):
    """Raised when attempting to enqueue into a full queue without checking first."""


class PacketQueue:
    """
    Fixed-capacity FIFO queue of packets waiting to be transmitted by a node.

    Attributes:
        capacity: Maximum number of packets the queue can hold (default 30,
            matching the paper's default experiment configuration).
    """

    def __init__(self, capacity: int = 30):
        if capacity <= 0:
            raise ValueError("Queue capacity must be positive")
        self.capacity = capacity
        self._items: Deque["Packet"] = deque()
        self.total_overflow_count = 0  # cumulative, for metrics

    def size(self) -> int:
        return len(self._items)

    def is_full(self) -> bool:
        return len(self._items) >= self.capacity

    def is_empty(self) -> bool:
        return len(self._items) == 0

    def occupancy_ratio(self) -> float:
        """Fraction of capacity currently in use, in [0, 1]."""
        return len(self._items) / self.capacity

    def enqueue(self, packet: "Packet") -> bool:
        """
        Attempt to add a packet to the queue.

        Returns:
            True if the packet was enqueued, False if the queue was full
            (caller is responsible for treating this as QUEUE_OVERFLOW /
            triggering store-and-forward or drop logic in later phases).
        """
        if self.is_full():
            self.total_overflow_count += 1
            return False
        self._items.append(packet)
        return True

    def dequeue(self) -> "Packet | None":
        """Remove and return the oldest packet, or None if empty."""
        if self.is_empty():
            return None
        return self._items.popleft()

    def peek_all(self) -> List["Packet"]:
        """Read-only snapshot of queued packets, oldest first."""
        return list(self._items)

    def to_dict(self) -> dict:
        return {
            "capacity": self.capacity,
            "occupancy": self.size(),
            "occupancy_ratio": round(self.occupancy_ratio(), 4),
            "total_overflow_count": self.total_overflow_count,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"PacketQueue({self.size()}/{self.capacity})"
