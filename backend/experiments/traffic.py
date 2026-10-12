"""
Traffic generation.

Produces a reproducible list of (time, source, destination, packet_type)
tuples from a ScenarioConfig and a seeded RNG -- deliberately independent of
any live Network object, since node IDs are always "N1".."N{num_nodes}"
regardless of position. This means the exact same traffic profile can be
generated once per run and reused unchanged across every algorithm being
compared in that run, which is exactly what the fair-comparison rule needs.

Arrival timing follows a Poisson process (exponential inter-arrival times)
at `traffic_rate_pps`, the standard model for packet arrivals in network
simulation. Packet type is drawn per the scenario's normal/important/
emergency ratios. An optional emergency burst adds extra EMERGENCY packets
uniformly distributed within a configured time window, on top of the
baseline traffic -- matching the paper's default configuration (baseline
traffic plus a distinct emergency burst window).
"""

from __future__ import annotations

import random
from typing import List, Tuple

from backend.scenarios.config import ScenarioConfig
from backend.simulation.packets.packet import PacketType

TrafficEvent = Tuple[float, str, str, PacketType]


def _node_ids(num_nodes: int) -> List[str]:
    return [f"N{i}" for i in range(1, num_nodes + 1)]


def _pick_packet_type(rng: random.Random, config: ScenarioConfig) -> PacketType:
    r = rng.random()
    if r < config.normal_ratio:
        return PacketType.NORMAL
    if r < config.normal_ratio + config.important_ratio:
        return PacketType.IMPORTANT
    return PacketType.EMERGENCY


def _pick_source_destination(rng: random.Random, config: ScenarioConfig,
                              node_ids: List[str]) -> Tuple[str, str]:
    if config.source_node is not None and config.destination_node is not None:
        return config.source_node, config.destination_node
    source = rng.choice(node_ids)
    destination = rng.choice(node_ids)
    while destination == source:
        destination = rng.choice(node_ids)
    return source, destination


def generate_traffic(config: ScenarioConfig, seed: int) -> List[TrafficEvent]:
    """
    Generate the full traffic profile for one run, sorted by time. `seed` is
    the run's own seed (e.g. base_seed + run_number) -- pass the SAME seed
    for every algorithm within a run to get identical traffic across them.
    """
    rng = random.Random(seed)
    node_ids = _node_ids(config.num_nodes)
    events: List[TrafficEvent] = []

    # -- baseline Poisson-process traffic across the whole duration ---------
    t = 0.0
    while True:
        if config.traffic_rate_pps <= 0:
            break
        interarrival = rng.expovariate(config.traffic_rate_pps)
        t += interarrival
        if t > config.simulation_duration_s:
            break
        packet_type = _pick_packet_type(rng, config)
        source, destination = _pick_source_destination(rng, config, node_ids)
        events.append((t, source, destination, packet_type))

    # -- optional emergency burst window -------------------------------------
    for _ in range(config.emergency_burst_count):
        t = rng.uniform(config.emergency_burst_start_s, config.emergency_burst_end_s)
        source, destination = _pick_source_destination(rng, config, node_ids)
        events.append((t, source, destination, PacketType.EMERGENCY))

    events.sort(key=lambda e: e[0])
    return events
