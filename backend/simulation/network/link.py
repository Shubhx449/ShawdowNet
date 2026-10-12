"""
Link model.

A Link represents the wireless connection between two nodes that are within
communication range of each other. RSSI is derived from a standard
log-distance path-loss model (not measured hardware) so that AEAR's signal
factor and the RSSI baseline algorithm have something realistic to read.

IMPORTANT (per project constraint 13/39): these radio parameters are
explicitly SIMULATION ASSUMPTIONS, not measured LoRa hardware values. They
are kept configurable via PathLossModel so Settings/experiment config can
override them later without touching this module.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from enum import Enum


class LinkStatus(str, Enum):
    ACTIVE = "ACTIVE"
    FAILED = "FAILED"  # manually/experimentally disconnected


@dataclass
class PathLossModel:
    """
    Log-distance path loss model parameters (simulation assumptions).

    RSSI(d) = tx_power_dbm - path_loss_ref_db
              - 10 * path_loss_exponent * log10(d / ref_distance_m)
              + shadowing_noise

    Defaults are plausible for sub-GHz LoRa (868 MHz) outdoor mesh but are
    NOT calibrated against real hardware measurements.
    """

    tx_power_dbm: float = 14.0
    path_loss_ref_db: float = 40.0       # path loss at ref_distance_m
    ref_distance_m: float = 1.0
    path_loss_exponent: float = 2.7      # ~2.7-3.5 typical for outdoor NLOS
    shadowing_std_db: float = 4.0        # gaussian shadowing std deviation

    def rssi_at(self, distance_m: float, rng: random.Random) -> float:
        distance_m = max(distance_m, self.ref_distance_m)
        path_loss = self.path_loss_ref_db + 10 * self.path_loss_exponent * math.log10(
            distance_m / self.ref_distance_m
        )
        shadowing = rng.gauss(0, self.shadowing_std_db)
        return self.tx_power_dbm - path_loss + shadowing


@dataclass
class Link:
    """
    A bidirectional wireless link between two nodes.

    Attributes:
        node_a, node_b: The two endpoint node IDs (order is not meaningful).
        distance_m: Euclidean distance between the nodes, in meters.
        rssi_dbm: Simulated received signal strength, computed once at link
            creation time from distance_m (static topology per run — nodes
            do not move mid-simulation in the current scope).
        status: ACTIVE or FAILED (for manual/experimental link failure).
    """

    node_a: str
    node_b: str
    distance_m: float
    rssi_dbm: float
    status: LinkStatus = LinkStatus.ACTIVE

    @property
    def is_active(self) -> bool:
        return self.status == LinkStatus.ACTIVE

    def fail(self) -> None:
        self.status = LinkStatus.FAILED

    def restore(self) -> None:
        self.status = LinkStatus.ACTIVE

    def other_end(self, node_id: str) -> str:
        """Given one endpoint, return the other."""
        if node_id == self.node_a:
            return self.node_b
        if node_id == self.node_b:
            return self.node_a
        raise ValueError(f"Node {node_id} is not an endpoint of this link")

    def connects(self, node_id: str) -> bool:
        return node_id in (self.node_a, self.node_b)

    def key(self) -> tuple[str, str]:
        """Canonical, order-independent key for dict lookups."""
        return tuple(sorted((self.node_a, self.node_b)))  # type: ignore[return-value]

    def to_dict(self) -> dict:
        return {
            "node_a": self.node_a,
            "node_b": self.node_b,
            "distance_m": round(self.distance_m, 2),
            "rssi_dbm": round(self.rssi_dbm, 2),
            "status": self.status.value,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return (f"Link({self.node_a}<->{self.node_b}, {self.distance_m:.1f}m, "
                f"{self.rssi_dbm:.1f}dBm, {self.status.value})")
