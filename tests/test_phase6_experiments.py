"""
Phase 6 tests: ScenarioConfig, traffic generation, ExperimentRunner, ResultStorage.

Run with:
    python -m pytest tests/test_phase6_experiments.py -v
"""

import sys
import os
import json
import tempfile
import shutil
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.scenarios.config import ScenarioConfig, ScenarioValidationError
from backend.simulation.failures.schedule import FailureEvent, node_failure_scenario
from backend.simulation.packets.packet import PacketType
from backend.experiments.traffic import generate_traffic
from backend.experiments.runner import ExperimentRunner, AGGREGATABLE_METRIC_FIELDS
from backend.experiments.storage import ResultStorage
from backend.experiments.stats import mean_std_ci


class TestScenarioConfigValidation(unittest.TestCase):
    def test_default_config_is_valid(self):
        ScenarioConfig().validate()  # should not raise

    def test_bad_ratio_sum_rejected(self):
        with self.assertRaises(ScenarioValidationError):
            ScenarioConfig(normal_ratio=0.5, important_ratio=0.3, emergency_ratio=0.3).validate()

    def test_zero_runs_rejected(self):
        with self.assertRaises(ScenarioValidationError):
            ScenarioConfig(num_runs=0).validate()

    def test_too_few_nodes_rejected(self):
        with self.assertRaises(ScenarioValidationError):
            ScenarioConfig(num_nodes=1).validate()

    def test_unknown_algorithm_rejected(self):
        with self.assertRaises(ScenarioValidationError):
            ScenarioConfig(algorithms=["AEAR", "DSDV"]).validate()

    def test_empty_algorithms_rejected(self):
        with self.assertRaises(ScenarioValidationError):
            ScenarioConfig(algorithms=[]).validate()

    def test_burst_window_ordering_enforced(self):
        with self.assertRaises(ScenarioValidationError):
            ScenarioConfig(emergency_burst_count=5, emergency_burst_start_s=80,
                           emergency_burst_end_s=60).validate()

    def test_partial_source_destination_rejected(self):
        with self.assertRaises(ScenarioValidationError):
            ScenarioConfig(source_node="N1", destination_node=None).validate()


class TestScenarioConfigSerialization(unittest.TestCase):
    def test_round_trip_preserves_basic_fields(self):
        cfg = ScenarioConfig(name="Test Scenario", num_nodes=15, base_seed=999, num_runs=3)
        restored = ScenarioConfig.from_dict(json.loads(json.dumps(cfg.to_dict())))
        self.assertEqual(restored.name, "Test Scenario")
        self.assertEqual(restored.num_nodes, 15)
        self.assertEqual(restored.base_seed, 999)
        self.assertEqual(restored.num_runs, 3)

    def test_round_trip_preserves_failure_events_with_link_tuple(self):
        cfg = ScenarioConfig(failure_events=[
            FailureEvent(time=1.0, action="fail_link", target=("N1", "N2")),
        ])
        restored = ScenarioConfig.from_dict(json.loads(json.dumps(cfg.to_dict())))
        self.assertEqual(len(restored.failure_events), 1)
        self.assertEqual(restored.failure_events[0].target, ("N1", "N2"))

    def test_round_trip_preserves_aear_weights(self):
        cfg = ScenarioConfig(aear_weights={0: (0.5, 0.3, 0.2), 2: (0.2, 0.6, 0.2)})
        restored = ScenarioConfig.from_dict(json.loads(json.dumps(cfg.to_dict())))
        self.assertEqual(restored.aear_weights[0], (0.5, 0.3, 0.2))
        self.assertEqual(restored.aear_weights[2], (0.2, 0.6, 0.2))


class TestTrafficGeneration(unittest.TestCase):
    def test_reproducible_with_same_seed(self):
        cfg = ScenarioConfig(num_nodes=10, simulation_duration_s=60, traffic_rate_pps=1.0)
        t1 = generate_traffic(cfg, seed=42)
        t2 = generate_traffic(cfg, seed=42)
        self.assertEqual(t1, t2)

    def test_different_seed_gives_different_traffic(self):
        cfg = ScenarioConfig(num_nodes=10, simulation_duration_s=60, traffic_rate_pps=1.0)
        t1 = generate_traffic(cfg, seed=1)
        t2 = generate_traffic(cfg, seed=2)
        self.assertNotEqual(t1, t2)

    def test_all_events_within_simulation_duration(self):
        cfg = ScenarioConfig(num_nodes=10, simulation_duration_s=30, traffic_rate_pps=2.0)
        traffic = generate_traffic(cfg, seed=5)
        self.assertTrue(all(0 <= t <= 30 for t, *_ in traffic))
        self.assertTrue(len(traffic) > 0)

    def test_events_sorted_by_time(self):
        cfg = ScenarioConfig(num_nodes=10, simulation_duration_s=60, traffic_rate_pps=1.5)
        traffic = generate_traffic(cfg, seed=7)
        times = [t for t, *_ in traffic]
        self.assertEqual(times, sorted(times))

    def test_fixed_source_destination_used_when_configured(self):
        cfg = ScenarioConfig(num_nodes=10, simulation_duration_s=30, traffic_rate_pps=1.0,
                             source_node="N1", destination_node="N5")
        traffic = generate_traffic(cfg, seed=3)
        self.assertTrue(all(src == "N1" and dst == "N5" for _, src, dst, _ in traffic))

    def test_random_source_destination_never_equal(self):
        cfg = ScenarioConfig(num_nodes=10, simulation_duration_s=60, traffic_rate_pps=2.0)
        traffic = generate_traffic(cfg, seed=11)
        self.assertTrue(all(src != dst for _, src, dst, _ in traffic))

    def test_packet_type_ratios_roughly_match_over_large_sample(self):
        cfg = ScenarioConfig(num_nodes=10, simulation_duration_s=2000, traffic_rate_pps=1.0,
                             normal_ratio=0.7, important_ratio=0.2, emergency_ratio=0.1)
        traffic = generate_traffic(cfg, seed=13)
        total = len(traffic)
        normal = sum(1 for *_, pt in traffic if pt == PacketType.NORMAL)
        emergency = sum(1 for *_, pt in traffic if pt == PacketType.EMERGENCY)
        self.assertGreater(total, 500)  # sanity: enough samples for a meaningful ratio check
        self.assertAlmostEqual(normal / total, 0.7, delta=0.05)
        self.assertAlmostEqual(emergency / total, 0.1, delta=0.05)

    def test_emergency_burst_adds_extra_packets_in_window(self):
        cfg = ScenarioConfig(num_nodes=10, simulation_duration_s=100, traffic_rate_pps=0.0,
                             emergency_burst_count=20, emergency_burst_start_s=60, emergency_burst_end_s=80)
        traffic = generate_traffic(cfg, seed=17)
        self.assertEqual(len(traffic), 20)
        self.assertTrue(all(pt == PacketType.EMERGENCY for *_, pt in traffic))
        self.assertTrue(all(60 <= t <= 80 for t, *_ in traffic))


class TestExperimentRunnerFairComparison(unittest.TestCase):
    def test_same_run_number_gets_identical_topology_and_traffic_across_algorithms(self):
        scenario = ScenarioConfig(num_nodes=10, simulation_duration_s=20, traffic_rate_pps=1.0,
                                  num_runs=1, algorithms=["AEAR", "RSSI", "AODV"], base_seed=500)
        runner = ExperimentRunner(scenario)
        result = runner.run(save_raw_events=False)

        # All three algorithms in run 1 must have used the same seed.
        seeds = {r.seed for r in result.runs}
        self.assertEqual(seeds, {501})

        # Since topology/traffic are seed-derived and identical, generated
        # packet counts (which depend only on traffic, not algorithm) must match.
        generated_counts = {r.algorithm: r.metrics.generated_packets for r in result.runs}
        self.assertEqual(len(set(generated_counts.values())), 1)

    def test_correct_number_of_run_results_produced(self):
        scenario = ScenarioConfig(num_nodes=8, simulation_duration_s=10, traffic_rate_pps=0.5,
                                  num_runs=3, algorithms=["AEAR", "RSSI"])
        runner = ExperimentRunner(scenario)
        result = runner.run(save_raw_events=False)
        self.assertEqual(len(result.runs), 3 * 2)
        self.assertEqual(len(result.runs_for("AEAR")), 3)
        self.assertEqual(len(result.runs_for("RSSI")), 3)

    def test_reproducible_across_two_full_experiment_runs(self):
        def run_experiment():
            scenario = ScenarioConfig(num_nodes=12, simulation_duration_s=15, traffic_rate_pps=1.0,
                                      num_runs=2, algorithms=["AEAR"], base_seed=77)
            return ExperimentRunner(scenario).run(save_raw_events=False)

        r1 = run_experiment()
        r2 = run_experiment()
        pdrs1 = [r.metrics.pdr_percent for r in r1.runs]
        pdrs2 = [r.metrics.pdr_percent for r in r2.runs]
        self.assertEqual(pdrs1, pdrs2)


class TestExperimentAggregation(unittest.TestCase):
    def test_aggregate_produces_stats_for_every_algorithm(self):
        scenario = ScenarioConfig(num_nodes=10, simulation_duration_s=15, traffic_rate_pps=1.0,
                                  num_runs=4, algorithms=["AEAR", "RSSI"])
        result = ExperimentRunner(scenario).run(save_raw_events=False)
        aggregated = result.aggregate()
        self.assertEqual(set(aggregated.keys()), {"AEAR", "RSSI"})
        for algorithm, metrics in aggregated.items():
            self.assertIn("pdr_percent", metrics)
            self.assertEqual(metrics["pdr_percent"].n, 4)  # 4 runs, all generated some packets presumably

    def test_mean_std_ci_matches_manual_calculation(self):
        agg = mean_std_ci([10.0, 20.0, 30.0])
        self.assertAlmostEqual(agg.mean, 20.0)
        self.assertIsNotNone(agg.std)
        self.assertIsNotNone(agg.ci_95)
        self.assertEqual(agg.n, 3)

    def test_single_value_gives_zero_std_and_no_ci(self):
        agg = mean_std_ci([42.0])
        self.assertEqual(agg.mean, 42.0)
        self.assertEqual(agg.std, 0.0)
        self.assertIsNone(agg.ci_95)

    def test_empty_values_gives_none(self):
        agg = mean_std_ci([])
        self.assertIsNone(agg.mean)
        self.assertEqual(agg.n, 0)


class TestResultStorage(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_save_creates_expected_file_structure(self):
        scenario = ScenarioConfig(num_nodes=8, simulation_duration_s=10, traffic_rate_pps=1.0,
                                  num_runs=2, algorithms=["AEAR", "RSSI"], name="Storage Test")
        result = ExperimentRunner(scenario).run(save_raw_events=True)
        ResultStorage.save(result, self.tmpdir)

        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, "experiment_metadata.json")))
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, "raw", "AEAR", "run_001.json")))
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, "raw", "AEAR", "run_002.json")))
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, "raw", "RSSI", "run_001.json")))
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, "aggregated", "scenario_summary.csv")))
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, "aggregated", "scenario_summary.json")))

    def test_metadata_json_contains_scenario_name(self):
        scenario = ScenarioConfig(num_nodes=8, simulation_duration_s=10, traffic_rate_pps=1.0,
                                  num_runs=1, algorithms=["AEAR"], name="Named Scenario Test")
        result = ExperimentRunner(scenario).run(save_raw_events=False)
        ResultStorage.save(result, self.tmpdir, save_raw_events=False)

        with open(os.path.join(self.tmpdir, "experiment_metadata.json")) as f:
            metadata = json.load(f)
        self.assertEqual(metadata["scenario"]["name"], "Named Scenario Test")
        self.assertEqual(metadata["total_runs"], 1)

    def test_raw_run_json_is_valid_and_has_metrics(self):
        scenario = ScenarioConfig(num_nodes=8, simulation_duration_s=10, traffic_rate_pps=1.0,
                                  num_runs=1, algorithms=["AEAR"])
        result = ExperimentRunner(scenario).run(save_raw_events=True)
        ResultStorage.save(result, self.tmpdir)

        with open(os.path.join(self.tmpdir, "raw", "AEAR", "run_001.json")) as f:
            data = json.load(f)
        self.assertIn("metrics", data)
        self.assertIn("pdr_percent", data["metrics"])
        self.assertIn("events", data)
        self.assertGreater(len(data["events"]), 0)

    def test_save_without_raw_events_omits_events_key_entirely(self):
        scenario = ScenarioConfig(num_nodes=8, simulation_duration_s=10, traffic_rate_pps=1.0,
                                  num_runs=1, algorithms=["AEAR"])
        result = ExperimentRunner(scenario).run(save_raw_events=False)
        ResultStorage.save(result, self.tmpdir, save_raw_events=False)
        with open(os.path.join(self.tmpdir, "raw", "AEAR", "run_001.json")) as f:
            data = json.load(f)
        self.assertNotIn("events", data)  # omitted entirely, not an empty list

    def test_csv_has_expected_header_and_row_count(self):
        scenario = ScenarioConfig(num_nodes=8, simulation_duration_s=10, traffic_rate_pps=1.0,
                                  num_runs=2, algorithms=["AEAR", "RSSI"])
        result = ExperimentRunner(scenario).run(save_raw_events=False)
        ResultStorage.save(result, self.tmpdir, save_raw_events=False)

        with open(os.path.join(self.tmpdir, "aggregated", "scenario_summary.csv")) as f:
            import csv
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 2)  # one row per algorithm
        self.assertIn("pdr_percent_mean", rows[0])
        self.assertIn("pdr_percent_std", rows[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
