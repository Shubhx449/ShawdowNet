"""
AEAR — Adaptive Emergency-Aware Multi-Factor Routing.

A distributed, LOCAL next-hop routing mechanism. At every node holding a
packet, AEAR looks only at its own currently-available neighbors (never a
global route) and scores each one on three normalized factors:

    S_j = signal strength   (normalized RSSI, among current candidates)
    H_j = hop efficiency     (1 - normalized estimated remaining hops)
    C_j = congestion availability (1 - queue occupancy ratio)

    Score(n_j, p) = wS(p)*S_j + wH(p)*H_j + wC(p)*C_j

where the weights depend on the packet's priority p (NORMAL/IMPORTANT/
EMERGENCY) and always sum to 1. The candidate with the highest score is
chosen. This is intentionally O(d) in the number of valid candidates at each
node — never a centralized shortest-path computation (project constraint 39).

This module plugs into SimulationEngine purely via register_handler() on
PACKET_GENERATED / PACKET_RECEIVED — it never modifies engine.py itself.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from backend.simulation.engine.engine import SimulationEngine
from backend.simulation.events.event import Event, EventType
from backend.simulation.network.network import Network
from backend.simulation.packets.packet import Packet, PacketStatus

ALGORITHM_NAME = "AEAR"


def _clip(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _normalize_min_max(value: float, lo: float, hi: float) -> float:
    """
    Min-max normalize `value` into [0, 1] given the (lo, hi) range observed
    among the current candidate set. If every candidate has the same value
    (hi == lo), there is no useful signal to differentiate on, so we return
    a neutral 1.0 rather than dividing by zero — that factor simply stops
    influencing the ranking for this decision (the other factors still can).
    """
    if hi == lo:
        return 1.0
    return _clip((value - lo) / (hi - lo), 0.0, 1.0)


@dataclass
class AEARWeights:
    """Per-priority weight configuration. Must sum to 1.0."""

    signal: float
    hop: float
    congestion: float

    def validate(self) -> None:
        total = self.signal + self.hop + self.congestion
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"AEAR weights must sum to 1.0, got signal={self.signal} + "
                f"hop={self.hop} + congestion={self.congestion} = {total}"
            )

    def to_dict(self) -> dict:
        return {"signal": self.signal, "hop": self.hop, "congestion": self.congestion}


@dataclass
class AEARConfig:
    """
    Configurable AEAR parameters. Defaults below are reasonable starting
    points, NOT values taken from the paper — the paper explicitly treats
    priority-dependent weights as an experimental parameter to be tuned and
    swept (see section 19's "AEAR Weight Sensitivity" graph requirement), so
    these are meant to be overridden per-experiment, not treated as fixed.
    """

    weights_by_priority: Dict[int, AEARWeights] = field(default_factory=lambda: {
        0: AEARWeights(signal=0.40, hop=0.30, congestion=0.30),  # NORMAL
        1: AEARWeights(signal=0.35, hop=0.35, congestion=0.30),  # IMPORTANT
        2: AEARWeights(signal=0.30, hop=0.50, congestion=0.20),  # EMERGENCY
    })
    hop_transmission_delay_s: float = 0.05  # simulated per-hop transmission time

    def validate(self) -> None:
        for priority, weights in self.weights_by_priority.items():
            if priority not in (0, 1, 2):
                raise ValueError(f"Unknown priority level in AEAR config: {priority}")
            weights.validate()
        for required in (0, 1, 2):
            if required not in self.weights_by_priority:
                raise ValueError(f"Missing AEAR weights for priority {required}")

    def weights_for(self, priority: int) -> AEARWeights:
        return self.weights_by_priority[priority]


@dataclass
class AEARCandidate:
    """
    One candidate next hop under evaluation, with raw inputs and the derived
    normalized factors + final score. Kept around (not just the winner) so
    the full evaluation can be logged for Mode 1's decision panel / Developer
    debug mode later.
    """

    node_id: str
    rssi_dbm: float
    distance_to_destination_m: float
    estimated_hops: int
    queue_occupancy: int
    queue_capacity: int

    normalized_rssi: Optional[float] = None
    hop_efficiency: Optional[float] = None
    congestion_availability: Optional[float] = None
    score: Optional[float] = None
    status: str = "EVALUATED"  # or "SELECTED"

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "rssi_dbm": round(self.rssi_dbm, 2),
            "normalized_rssi": round(self.normalized_rssi, 4) if self.normalized_rssi is not None else None,
            "estimated_hops": self.estimated_hops,
            "hop_efficiency": round(self.hop_efficiency, 4) if self.hop_efficiency is not None else None,
            "queue_occupancy": self.queue_occupancy,
            "queue_capacity": self.queue_capacity,
            "congestion_availability": round(self.congestion_availability, 4) if self.congestion_availability is not None else None,
            "score": round(self.score, 4) if self.score is not None else None,
            "status": self.status,
        }


class AEARRouter:
    """
    Registers itself against a SimulationEngine and makes AEAR forwarding
    decisions whenever a packet is generated or arrives at a node.

    Usage:
        engine = SimulationEngine(network)
        router = AEARRouter(network, engine, config=AEARConfig())
        engine.generate_packet(0.0, "N1", "N9", PacketType.EMERGENCY)
        engine.run()
    """

    def __init__(self, network: Network, engine: SimulationEngine,
                 config: Optional[AEARConfig] = None):
        self.network = network
        self.engine = engine
        self.config = config or AEARConfig()
        self.config.validate()
        self.last_candidates: Dict[str, List[AEARCandidate]] = {}  # packet_id -> last evaluation, for UI/debug

        engine.register_handler(EventType.PACKET_GENERATED, self._on_packet_ready)
        engine.register_handler(EventType.PACKET_RECEIVED, self._on_packet_ready)

    # -- event handlers ---------------------------------------------------------

    def _on_packet_ready(self, event: Event, engine: SimulationEngine) -> None:
        packet = engine.packets[event.packet_id]
        if packet.is_terminal:
            return
        self._route(packet)

    # -- core decision logic ----------------------------------------------------

    def _route(self, packet: Packet) -> None:
        node_id = packet.current_node

        if packet.ttl <= 0:
            self.engine.drop_packet(packet.packet_id, PacketStatus.DROPPED_TTL_EXPIRED,
                                     reason="TTL reached 0 before delivery", algorithm=ALGORITHM_NAME)
            return

        neighbors = self.network.available_neighbors_of(node_id)
        neighbor_ids = [n.node_id for n in neighbors]
        self.engine.log_neighbor_discovery(node_id, packet.packet_id, neighbor_ids, algorithm=ALGORITHM_NAME)

        candidates = [n for n in neighbors if not packet.has_visited(n.node_id)]
        if not candidates:
            self.engine.buffer_packet(packet.packet_id,
                                       reason="No valid forwarding candidate (all neighbors visited, "
                                              "failed, or unreachable)",
                                       algorithm=ALGORITHM_NAME)
            return

        evaluated = self._evaluate_candidates(node_id, packet, candidates)
        self.last_candidates[packet.packet_id] = evaluated

        best = max(evaluated, key=lambda c: c.score)
        best.status = "SELECTED"

        for c in evaluated:
            self.engine.log_candidate_evaluated(
                node_id, packet.packet_id, c.node_id, round(c.score, 4),
                algorithm=ALGORITHM_NAME,
                rssi_dbm=round(c.rssi_dbm, 2),
                normalized_rssi=round(c.normalized_rssi, 4),
                estimated_hops=c.estimated_hops,
                hop_efficiency=round(c.hop_efficiency, 4),
                queue_occupancy=c.queue_occupancy,
                queue_capacity=c.queue_capacity,
                congestion_availability=round(c.congestion_availability, 4),
                weights=self.config.weights_for(packet.priority).to_dict(),
                status=c.status,
            )

        self.engine.log_forwarding_decision(
            node_id, packet.packet_id, best.node_id, algorithm=ALGORITHM_NAME,
            score=round(best.score, 4), candidate_count=len(evaluated),
        )

        self.engine.forward_packet(packet.packet_id, best.node_id,
                                    transmission_delay=self.config.hop_transmission_delay_s,
                                    algorithm=ALGORITHM_NAME)

    def _evaluate_candidates(self, node_id: str, packet: Packet,
                              candidate_nodes) -> List[AEARCandidate]:
        destination = self.network.get_node(packet.destination)
        weights = self.config.weights_for(packet.priority)

        raw: List[AEARCandidate] = []
        for n in candidate_nodes:
            link = self.network.get_link(node_id, n.node_id)
            distance_to_dest = n.distance_to(destination)
            estimated_hops = self._estimate_remaining_hops(distance_to_dest)
            raw.append(AEARCandidate(
                node_id=n.node_id,
                rssi_dbm=link.rssi_dbm,
                distance_to_destination_m=distance_to_dest,
                estimated_hops=estimated_hops,
                queue_occupancy=n.queue.size(),
                queue_capacity=n.queue.capacity,
            ))

        rssi_values = [c.rssi_dbm for c in raw]
        r_min, r_max = min(rssi_values), max(rssi_values)
        hop_values = [c.estimated_hops for c in raw]
        h_min, h_max = min(hop_values), max(hop_values)

        for c in raw:
            c.normalized_rssi = _normalize_min_max(c.rssi_dbm, r_min, r_max)
            c.hop_efficiency = 1.0 - _normalize_min_max(c.estimated_hops, h_min, h_max)
            q_max = c.queue_capacity if c.queue_capacity > 0 else 1
            c.congestion_availability = 1.0 - _clip(c.queue_occupancy / q_max, 0.0, 1.0)
            c.score = (weights.signal * c.normalized_rssi
                       + weights.hop * c.hop_efficiency
                       + weights.congestion * c.congestion_availability)
        return raw

    def _estimate_remaining_hops(self, distance_to_destination_m: float) -> int:
        """
        Heuristic remaining-hop estimate: straight-line distance to the
        destination divided by the network's communication range, rounded
        up. A candidate that IS the destination gets 0. This is a LOCAL
        heuristic (the node doesn't know the real path), consistent with
        AEAR being a local, not global, routing decision.
        """
        if distance_to_destination_m <= 0:
            return 0
        return max(1, math.ceil(distance_to_destination_m / self.network.communication_range_m))

    # -- recovery hook (Phase 4: called by FailureRecoveryManager) ------------

    def retry_buffered_packets(self) -> None:
        """
        Re-attempt routing for every currently BUFFERED packet. AEAR has no
        internal in-flight state to worry about (unlike AODV's route
        discovery), so it's always safe to retry every buffered packet here
        — a repeat failure just re-buffers it with the same reason.
        """
        for packet in list(self.engine.packets.values()):
            if packet.status.value != "BUFFERED":
                continue
            self.engine.unbuffer_packet(packet.packet_id)
            self._route(packet)
