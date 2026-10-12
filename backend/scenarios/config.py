"""
ScenarioConfig.

A single, serializable description of everything an experiment needs:
network shape, traffic profile, failure timing, TTL/queue parameters, which
algorithms to compare, and how many independent runs to do. This is the
"same everything except the algorithm" object the fair-comparison
requirement depends on -- one ScenarioConfig, several algorithms, each run
gets byte-identical topology/traffic/failures except for which router class
is doing the forwarding.

This also doubles as the data model Phase 14's Scenario Manager will
persist/CRUD later -- to_dict()/from_dict() are already here so that's just
adding storage around this same shape, not redesigning it.

Defaults below mirror the paper's own default experiment configuration
(30 nodes, 1000x1000m, 430m range, 180s, SF9/etc. radio assumptions already
live in PathLossModel from Phase 1 -- this config controls the simulation
parameters, not the radio physics).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from backend.simulation.failures.schedule import FailureEvent


class ScenarioValidationError(Exception):
    pass


@dataclass
class ScenarioConfig:
    name: str = "Unnamed Scenario"

    # -- network ------------------------------------------------------------
    num_nodes: int = 30
    width_m: float = 1000.0
    height_m: float = 1000.0
    communication_range_m: float = 430.0
    queue_capacity: int = 30

    # -- packet parameters --------------------------------------------------
    ttl: int = 12
    max_retransmissions: int = 3

    # -- traffic (baseline Poisson-ish arrivals over the whole run) ---------
    simulation_duration_s: float = 180.0
    traffic_rate_pps: float = 1.2
    normal_ratio: float = 0.70
    important_ratio: float = 0.20
    emergency_ratio: float = 0.10
    # fixed source/destination (paper's simple case); leave both None for
    # random distinct source/destination pairs per packet instead
    source_node: Optional[str] = None
    destination_node: Optional[str] = None

    # -- optional emergency burst (paper: 45 packets, 60-80s window) --------
    emergency_burst_count: int = 0
    emergency_burst_start_s: float = 0.0
    emergency_burst_end_s: float = 0.0

    # -- failures (identical across every algorithm and, currently, every run) --
    failure_events: List[FailureEvent] = field(default_factory=list)

    # -- experiment control ---------------------------------------------------
    algorithms: List[str] = field(default_factory=lambda: ["AEAR", "RSSI", "AODV"])
    num_runs: int = 10
    base_seed: int = 12345

    # -- optional per-algorithm overrides -------------------------------------
    aear_weights: Optional[Dict[int, Tuple[float, float, float]]] = None  # priority -> (signal, hop, congestion)
    queue_sample_interval_s: Optional[float] = None  # None disables QueueSampler entirely

    def validate(self) -> None:
        ratio_sum = self.normal_ratio + self.important_ratio + self.emergency_ratio
        if abs(ratio_sum - 1.0) > 1e-6:
            raise ScenarioValidationError(
                f"Traffic ratios must sum to 1.0, got {ratio_sum} "
                f"(normal={self.normal_ratio}, important={self.important_ratio}, "
                f"emergency={self.emergency_ratio})"
            )
        if self.num_runs < 1:
            raise ScenarioValidationError("num_runs must be at least 1")
        if self.num_nodes < 2:
            raise ScenarioValidationError("num_nodes must be at least 2")
        if not self.algorithms:
            raise ScenarioValidationError("algorithms list must not be empty")
        valid_algorithms = {"AEAR", "RSSI", "AODV"}
        unknown = set(self.algorithms) - valid_algorithms
        if unknown:
            raise ScenarioValidationError(f"Unknown algorithm(s): {sorted(unknown)}")
        if self.emergency_burst_count > 0 and self.emergency_burst_end_s <= self.emergency_burst_start_s:
            raise ScenarioValidationError("emergency_burst_end_s must be after emergency_burst_start_s")
        if (self.source_node is None) != (self.destination_node is None):
            raise ScenarioValidationError("source_node and destination_node must both be set or both be None")

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "num_nodes": self.num_nodes,
            "width_m": self.width_m,
            "height_m": self.height_m,
            "communication_range_m": self.communication_range_m,
            "queue_capacity": self.queue_capacity,
            "ttl": self.ttl,
            "max_retransmissions": self.max_retransmissions,
            "simulation_duration_s": self.simulation_duration_s,
            "traffic_rate_pps": self.traffic_rate_pps,
            "normal_ratio": self.normal_ratio,
            "important_ratio": self.important_ratio,
            "emergency_ratio": self.emergency_ratio,
            "source_node": self.source_node,
            "destination_node": self.destination_node,
            "emergency_burst_count": self.emergency_burst_count,
            "emergency_burst_start_s": self.emergency_burst_start_s,
            "emergency_burst_end_s": self.emergency_burst_end_s,
            "failure_events": [e.to_dict() for e in self.failure_events],
            "algorithms": list(self.algorithms),
            "num_runs": self.num_runs,
            "base_seed": self.base_seed,
            "aear_weights": self.aear_weights,
            "queue_sample_interval_s": self.queue_sample_interval_s,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ScenarioConfig":
        data = dict(data)  # shallow copy, don't mutate caller's dict
        failure_events_raw = data.pop("failure_events", [])
        failure_events = [
            FailureEvent(time=fe["time"], action=fe["action"],
                         target=tuple(fe["target"]) if isinstance(fe["target"], list) else fe["target"])
            for fe in failure_events_raw
        ]
        aear_weights_raw = data.pop("aear_weights", None)
        aear_weights = None
        if aear_weights_raw is not None:
            aear_weights = {int(k): tuple(v) for k, v in aear_weights_raw.items()}
        known_fields = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(failure_events=failure_events, aear_weights=aear_weights, **filtered)
