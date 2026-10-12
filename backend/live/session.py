"""
LiveSession -- one interactive Mode 1 simulation.

Wraps exactly one Network + SimulationEngine + one routing algorithm and
exposes the operations a live UI needs: send a packet, inject/clear a
failure, step or advance time, and read back the events produced since the
last read plus a compact state snapshot.

Nothing here knows about sockets or JSON transport -- that's backend/ws/.
Phase 10's communication adapter will sit around this same surface, so a
future LoRa adapter can offer the same operations.

Construction deliberately reuses ExperimentRunner's network/router builders,
so a live session with scenario X and seed S has the SAME topology and
router configuration as run 1 of a Mode 2 experiment on X.

Time model: the engine's clock only moves when an event is processed. The
session keeps a separate `playhead` -- how far simulated time has been
advanced, even through idle stretches. New actions (a packet, a failure) are
scheduled AT the playhead, which is always >= the engine clock, so
"do this now" is always legal.
"""

from __future__ import annotations

import threading
from typing import Dict, List, Optional

from backend.experiments.runner import ExperimentRunner
from backend.scenarios.config import ScenarioConfig
from backend.simulation.engine.engine import SimulationEngine
from backend.simulation.failures.recovery import FailureRecoveryManager
from backend.simulation.packets.packet import PacketType

MAX_LIVE_NODES = 100
MAX_EVENTS_PER_ADVANCE = 5000  # runaway guard for a single advance()/run call


class SessionError(Exception):
    """A bad command (unknown node, wrong state...). Reported to the client, never fatal."""


class LiveSession:
    def __init__(self, scenario: ScenarioConfig, algorithm: str = "AEAR"):
        if algorithm not in ExperimentRunner.ROUTER_CLASSES:
            raise SessionError(f"Unknown algorithm: {algorithm!r}")
        scenario.validate()
        if scenario.num_nodes > MAX_LIVE_NODES:
            raise SessionError(f"Live sessions are limited to {MAX_LIVE_NODES} nodes")
        self.scenario = scenario
        self.algorithm = algorithm
        self.lock = threading.RLock()

        runner = ExperimentRunner(scenario)
        self.network = runner._build_network(scenario.base_seed)
        self.engine = SimulationEngine(self.network)
        self.router = runner._build_router(algorithm, self.network, self.engine)
        FailureRecoveryManager(self.engine, [self.router])

        self.playhead: float = 0.0
        self._cursor: int = 0  # index into event_log of the next unsent event

    # -- helpers ---------------------------------------------------------------

    def _node(self, node_id: str):
        if not isinstance(node_id, str) or node_id not in self.network.nodes:
            raise SessionError(f"Unknown node: {node_id!r}")
        return self.network.nodes[node_id]

    def _link(self, a: str, b: str):
        self._node(a)
        self._node(b)
        link = self.network.get_link(a, b)
        if link is None:
            raise SessionError(f"No link between {a} and {b} (out of range)")
        return link

    # -- actions ---------------------------------------------------------------

    def send_packet(self, source: str, destination: str, packet_type: str = "NORMAL",
                    ttl: Optional[int] = None, message: str = "") -> dict:
        with self.lock:
            src = self._node(source)
            self._node(destination)
            if source == destination:
                raise SessionError("Source and destination must differ")
            if src.is_failed:
                raise SessionError(f"Source node {source} is failed and cannot transmit")
            try:
                ptype = PacketType(str(packet_type).upper())
            except ValueError:
                raise SessionError(f"Unknown packet_type: {packet_type!r} "
                                   f"(expected NORMAL, IMPORTANT or EMERGENCY)")
            ttl = self.scenario.ttl if ttl is None else ttl
            if not isinstance(ttl, int) or isinstance(ttl, bool) or not (1 <= ttl <= 64):
                raise SessionError("ttl must be an integer between 1 and 64")
            packet = self.engine.generate_packet(
                self.playhead, source, destination, ptype, message=str(message)[:200],
                ttl=ttl, max_retransmissions=self.scenario.max_retransmissions)
            return {"packet_id": packet.packet_id, "scheduled_at": self.playhead}

    def fail_node(self, node_id: str) -> None:
        with self.lock:
            if self._node(node_id).is_failed:
                raise SessionError(f"Node {node_id} is already failed")
            self.engine.fail_node_at(self.playhead, node_id)

    def restore_node(self, node_id: str) -> None:
        with self.lock:
            if not self._node(node_id).is_failed:
                raise SessionError(f"Node {node_id} is not failed")
            self.engine.restore_node_at(self.playhead, node_id)

    def fail_link(self, node_a: str, node_b: str) -> None:
        with self.lock:
            if not self._link(node_a, node_b).is_active:
                raise SessionError(f"Link {node_a}-{node_b} is already failed")
            self.engine.fail_link_at(self.playhead, node_a, node_b)

    def restore_link(self, node_a: str, node_b: str) -> None:
        with self.lock:
            if self._link(node_a, node_b).is_active:
                raise SessionError(f"Link {node_a}-{node_b} is not failed")
            self.engine.restore_link_at(self.playhead, node_a, node_b)

    # -- time ------------------------------------------------------------------

    def step(self, count: int = 1) -> int:
        """Process up to `count` events. Returns how many were processed."""
        with self.lock:
            count = max(1, min(int(count), MAX_EVENTS_PER_ADVANCE))
            done = 0
            for _ in range(count):
                if not self.engine.step():
                    break
                done += 1
            self.playhead = max(self.playhead, self.engine.clock)
            return done

    def advance(self, dt: float) -> int:
        """Advance simulated time by `dt` seconds, processing everything due."""
        with self.lock:
            if dt < 0:
                raise SessionError("dt must be >= 0")
            target = self.playhead + dt
            processed = self.engine.run(until=target, max_events=MAX_EVENTS_PER_ADVANCE)
            # if the runaway guard tripped, don't skip past unprocessed events
            next_t = self.engine.scheduler.peek_next_time()
            self.playhead = target if (next_t is None or next_t > target) else max(self.playhead, self.engine.clock)
            return processed

    def run_until_idle(self, max_events: int = MAX_EVENTS_PER_ADVANCE) -> int:
        """Process pending events until none remain (test/demo convenience)."""
        with self.lock:
            n = self.engine.run(max_events=max_events)
            self.playhead = max(self.playhead, self.engine.clock)
            return n

    @property
    def has_pending(self) -> bool:
        return self.engine.scheduler.peek_next_time() is not None

    # -- reading state ---------------------------------------------------------

    def drain_events(self) -> List[dict]:
        """Events logged since the previous drain, in log order."""
        with self.lock:
            events = self.engine.event_log.all()
            new = events[self._cursor:]
            self._cursor = len(events)
            return [e.to_dict() for e in new]

    def network_dict(self) -> dict:
        return self.network.to_dict()

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "algorithm": self.algorithm,
                "clock": self.engine.clock,
                "playhead": self.playhead,
                "pending_events": len(self.engine.scheduler),
                "nodes": {nid: {"status": n.status.value, "queue": n.queue.size(),
                                "capacity": n.queue.capacity, "failed": n.is_failed}
                          for nid, n in self.network.nodes.items()},
                "failed_links": [list(k) for k, l in self.network.links.items() if not l.is_active],
                "packets": {pid: {"status": p.status.value, "type": p.packet_type.value,
                                  "source": p.source, "destination": p.destination,
                                  "current_node": p.current_node, "hop_count": p.hop_count,
                                  "ttl": p.ttl, "path": list(p.visited_nodes)}
                            for pid, p in self.engine.packets.items()},
            }
