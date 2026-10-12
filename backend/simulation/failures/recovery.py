"""
FailureRecoveryManager.

Closes the loop that AEAR/RSSI/AODV each deliberately left open in Phase 3:
when a node or link comes back online, any packet that got buffered because
of an earlier failure should be re-evaluated against the CURRENT network
state -- not simply re-sent along the old (now possibly stale) decision.

This manager stays algorithm-agnostic on purpose. It doesn't know anything
about AEAR scoring, RSSI, or AODV route tables -- it only knows that every
router exposes a `retry_buffered_packets()` method (see the bottom of each
router module), and calls it whenever NODE_RECOVERED or LINK_RESTORED fires.
Each router decides for itself, internally, which of its buffered packets
are actually safe to retry right now (AODV in particular must not touch a
packet that's still legitimately mid-route-discovery).
"""

from __future__ import annotations

from typing import Iterable, List, Protocol

from backend.simulation.engine.engine import SimulationEngine
from backend.simulation.events.event import Event, EventType


class RetryableRouter(Protocol):
    def retry_buffered_packets(self) -> None: ...


class FailureRecoveryManager:
    """
    Usage:
        engine = SimulationEngine(network)
        router = AEARRouter(network, engine)
        FailureRecoveryManager(engine, [router])
        # ... later, whenever a node/link recovers, router.retry_buffered_packets()
        # is called automatically.

    Works with any number of routers, though in practice only one routing
    algorithm is active per simulation run (per the fair-comparison rule).
    Accepting a list rather than a single router keeps this reusable for a
    hypothetical future case (e.g. Mode 1 letting the user switch algorithms
    mid-session) without changing this class.
    """

    def __init__(self, engine: SimulationEngine, routers: Iterable[RetryableRouter]):
        self.engine = engine
        self.routers: List[RetryableRouter] = list(routers)
        engine.register_handler(EventType.NODE_RECOVERED, self._on_recovery)
        engine.register_handler(EventType.LINK_RESTORED, self._on_recovery)

    def add_router(self, router: RetryableRouter) -> None:
        self.routers.append(router)

    def _on_recovery(self, event: Event, engine: SimulationEngine) -> None:
        for router in self.routers:
            router.retry_buffered_packets()
