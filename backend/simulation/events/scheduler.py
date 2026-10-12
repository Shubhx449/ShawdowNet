"""
EventScheduler — the "future event list" (FEL) of a discrete-event simulation.

This is deliberately separate from EventLog:
  - EventLog is the *past* — an append-only record of what already happened.
  - EventScheduler is the *future* — a priority queue of things that will
    happen, ordered by simulated time, waiting to be processed.

Standard discrete-event simulation pattern: pop the earliest-scheduled item,
advance the simulation clock to its timestamp, process it (which may itself
schedule new future items), repeat.
"""

from __future__ import annotations

import heapq
import itertools
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(order=True)
class _ScheduledItem:
    """
    Wraps a pending event for heap ordering.

    Ordered by (time, sequence) so that:
      1. Earlier simulated time always comes first.
      2. Events scheduled for the exact same timestamp are processed in the
         order they were scheduled (FIFO tie-break) — without this, heapq
         would fall back to comparing the payload, which isn't orderable.
    """

    time: float
    sequence: int
    payload: Any = field(compare=False)


class EventScheduler:
    """A min-heap of pending (time, payload) items, FIFO among equal times."""

    def __init__(self):
        self._heap: list[_ScheduledItem] = []
        self._seq_counter = itertools.count()

    def schedule(self, time: float, payload: Any) -> None:
        """Queue `payload` (typically kwargs for building an Event) for `time`."""
        item = _ScheduledItem(time=time, sequence=next(self._seq_counter), payload=payload)
        heapq.heappush(self._heap, item)

    def pop_next(self) -> Optional[tuple[float, Any]]:
        """
        Remove and return (time, payload) for the earliest-scheduled item,
        or None if the scheduler is empty. Returning time alongside the
        payload avoids callers having to duplicate it inside the payload.
        """
        if not self._heap:
            return None
        item = heapq.heappop(self._heap)
        return item.time, item.payload

    def peek_next_time(self) -> Optional[float]:
        """Time of the earliest pending item, without removing it."""
        if not self._heap:
            return None
        return self._heap[0].time

    def is_empty(self) -> bool:
        return not self._heap

    def __len__(self) -> int:
        return len(self._heap)

    def clear(self) -> None:
        self._heap = []
        self._seq_counter = itertools.count()

    def __repr__(self) -> str:  # pragma: no cover
        return f"EventScheduler({len(self._heap)} pending)"
