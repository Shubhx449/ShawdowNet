"""
QueueSampler.

Queue Overflow has a natural raw-event source already (QUEUE_OVERFLOW, from
Phase 2). But Queue Utilization, Average Queue Length, and Maximum Queue
Length need to know occupancy *over time*, and nothing in the simulation
otherwise produces that -- a node's queue is live, mutable state, not an
event. Without periodic sampling, those three metrics would have to read
Node.queue state directly, breaking the "metrics come only from event_log"
rule this project cares about.

QueueSampler closes that gap: attach it to an engine, and it periodically
emits a QUEUE_SNAPSHOT event per node with the CURRENT occupancy/capacity at
that instant, entirely through the normal engine.schedule()/log_now()
machinery -- no special-casing needed elsewhere.
"""

from __future__ import annotations

from backend.simulation.engine.engine import SimulationEngine
from backend.simulation.events.event import Event, EventType
from backend.simulation.network.network import Network


class QueueSampler:
    """
    IMPORTANT: because this reschedules itself every `interval_s`, you MUST
    provide `end_time` (the run's total simulated duration). Without a
    bound, the sampler would keep the scheduler non-empty forever and
    engine.run() would never return. `end_time` is required, not optional,
    specifically to make that mistake hard to make.

    Usage:
        engine = SimulationEngine(network)
        AEARRouter(network, engine)
        QueueSampler(engine, network, interval_s=1.0, end_time=180.0)
        engine.generate_packet(...)
        engine.run(until=180.0)
    """

    def __init__(self, engine: SimulationEngine, network: Network,
                 interval_s: float, end_time: float):
        if interval_s <= 0:
            raise ValueError("interval_s must be positive")
        if end_time <= 0:
            raise ValueError("end_time must be positive")
        self.engine = engine
        self.network = network
        self.interval_s = interval_s
        self.end_time = end_time

        engine.register_handler(EventType.QUEUE_SNAPSHOT, self._on_event)
        self._schedule_tick(0.0)

    def _schedule_tick(self, at_time: float) -> None:
        if at_time > self.end_time:
            return
        self.engine.schedule(at_time, EventType.QUEUE_SNAPSHOT, kind="tick")

    def _on_event(self, event: Event, engine: SimulationEngine) -> None:
        if event.metadata.get("kind") != "tick":
            return  # ignore the per-node "sample" events this same tick emits
        for node in self.network.nodes.values():
            self.engine.log_now(EventType.QUEUE_SNAPSHOT, node_id=node.node_id,
                                kind="sample", occupancy=node.queue.size(),
                                capacity=node.queue.capacity)
        self._schedule_tick(self.engine.clock + self.interval_s)
