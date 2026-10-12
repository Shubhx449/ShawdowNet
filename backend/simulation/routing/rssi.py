"""
RSSI-based forwarding — the baseline algorithm AEAR is compared against.

Deliberately the simplest possible routing rule: at every hop, forward to
whichever available, unvisited neighbor has the strongest measured RSSI.
No congestion awareness, no hop-efficiency estimate, no priority weighting —
per project constraint (section 11): the RSSI baseline must NOT use AEAR's
score, weights, or congestion factor, so that any performance difference
between AEAR and RSSI is attributable to AEAR's extra factors, not to some
accidental blending of the two algorithms.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from backend.simulation.engine.engine import SimulationEngine
from backend.simulation.events.event import Event, EventType
from backend.simulation.network.network import Network
from backend.simulation.packets.packet import Packet, PacketStatus

ALGORITHM_NAME = "RSSI"


@dataclass
class RSSIConfig:
    hop_transmission_delay_s: float = 0.05  # simulated per-hop transmission time


@dataclass
class RSSICandidate:
    """One evaluated candidate: just its RSSI and whether it was selected."""

    node_id: str
    rssi_dbm: float
    status: str = "EVALUATED"  # or "SELECTED"

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "rssi_dbm": round(self.rssi_dbm, 2),
            "status": self.status,
        }


class RSSIRouter:
    """
    Registers itself against a SimulationEngine and forwards each packet to
    the strongest-signal available neighbor at every hop.

    Usage:
        engine = SimulationEngine(network)
        router = RSSIRouter(network, engine)
        engine.generate_packet(0.0, "N1", "N9", PacketType.NORMAL)
        engine.run()
    """

    def __init__(self, network: Network, engine: SimulationEngine,
                 config: Optional[RSSIConfig] = None):
        self.network = network
        self.engine = engine
        self.config = config or RSSIConfig()
        self.last_candidates: Dict[str, List[RSSICandidate]] = {}

        engine.register_handler(EventType.PACKET_GENERATED, self._on_packet_ready)
        engine.register_handler(EventType.PACKET_RECEIVED, self._on_packet_ready)

    def _on_packet_ready(self, event: Event, engine: SimulationEngine) -> None:
        packet = engine.packets[event.packet_id]
        if packet.is_terminal:
            return
        self._route(packet)

    def _route(self, packet: Packet) -> None:
        node_id = packet.current_node

        if packet.ttl <= 0:
            self.engine.drop_packet(packet.packet_id, PacketStatus.DROPPED_TTL_EXPIRED,
                                     reason="TTL reached 0 before delivery", algorithm=ALGORITHM_NAME)
            return

        neighbors = self.network.available_neighbors_of(node_id)
        neighbor_ids = [n.node_id for n in neighbors]
        self.engine.log_neighbor_discovery(node_id, packet.packet_id, neighbor_ids, algorithm=ALGORITHM_NAME)

        candidate_nodes = [n for n in neighbors if not packet.has_visited(n.node_id)]
        if not candidate_nodes:
            self.engine.buffer_packet(packet.packet_id,
                                       reason="No valid forwarding candidate (all neighbors visited, "
                                              "failed, or unreachable)",
                                       algorithm=ALGORITHM_NAME)
            return

        evaluated = [
            RSSICandidate(node_id=n.node_id,
                          rssi_dbm=self.network.get_link(node_id, n.node_id).rssi_dbm)
            for n in candidate_nodes
        ]
        self.last_candidates[packet.packet_id] = evaluated

        best = max(evaluated, key=lambda c: c.rssi_dbm)
        best.status = "SELECTED"

        for c in evaluated:
            self.engine.log_candidate_evaluated(
                node_id, packet.packet_id, c.node_id, round(c.rssi_dbm, 4),
                algorithm=ALGORITHM_NAME, rssi_dbm=round(c.rssi_dbm, 2), status=c.status,
            )

        self.engine.log_forwarding_decision(
            node_id, packet.packet_id, best.node_id, algorithm=ALGORITHM_NAME,
            rssi_dbm=round(best.rssi_dbm, 2), candidate_count=len(evaluated),
        )

        self.engine.forward_packet(packet.packet_id, best.node_id,
                                    transmission_delay=self.config.hop_transmission_delay_s,
                                    algorithm=ALGORITHM_NAME)

    # -- recovery hook (Phase 4: called by FailureRecoveryManager) ------------

    def retry_buffered_packets(self) -> None:
        """Re-attempt routing for every currently BUFFERED packet. Safe to
        call any time -- RSSI has no in-flight discovery state to protect."""
        for packet in list(self.engine.packets.values()):
            if packet.status.value != "BUFFERED":
                continue
            self.engine.unbuffer_packet(packet.packet_id)
            self._route(packet)
