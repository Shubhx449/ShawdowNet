"""
SimulationEngine.

This is the discrete-event core: it owns the simulation clock, the future
event list (EventScheduler), and the record of everything that happened
(EventLog). It knows how to advance time and dispatch events, and it
performs the handful of network-state mutations that need no routing
intelligence (node/link failure and recovery).

What it deliberately does NOT do: make routing decisions. There is no AEAR/
RSSI/AODV logic anywhere in this file. Routing engines (Phase 3) plug in by
calling `engine.register_handler(EventType.PACKET_GENERATED, my_handler)`
and `engine.register_handler(EventType.PACKET_RECEIVED, my_handler)` — when
those events fire, the engine calls the handler, which reads network/packet
state, decides a next hop, and calls back into engine helpers
(`forward_packet`, `buffer_packet`, `drop_packet`, ...) to act on that
decision. This keeps the engine reusable across all three algorithms
unchanged, per the project's modularity requirement.
"""

from __future__ import annotations

import itertools
from collections import defaultdict
from typing import Callable, Dict, List, Optional

from backend.simulation.events.event import Event, EventLog, EventType
from backend.simulation.events.scheduler import EventScheduler
from backend.simulation.network.network import Network
from backend.simulation.packets.packet import Packet, PacketStatus, PacketType

HandlerFn = Callable[[Event, "SimulationEngine"], None]


class SimulationEngine:
    """
    Attributes:
        network: The Network this engine operates on.
        clock: Current simulated time (advances only via step()/run()).
        scheduler: Future event list (pending, not-yet-processed events).
        event_log: Append-only record of every event that has been processed.
        packets: Registry of every Packet ever generated in this run, by ID.
    """

    def __init__(self, network: Network):
        self.network = network
        self.clock: float = 0.0
        self.scheduler = EventScheduler()
        self.event_log = EventLog()
        self.packets: Dict[str, Packet] = {}
        self._handlers: Dict[EventType, List[HandlerFn]] = defaultdict(list)
        self._processed_count = 0
        self._packet_counter = itertools.count(1)  # per-engine packet IDs; see reset()

    # -- handler registration (this is how Phase 3 routing plugs in) ---------

    def register_handler(self, event_type: EventType, handler: HandlerFn) -> None:
        """Register a callback invoked whenever `event_type` is processed."""
        self._handlers[event_type].append(handler)

    def clear_handlers(self, event_type: Optional[EventType] = None) -> None:
        """Remove handlers for one event type, or all handlers if omitted."""
        if event_type is None:
            self._handlers.clear()
        else:
            self._handlers.pop(event_type, None)

    # -- lifecycle -------------------------------------------------------------

    def reset(self, network: Optional[Network] = None) -> None:
        """
        Reset engine state for a fresh run. Does NOT clear registered
        handlers — a routing engine registers once and can be reused across
        multiple experiment runs (e.g. run 1..10 of the same algorithm).
        """
        if network is not None:
            self.network = network
        self.clock = 0.0
        self.scheduler.clear()
        self.event_log.clear()
        self.packets = {}
        self._processed_count = 0
        # Packet IDs are allocated PER ENGINE (P001, P002, ...). They used to come from a process-wide counter that
        # the runner, every Live session and this method reset, so creating a second engine (an experiment run,
        # another Live tab) made a running session reuse IDs and silently overwrite its own in-flight packets.
        self._packet_counter = itertools.count(1)

    @property
    def processed_event_count(self) -> int:
        return self._processed_count

    # -- scheduling (future events) -------------------------------------------

    def schedule(self, time: float, event_type: EventType, node_id: Optional[str] = None,
                 packet_id: Optional[str] = None, algorithm: Optional[str] = None,
                 **metadata) -> None:
        """Queue an event to be processed at `time` (must be >= current clock)."""
        if time < self.clock:
            raise ValueError(
                f"Cannot schedule {event_type} at time {time}, which is before "
                f"the current clock ({self.clock}). Simulated time never moves backward."
            )
        payload = {
            "event_type": event_type,
            "node_id": node_id,
            "packet_id": packet_id,
            "algorithm": algorithm,
            "metadata": metadata,
        }
        self.scheduler.schedule(time, payload)

    def log_now(self, event_type: EventType, node_id: Optional[str] = None,
                packet_id: Optional[str] = None, algorithm: Optional[str] = None,
                **metadata) -> Event:
        """
        Record an event that happens instantaneously at the current clock
        time — e.g. a candidate-evaluation sub-step that occurs while
        processing a PACKET_RECEIVED event. Bypasses the scheduler since it
        isn't a future event; still gets dispatched to registered handlers.
        """
        event = Event(event_id=self.event_log.next_event_id(), timestamp=self.clock,
                      event_type=event_type, node_id=node_id, packet_id=packet_id,
                      algorithm=algorithm, metadata=metadata)
        self.event_log.append(event)
        self._dispatch(event)
        return event

    # -- core discrete-event loop ----------------------------------------------

    def step(self) -> bool:
        """
        Process the single earliest pending event: advance the clock to its
        time, log it, apply any built-in network-state effect, and dispatch
        it to registered handlers. Returns False if there was nothing to do.
        """
        popped = self.scheduler.pop_next()
        if popped is None:
            return False
        time, payload = popped
        self.clock = time
        event = Event(
            event_id=self.event_log.next_event_id(),
            timestamp=time,
            event_type=payload["event_type"],
            node_id=payload.get("node_id"),
            packet_id=payload.get("packet_id"),
            algorithm=payload.get("algorithm"),
            metadata=payload.get("metadata", {}),
        )
        self.event_log.append(event)
        self._processed_count += 1
        self._apply_builtin_effects(event)
        self._dispatch(event)
        return True

    def run(self, until: Optional[float] = None, max_events: Optional[int] = None) -> int:
        """
        Process events until the scheduler is empty, or `until` (simulated
        time) is reached, or `max_events` have been processed — whichever
        comes first. Returns the number of events processed in this call.
        """
        processed = 0
        while True:
            if max_events is not None and processed >= max_events:
                break
            next_time = self.scheduler.peek_next_time()
            if next_time is None:
                break
            if until is not None and next_time > until:
                break
            if not self.step():
                break
            processed += 1
        return processed

    def _dispatch(self, event: Event) -> None:
        for handler in self._handlers.get(event.event_type, []):
            handler(event, self)

    def _apply_builtin_effects(self, event: Event) -> None:
        """Network-state mutations needing no routing intelligence."""
        et = event.event_type
        if et == EventType.NODE_FAILED:
            self.network.fail_node(event.node_id)
        elif et == EventType.NODE_RECOVERED:
            self.network.restore_node(event.node_id)
        elif et == EventType.LINK_FAILED:
            self.network.fail_link(event.metadata["node_a"], event.metadata["node_b"])
        elif et == EventType.LINK_RESTORED:
            self.network.restore_link(event.metadata["node_a"], event.metadata["node_b"])

    # -- convenience: network-state scheduling --------------------------------

    def fail_node_at(self, time: float, node_id: str) -> None:
        self.schedule(time, EventType.NODE_FAILED, node_id=node_id)

    def restore_node_at(self, time: float, node_id: str) -> None:
        self.schedule(time, EventType.NODE_RECOVERED, node_id=node_id)

    def fail_link_at(self, time: float, node_a: str, node_b: str) -> None:
        self.schedule(time, EventType.LINK_FAILED, node_a=node_a, node_b=node_b)

    def restore_link_at(self, time: float, node_a: str, node_b: str) -> None:
        self.schedule(time, EventType.LINK_RESTORED, node_a=node_a, node_b=node_b)

    # -- convenience: packet lifecycle -----------------------------------------

    def generate_packet(self, time: float, source: str, destination: str,
                         packet_type: PacketType, message: str = "", ttl: int = 12,
                         max_retransmissions: int = 3) -> Packet:
        """Create a packet and schedule its PACKET_GENERATED event at `time`.

        The event carries packet_type/priority in its metadata (not just the
        Packet object itself) specifically so the Metrics engine (Phase 5)
        can filter delivered/dropped packets by priority using ONLY the
        event log -- never by reaching into Packet state directly."""
        packet = Packet(source=source, destination=destination, packet_type=packet_type,
                         message=message, ttl=ttl, max_retransmissions=max_retransmissions,
                         creation_time=time, packet_id=f"P{next(self._packet_counter):03d}")
        self.packets[packet.packet_id] = packet
        self.schedule(time, EventType.PACKET_GENERATED, node_id=source, packet_id=packet.packet_id,
                      packet_type=packet_type.value, priority=packet_type.priority,
                      destination=destination)
        return packet

    def forward_packet(self, packet_id: str, next_node: str, transmission_delay: float = 0.0,
                        algorithm: Optional[str] = None) -> Packet:
        """
        Advance a packet one hop to `next_node`. Logs PACKET_FORWARDED now.
        If next_node is the destination, the packet becomes DELIVERED
        immediately and PACKET_DELIVERED is logged. Otherwise a
        PACKET_RECEIVED event is scheduled for next_node after
        transmission_delay, so a routing handler can react to its arrival.

        Routing legality (is next_node actually a valid, unvisited, available
        neighbor?) is the routing engine's job, not this helper's — this is
        intentionally a low-level "make it so" primitive.
        """
        packet = self.packets[packet_id]
        from_node = packet.current_node
        packet.mark_forwarded(self.clock, next_node)
        self.log_now(EventType.PACKET_FORWARDED, node_id=from_node, packet_id=packet_id,
                     algorithm=algorithm, to_node=next_node, hop_count=packet.hop_count,
                     ttl_remaining=packet.ttl)
        if packet.is_delivered:
            self.log_now(EventType.PACKET_DELIVERED, node_id=next_node, packet_id=packet_id,
                         algorithm=algorithm, hop_count=packet.hop_count,
                         end_to_end_delay=packet.delivery_time - packet.creation_time)
        else:
            self.schedule(self.clock + transmission_delay, EventType.PACKET_RECEIVED,
                          node_id=next_node, packet_id=packet_id, algorithm=algorithm)
        return packet

    def drop_packet(self, packet_id: str, status: PacketStatus, reason: str = "",
                     algorithm: Optional[str] = None) -> Packet:
        packet = self.packets[packet_id]
        packet.mark_dropped(self.clock, status, reason)
        self.log_now(EventType.PACKET_DROPPED, node_id=packet.current_node, packet_id=packet_id,
                     algorithm=algorithm, status=status.value, reason=reason)
        return packet

    def buffer_packet(self, packet_id: str, reason: str = "No valid forwarding candidate",
                       algorithm: Optional[str] = None) -> Packet:
        packet = self.packets[packet_id]
        packet.mark_buffered(self.clock, reason)
        self.log_now(EventType.PACKET_BUFFERED, node_id=packet.current_node, packet_id=packet_id,
                     algorithm=algorithm, reason=reason)
        return packet

    def unbuffer_packet(self, packet_id: str) -> Packet:
        """Mark a buffered packet IN_TRANSIT again so it can be re-evaluated
        against current network state. No event is logged here — the
        re-evaluation itself will produce its own FORWARDING_DECISION /
        PACKET_FORWARDED / PACKET_BUFFERED event once the routing engine
        decides what happens next (Phase 3/4)."""
        packet = self.packets[packet_id]
        packet.mark_unbuffered()
        return packet

    def attempt_retransmission(self, packet_id: str, algorithm: Optional[str] = None) -> bool:
        """Returns True if another attempt is allowed, False if exhausted."""
        packet = self.packets[packet_id]
        can_retry = packet.mark_retransmission(self.clock)
        self.log_now(EventType.RETRANSMISSION, node_id=packet.current_node, packet_id=packet_id,
                     algorithm=algorithm, attempt=packet.retransmission_count,
                     exhausted=not can_retry)
        return can_retry

    # -- convenience: queue operations -----------------------------------------

    def enqueue_or_overflow(self, node_id: str, packet_id: str,
                             algorithm: Optional[str] = None) -> bool:
        """Attempt to enqueue a packet at node_id's outgoing queue. Logs
        QUEUE_OVERFLOW and returns False if the queue is full."""
        node = self.network.get_node(node_id)
        packet = self.packets[packet_id]
        ok = node.queue.enqueue(packet)
        node.on_queue_changed()
        if not ok:
            self.log_now(EventType.QUEUE_OVERFLOW, node_id=node_id, packet_id=packet_id,
                         algorithm=algorithm, queue_capacity=node.queue.capacity)
        return ok

    def dequeue(self, node_id: str) -> Optional[Packet]:
        node = self.network.get_node(node_id)
        packet = node.queue.dequeue()
        node.on_queue_changed()
        return packet

    # -- convenience: routing-decision visibility (for Mode 1's live log) -----

    def log_neighbor_discovery(self, node_id: str, packet_id: str, neighbor_ids,
                                algorithm: Optional[str] = None) -> Event:
        return self.log_now(EventType.NEIGHBOR_DISCOVERED, node_id=node_id, packet_id=packet_id,
                            algorithm=algorithm, neighbors=list(neighbor_ids))

    def log_candidate_evaluated(self, node_id: str, packet_id: str, candidate_id: str,
                                 score: float, algorithm: Optional[str] = None, **detail) -> Event:
        return self.log_now(EventType.CANDIDATE_EVALUATED, node_id=node_id, packet_id=packet_id,
                            algorithm=algorithm, candidate=candidate_id, score=score, **detail)

    def log_forwarding_decision(self, node_id: str, packet_id: str, selected_node: Optional[str],
                                 algorithm: Optional[str] = None, **detail) -> Event:
        return self.log_now(EventType.FORWARDING_DECISION, node_id=node_id, packet_id=packet_id,
                            algorithm=algorithm, selected=selected_node, **detail)

    def log_route_changed(self, node_id: str, packet_id: str,
                           algorithm: Optional[str] = None, **detail) -> Event:
        return self.log_now(EventType.ROUTE_CHANGED, node_id=node_id, packet_id=packet_id,
                            algorithm=algorithm, **detail)

    def __repr__(self) -> str:  # pragma: no cover
        return (f"SimulationEngine(clock={self.clock:.3f}, "
                f"pending={len(self.scheduler)}, processed={self._processed_count}, "
                f"packets={len(self.packets)})")
