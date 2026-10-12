"""
Network model.

The Network owns the set of Nodes and the Links between them. It is
responsible for:
  - building topology (auto-generated or manually placed nodes)
  - deciding which node pairs are linked (within communication range)
  - answering "who are the available neighbors of node X right now"
    (excluding failed nodes and failed links) — this is what every
    routing engine (AEAR/RSSI/AODV) will call as its candidate source.

The Network does NOT know about packets or routing decisions. It is pure
topology + availability state.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from backend.simulation.network.link import Link, LinkStatus, PathLossModel
from backend.simulation.network.node import Node, NodeStatus
from backend.simulation.queues.queue_model import PacketQueue


@dataclass
class Network:
    """
    Attributes:
        width_m, height_m: Simulation area bounds in meters.
        communication_range_m: Max distance for two nodes to be linkable.
        queue_capacity: Default queue capacity assigned to generated nodes.
        seed: Random seed for reproducible auto-generation and RSSI shadowing.
        path_loss_model: Governs simulated RSSI (see link.py).
    """

    width_m: float = 1000.0
    height_m: float = 1000.0
    communication_range_m: float = 430.0
    queue_capacity: int = 30
    seed: int = 12345
    path_loss_model: PathLossModel = field(default_factory=PathLossModel)

    nodes: Dict[str, Node] = field(default_factory=dict, init=False)
    links: Dict[Tuple[str, str], Link] = field(default_factory=dict, init=False)

    def __post_init__(self):
        self._rng = random.Random(self.seed)

    # -- construction ---------------------------------------------------------

    def add_node(self, node_id: str, x: float, y: float) -> Node:
        """Manually place a node. Rebuilds links touching it."""
        if node_id in self.nodes:
            raise ValueError(f"Node {node_id} already exists")
        node = Node(node_id=node_id, x=x, y=y,
                    queue=PacketQueue(capacity=self.queue_capacity))
        self.nodes[node_id] = node
        self._recompute_links_for(node_id)
        return node

    def remove_node(self, node_id: str) -> None:
        self.nodes.pop(node_id, None)
        self.links = {k: v for k, v in self.links.items() if node_id not in k}

    @classmethod
    def generate_random(
        cls,
        num_nodes: int = 30,
        width_m: float = 1000.0,
        height_m: float = 1000.0,
        communication_range_m: float = 430.0,
        queue_capacity: int = 30,
        seed: int = 12345,
        path_loss_model: Optional[PathLossModel] = None,
    ) -> "Network":
        """
        Auto-generate a network with num_nodes uniformly-random positions.

        Uses `seed` for both node placement and RSSI shadowing so the exact
        same topology (and therefore a fair comparison across AEAR/RSSI/AODV)
        can be reproduced by reusing the same seed.
        """
        net = cls(
            width_m=width_m,
            height_m=height_m,
            communication_range_m=communication_range_m,
            queue_capacity=queue_capacity,
            seed=seed,
            path_loss_model=path_loss_model or PathLossModel(),
        )
        for i in range(1, num_nodes + 1):
            x = net._rng.uniform(0, width_m)
            y = net._rng.uniform(0, height_m)
            node_id = f"N{i}"
            node = Node(node_id=node_id, x=x, y=y,
                        queue=PacketQueue(capacity=queue_capacity))
            net.nodes[node_id] = node
        net._build_all_links()
        return net

    def _build_all_links(self) -> None:
        """Recompute every pairwise link from scratch based on current positions."""
        self.links = {}
        node_list = list(self.nodes.values())
        for i in range(len(node_list)):
            for j in range(i + 1, len(node_list)):
                self._maybe_link(node_list[i], node_list[j])

    def _recompute_links_for(self, node_id: str) -> None:
        """After adding/moving a single node, (re)compute just its links."""
        node = self.nodes[node_id]
        for other_id, other in self.nodes.items():
            if other_id == node_id:
                continue
            self._maybe_link(node, other)

    def _maybe_link(self, a: Node, b: Node) -> None:
        distance = a.distance_to(b)
        if distance <= self.communication_range_m:
            rssi = self.path_loss_model.rssi_at(distance, self._rng)
            link = Link(node_a=a.node_id, node_b=b.node_id,
                        distance_m=distance, rssi_dbm=rssi)
            self.links[link.key()] = link

    # -- queries ----------------------------------------------------------------

    def get_node(self, node_id: str) -> Node:
        return self.nodes[node_id]

    def get_link(self, node_a: str, node_b: str) -> Optional[Link]:
        return self.links.get(tuple(sorted((node_a, node_b))))

    def neighbors_of(self, node_id: str) -> List[Node]:
        """All nodes physically in range of node_id, regardless of availability."""
        result = []
        for link in self.links.values():
            if link.connects(node_id):
                result.append(self.nodes[link.other_end(node_id)])
        return result

    def available_neighbors_of(self, node_id: str) -> List[Node]:
        """
        Neighbors usable for forwarding right now: the node itself must be
        available, the link must be ACTIVE, and the neighbor must not have
        failed. This is the single source of truth every routing engine
        should call for candidate discovery.
        """
        if self.nodes[node_id].is_failed:
            return []
        result = []
        for link in self.links.values():
            if not link.connects(node_id) or not link.is_active:
                continue
            neighbor_id = link.other_end(node_id)
            neighbor = self.nodes[neighbor_id]
            if neighbor.is_available:
                result.append(neighbor)
        return result

    def link_between(self, node_a: str, node_b: str) -> Optional[Link]:
        return self.get_link(node_a, node_b)

    # -- failure injection --------------------------------------------------

    def fail_node(self, node_id: str) -> None:
        self.nodes[node_id].fail()

    def restore_node(self, node_id: str) -> None:
        self.nodes[node_id].restore()

    def fail_link(self, node_a: str, node_b: str) -> None:
        link = self.get_link(node_a, node_b)
        if link is None:
            raise ValueError(f"No link between {node_a} and {node_b}")
        link.fail()

    def restore_link(self, node_a: str, node_b: str) -> None:
        link = self.get_link(node_a, node_b)
        if link is None:
            raise ValueError(f"No link between {node_a} and {node_b}")
        link.restore()

    # -- serialization ----------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "width_m": self.width_m,
            "height_m": self.height_m,
            "communication_range_m": self.communication_range_m,
            "seed": self.seed,
            "nodes": [n.to_dict() for n in self.nodes.values()],
            "links": [l.to_dict() for l in self.links.values()],
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"Network({len(self.nodes)} nodes, {len(self.links)} links)"
