"""
Phase 15 -- whole-simulation invariants.

These run full experiments (all three algorithms, several scenarios including
node and link failures) and check properties that must hold in ANY correct run,
computed independently from the raw event stream. They deliberately do not rely
on ValidationEngine's verdict alone, so a bug shared by the metrics code and the
validator can't hide here.

    python -m pytest tests/test_phase15_invariants.py -v
"""

import os
import sys
import unittest
from collections import defaultdict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.experiments.runner import ExperimentRunner
from backend.scenarios.config import ScenarioConfig
from backend.simulation.failures.schedule import FailureEvent

ALGORITHMS = ["AEAR", "RSSI", "AODV"]


def _link_for(base):
    """A real link from the topology run 1 of `base` will use (seed = base_seed + 1)."""
    probe = ExperimentRunner(ScenarioConfig.from_dict({**base, "num_runs": 1}))
    return next(iter(probe._build_network(base["base_seed"] + 1).links.keys()))


def scenarios():
    base = {"num_nodes": 20, "simulation_duration_s": 40, "num_runs": 2, "base_seed": 100}
    link = _link_for(base)
    return {
        "stable": {**base},
        "congestion": {**base, "queue_capacity": 4, "traffic_rate_pps": 6.0},
        "emergency_burst": {**base, "emergency_burst_count": 15, "emergency_burst_start_s": 10, "emergency_burst_end_s": 20},
        "node_failure": {**base, "failure_events": [FailureEvent(5, "fail_node", "N3"), FailureEvent(5, "fail_node", "N7"),
                                                    FailureEvent(25, "restore_node", "N3")]},
        # one run only: the topology (and so which links exist) changes with each run's seed,
        # but a failure schedule is fixed, so the link is only guaranteed to exist for run 1
        "link_failure": {**base, "num_runs": 1, "failure_events": [FailureEvent(5, "fail_link", tuple(link)), FailureEvent(20, "restore_link", tuple(link))]},
    }


def build(cfg):
    raw = dict(cfg)
    events = raw.pop("failure_events", [])
    sc = ScenarioConfig.from_dict(raw)
    sc.failure_events = events
    return sc


_CACHE = {}


def run_scenario(name):
    if name not in _CACHE:
        sc = build(scenarios()[name])
        _CACHE[name] = (sc, ExperimentRunner(sc).run(save_raw_events=True))
    return _CACHE[name]


class TestRunInvariants(unittest.TestCase):
    def each_run(self):
        for name in scenarios():
            sc, result = run_scenario(name)
            for run in result.runs:
                yield name, sc, run

    def label(self, name, run):
        return f"{name}/{run.algorithm}/run{run.run_number}"

    def test_every_run_passes_validation(self):
        for name, sc, run in self.each_run():
            self.assertTrue(run.validation_report.is_valid, f"{self.label(name, run)}: {run.validation_report.summary}")

    def test_event_log_is_ordered(self):
        for name, sc, run in self.each_run():
            ev = run.raw_events
            ids = [e["event_id"] for e in ev]
            self.assertEqual(ids, sorted(set(ids)), self.label(name, run))
            times = [e["timestamp"] for e in ev]
            self.assertEqual(times, sorted(times), f"time went backwards in {self.label(name, run)}")

    def test_a_packet_is_delivered_at_most_once_and_terminal_states_are_final(self):
        terminal = {"PACKET_DELIVERED", "PACKET_DROPPED"}
        for name, sc, run in self.each_run():
            delivered, ended = defaultdict(int), {}
            for e in run.raw_events:
                pid = e["packet_id"]
                if not pid:
                    continue
                if pid in ended and e["event_type"] in ("PACKET_FORWARDED", "PACKET_DELIVERED"):
                    self.fail(f"{self.label(name, run)}: {pid} had {e['event_type']} after it was {ended[pid]}")
                if e["event_type"] == "PACKET_DELIVERED":
                    delivered[pid] += 1
                if e["event_type"] in terminal:
                    ended[pid] = e["event_type"]
            self.assertTrue(all(v == 1 for v in delivered.values()), self.label(name, run))

    def test_nothing_is_forwarded_from_or_to_a_failed_node_or_over_a_failed_link(self):
        for name, sc, run in self.each_run():
            failed_nodes, failed_links = set(), set()
            for e in run.raw_events:
                t, m = e["event_type"], e["metadata"]
                if t == "NODE_FAILED": failed_nodes.add(e["node_id"])
                elif t == "NODE_RECOVERED": failed_nodes.discard(e["node_id"])
                elif t == "LINK_FAILED": failed_links.add(frozenset((m["node_a"], m["node_b"])))
                elif t == "LINK_RESTORED": failed_links.discard(frozenset((m["node_a"], m["node_b"])))
                elif t == "PACKET_FORWARDED":
                    src, dst = e["node_id"], m["to_node"]
                    where = f"{self.label(name, run)} t={e['timestamp']}"
                    self.assertNotIn(src, failed_nodes, f"{where}: forwarded FROM failed {src}")
                    self.assertNotIn(dst, failed_nodes, f"{where}: forwarded TO failed {dst}")
                    self.assertNotIn(frozenset((src, dst)), failed_links, f"{where}: used failed link {src}-{dst}")

    def test_hop_count_and_ttl_are_consistent(self):
        for name, sc, run in self.each_run():
            for e in run.raw_events:
                if e["event_type"] == "PACKET_FORWARDED":
                    m = e["metadata"]
                    self.assertLessEqual(m["hop_count"], sc.ttl, self.label(name, run))
                    self.assertGreaterEqual(m["ttl_remaining"], 0, self.label(name, run))
                    self.assertEqual(m["hop_count"] + m["ttl_remaining"], sc.ttl, self.label(name, run))

    def test_metrics_agree_with_the_raw_events(self):
        for name, sc, run in self.each_run():
            counts = defaultdict(int)
            for e in run.raw_events:
                counts[e["event_type"]] += 1
            m = run.metrics
            lab = self.label(name, run)
            self.assertEqual(m.generated_packets, counts["PACKET_GENERATED"], lab)
            self.assertEqual(m.delivered_packets, counts["PACKET_DELIVERED"], lab)
            self.assertEqual(m.packet_loss, m.generated_packets - m.delivered_packets, lab)
            self.assertLessEqual(m.delivered_packets, m.generated_packets, lab)
            if m.generated_packets:
                self.assertAlmostEqual(m.pdr_percent, 100 * m.delivered_packets / m.generated_packets, places=2, msg=lab)
            self.assertTrue(0 <= m.pdr_percent <= 100, lab)
            self.assertLessEqual(m.emergency_packets_delivered, m.emergency_packets_generated, lab)
            gen_by_type = defaultdict(int)
            for e in run.raw_events:
                if e["event_type"] == "PACKET_GENERATED":
                    gen_by_type[e["metadata"]["packet_type"]] += 1
            for ptype, v in m.pdr_by_priority.items():
                if v is None:  # "not applicable" is only legitimate when nothing of that priority was sent
                    self.assertEqual(gen_by_type[ptype], 0, f"{lab}: PDR for {ptype} is None but packets were generated")
                else:
                    self.assertTrue(0 <= v <= 100, lab)
            if m.delivered_packets:
                self.assertGreaterEqual(m.avg_hop_count, 1, lab)
                self.assertGreaterEqual(m.avg_end_to_end_delay_s, 0, lab)

    def test_all_algorithms_face_identical_traffic_and_topology_in_a_run(self):
        for name in scenarios():
            _, result = run_scenario(name)
            for n in {r.run_number for r in result.runs}:
                gen = {}
                for r in (x for x in result.runs if x.run_number == n):
                    gen[r.algorithm] = [(e["timestamp"], e["node_id"], e["metadata"]["destination"], e["metadata"]["packet_type"])
                                        for e in r.raw_events if e["event_type"] == "PACKET_GENERATED"]
                self.assertEqual(len({tuple(v) for v in gen.values()}), 1, f"{name} run {n}: algorithms saw different traffic")

    def test_runs_are_reproducible_including_failure_scenarios(self):
        for name in ("node_failure", "link_failure"):
            sc, first = run_scenario(name)
            second = ExperimentRunner(sc).run(save_raw_events=True)
            for a, b in zip(first.runs, second.runs):
                self.assertEqual(a.raw_events, b.raw_events, f"{name}/{a.algorithm} not reproducible")

    def test_failures_actually_happen_in_the_failure_scenarios(self):
        """Guards against a scenario silently testing nothing."""
        for name, kind in (("node_failure", "NODE_FAILED"), ("link_failure", "LINK_FAILED")):
            _, result = run_scenario(name)
            for r in result.runs:
                self.assertTrue(any(e["event_type"] == kind for e in r.raw_events), f"{name}/{r.algorithm}")

    def test_congestion_scenario_really_is_congested(self):
        """If the queue were never stressed, the congestion scenario would prove nothing."""
        _, result = run_scenario("congestion")
        self.assertTrue(any(r.metrics.queue_overflow_count > 0 or r.metrics.pdr_percent < 100 for r in result.runs))


if __name__ == "__main__":
    unittest.main()
