"""
ExperimentRunner.

Runs a ScenarioConfig across every configured algorithm, `num_runs` times
each, with the fair-comparison guarantee enforced structurally: for a given
run number, the network topology (same seed), the traffic profile (same
seed, generated independently of any live Network), and the failure
schedule are IDENTICAL across every algorithm -- only the router class
differs. Each algorithm gets its own freshly-generated Network per run
(never a mutated leftover from a previous run/algorithm), per the caveat
documented back in Phase 2/3: reusing a Network whose nodes were failed by
a previous run would silently break the next one.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List

from backend.scenarios.config import ScenarioConfig
from backend.simulation.network.network import Network
from backend.simulation.engine.engine import SimulationEngine
from backend.simulation.routing.aear import AEARRouter, AEARConfig, AEARWeights
from backend.simulation.routing.rssi import RSSIRouter
from backend.simulation.routing.aodv import AODVRouter
from backend.simulation.failures.recovery import FailureRecoveryManager
from backend.simulation.failures.schedule import apply_failure_schedule
from backend.simulation.metrics.engine import MetricsEngine, MetricsResult
from backend.simulation.metrics.sampler import QueueSampler
from backend.validation.engine import ValidationEngine
from backend.validation.issues import ValidationReport
from backend.experiments.traffic import generate_traffic
from backend.experiments.stats import mean_std_ci, AggregatedMetric

# Every numeric MetricsResult field worth aggregating across runs. Excludes
# pdr_by_priority / avg_delay_by_priority / notes (not plain numbers).
AGGREGATABLE_METRIC_FIELDS = [
    "generated_packets", "delivered_packets", "pdr_percent",
    "avg_end_to_end_delay_s", "avg_hop_count", "packet_loss", "packet_loss_rate_percent",
    "throughput_packets_per_s",
    "emergency_packets_generated", "emergency_packets_delivered",
    "emergency_pdr_percent", "avg_emergency_delay_s",
    "routing_overhead_control_messages",
    "recovery_events_count", "avg_recovery_time_s",
    "duplicate_forward_count", "duplicate_packet_rate_percent",
    "queue_overflow_count", "avg_queue_length", "max_queue_length", "avg_queue_utilization_percent",
    "forwarding_decisions", "successful_forwarding_attempts", "failed_forwarding_attempts",
    "retransmissions", "packets_buffered_events", "unique_packets_buffered",
    "successfully_recovered_buffered_packets",
]


@dataclass
class RunResult:
    algorithm: str
    run_number: int
    seed: int
    metrics: MetricsResult
    event_count: int
    validation_report: ValidationReport = field(default_factory=ValidationReport)
    raw_events: List[dict] = field(default_factory=list)  # populated only if requested

    def to_dict(self, include_events: bool = True) -> dict:
        d = {
            "algorithm": self.algorithm,
            "run_number": self.run_number,
            "seed": self.seed,
            "event_count": self.event_count,
            "metrics": self.metrics.to_dict(),
            "validation": self.validation_report.to_dict(),
        }
        if include_events:
            d["events"] = self.raw_events
        return d


@dataclass
class ExperimentResult:
    scenario: ScenarioConfig
    runs: List[RunResult]

    def runs_for(self, algorithm: str) -> List[RunResult]:
        return [r for r in self.runs if r.algorithm == algorithm]

    def aggregate(self) -> Dict[str, Dict[str, AggregatedMetric]]:
        by_algorithm: Dict[str, List[RunResult]] = defaultdict(list)
        for r in self.runs:
            by_algorithm[r.algorithm].append(r)

        aggregated: Dict[str, Dict[str, AggregatedMetric]] = {}
        for algorithm, run_list in by_algorithm.items():
            per_metric: Dict[str, AggregatedMetric] = {}
            for field_name in AGGREGATABLE_METRIC_FIELDS:
                values = [getattr(r.metrics, field_name) for r in run_list
                          if getattr(r.metrics, field_name) is not None]
                per_metric[field_name] = mean_std_ci(values)
            aggregated[algorithm] = per_metric
        return aggregated


class SimulationRunaway(RuntimeError):
    """A single run processed far more events than any legitimate scenario needs (a routing loop)."""


class ExperimentRunner:
    # Per-run event budget. Legitimate runs are orders of magnitude below this; hitting it means a
    # runaway loop, and we fail loudly rather than silently truncate results or exhaust memory.
    MAX_EVENTS_PER_RUN = 2_000_000
    ROUTER_CLASSES = {"AEAR": AEARRouter, "RSSI": RSSIRouter, "AODV": AODVRouter}

    def __init__(self, scenario: ScenarioConfig):
        scenario.validate()
        self.scenario = scenario

    def run(self, save_raw_events: bool = True, progress_callback=None) -> ExperimentResult:
        """
        progress_callback, if given, is called after every completed
        (algorithm, run) pair as progress_callback(algorithm, completed, total)
        where completed/total count runs for THAT algorithm -- enough for a UI
        to draw one progress bar per algorithm.
        """
        results: List[RunResult] = []
        completed_per_algorithm: Dict[str, int] = defaultdict(int)
        for run_number in range(1, self.scenario.num_runs + 1):
            run_seed = self.scenario.base_seed + run_number
            traffic = generate_traffic(self.scenario, seed=run_seed)

            for algorithm in self.scenario.algorithms:
                net = self._build_network(run_seed)
                engine = SimulationEngine(net)
                router = self._build_router(algorithm, net, engine)
                FailureRecoveryManager(engine, [router])
                if self.scenario.queue_sample_interval_s:
                    QueueSampler(engine, net, interval_s=self.scenario.queue_sample_interval_s,
                                 end_time=self.scenario.simulation_duration_s)
                apply_failure_schedule(engine, self.scenario.failure_events)

                for time, source, destination, packet_type in traffic:
                    engine.generate_packet(time, source, destination, packet_type,
                                           ttl=self.scenario.ttl,
                                           max_retransmissions=self.scenario.max_retransmissions)

                processed = engine.run(until=self.scenario.simulation_duration_s, max_events=self.MAX_EVENTS_PER_RUN)
                next_t = engine.scheduler.peek_next_time()
                if processed >= self.MAX_EVENTS_PER_RUN and next_t is not None and next_t <= self.scenario.simulation_duration_s:
                    raise SimulationRunaway(
                        f"{algorithm} run {run_number} processed {processed:,} events by simulated t={engine.clock:.2f}s "
                        f"and was still going: aborted as a probable routing loop. No results were kept for this experiment.")
                metrics = MetricsEngine.compute(engine.event_log,
                                                simulation_duration_s=self.scenario.simulation_duration_s)
                validation_report = ValidationEngine.validate(
                    engine.event_log, metrics=metrics,
                    expected_duration_s=self.scenario.simulation_duration_s,
                )

                results.append(RunResult(
                    algorithm=algorithm, run_number=run_number, seed=run_seed,
                    metrics=metrics, event_count=engine.event_log.count(),
                    validation_report=validation_report,
                    raw_events=engine.event_log.to_list_of_dicts() if save_raw_events else [],
                ))
                completed_per_algorithm[algorithm] += 1
                if progress_callback is not None:
                    progress_callback(algorithm, completed_per_algorithm[algorithm],
                                      self.scenario.num_runs)

        return ExperimentResult(scenario=self.scenario, runs=results)

    def _build_network(self, seed: int) -> Network:
        return Network.generate_random(
            num_nodes=self.scenario.num_nodes, width_m=self.scenario.width_m,
            height_m=self.scenario.height_m, communication_range_m=self.scenario.communication_range_m,
            queue_capacity=self.scenario.queue_capacity, seed=seed,
        )

    def _build_router(self, algorithm: str, net: Network, engine: SimulationEngine):
        if algorithm == "AEAR":
            config = None
            if self.scenario.aear_weights:
                weights = {
                    priority: AEARWeights(signal=w[0], hop=w[1], congestion=w[2])
                    for priority, w in self.scenario.aear_weights.items()
                }
                config = AEARConfig(weights_by_priority=weights)
            return AEARRouter(net, engine, config=config)
        if algorithm == "RSSI":
            return RSSIRouter(net, engine)
        if algorithm == "AODV":
            return AODVRouter(net, engine)
        raise ValueError(f"Unknown algorithm: {algorithm}")  # scenario.validate() should prevent this
