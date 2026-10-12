"""
Phase 8b tests: background experiment execution, status/progress, guard
rails, and malformed-input handling -- at the Store level and end to end over
a real HTTP server.

Run with:
    python -m pytest tests/test_phase8b_async.py -v
"""

import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.api.server import make_handler_class
from backend.api.store import ConflictError, NotFoundError, Store
from backend.experiments.runner import ExperimentRunner
from backend.scenarios.config import ScenarioValidationError


def small_scenario(**overrides):
    base = {"name": "Async Test", "num_nodes": 10, "simulation_duration_s": 10.0,
            "traffic_rate_pps": 1.0, "num_runs": 2, "algorithms": ["AEAR", "RSSI"], "base_seed": 42}
    base.update(overrides)
    return base


def make_gated_runner():
    """A real ExperimentRunner that blocks until released, so a test can hold
    an experiment in the 'running' state deterministically (no sleeps/races)."""
    gate = threading.Event()

    class GatedRunner(ExperimentRunner):
        def run(self, *args, **kwargs):
            gate.wait(15)
            return super().run(*args, **kwargs)

    return GatedRunner, gate


def wait_until(predicate, timeout=15.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class TestAsyncStore(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store = Store(root_dir=self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_async_run_completes_and_results_are_readable(self):
        experiment_id = self.store.start_experiment_async(inline_scenario=small_scenario())
        self.store.wait(experiment_id, timeout=30)
        status = self.store.get_experiment_status(experiment_id)
        self.assertEqual(status["status"], "completed")
        self.assertIsNone(status["error"])
        self.assertIsNotNone(status["started_at"])
        self.assertIsNotNone(status["completed_at"])
        metrics = self.store.get_experiment_metrics(experiment_id)
        self.assertEqual(set(metrics.keys()), {"AEAR", "RSSI"})

    def test_progress_reaches_total_for_every_algorithm(self):
        experiment_id = self.store.start_experiment_async(inline_scenario=small_scenario(num_runs=3))
        self.store.wait(experiment_id, timeout=30)
        progress = self.store.get_experiment_status(experiment_id)["progress"]
        self.assertEqual(set(progress.keys()), {"AEAR", "RSSI"})
        for algorithm, p in progress.items():
            self.assertEqual(p, {"completed": 3, "total": 3}, algorithm)

    def test_results_while_running_raise_conflict_then_succeed(self):
        gated, gate = make_gated_runner()
        with mock.patch("backend.api.store.ExperimentRunner", gated):
            experiment_id = self.store.start_experiment_async(inline_scenario=small_scenario())
            self.assertTrue(wait_until(
                lambda: self.store.get_experiment_status(experiment_id)["status"] == "running"))
            for read in (self.store.get_experiment_metrics, self.store.get_experiment_results,
                         self.store.get_experiment_graph_data, self.store.get_experiment_metadata):
                with self.assertRaises(ConflictError):
                    read(experiment_id)
            gate.set()
            self.store.wait(experiment_id, timeout=30)
        self.assertEqual(self.store.get_experiment_status(experiment_id)["status"], "completed")
        self.assertTrue(self.store.get_experiment_metrics(experiment_id))  # now readable

    def test_saving_status_is_reported_between_finishing_runs_and_completed(self):
        from backend.experiments.storage import ResultStorage
        release = threading.Event()
        real_save = ResultStorage.save

        def slow_save(*args, **kwargs):
            release.wait(15)
            return real_save(*args, **kwargs)

        with mock.patch("backend.api.store.ResultStorage.save", side_effect=slow_save):
            experiment_id = self.store.start_experiment_async(inline_scenario=small_scenario())
            self.assertTrue(wait_until(
                lambda: self.store.get_experiment_status(experiment_id)["status"] == "saving"))
            status = self.store.get_experiment_status(experiment_id)
            # every simulation is done by now -- progress must already read complete
            self.assertTrue(all(p["completed"] == p["total"] for p in status["progress"].values()))
            with self.assertRaises(ConflictError):
                self.store.get_experiment_metrics(experiment_id)  # still not readable
            release.set()
            self.store.wait(experiment_id, timeout=30)
        self.assertEqual(self.store.get_experiment_status(experiment_id)["status"], "completed")

    def test_experiment_left_saving_on_disk_is_also_reported_interrupted(self):
        directory = os.path.join(self.store.experiments_dir, "orphan456")
        os.makedirs(directory)
        with open(os.path.join(directory, "status.json"), "w") as f:
            json.dump({"experiment_id": "orphan456", "status": "saving", "scenario_name": "x",
                       "progress": {}, "error": None}, f)
        self.assertEqual(Store(root_dir=self.tmpdir).get_experiment_status("orphan456")["status"], "failed")

    def test_failure_inside_the_run_becomes_a_failed_status_not_a_dead_thread(self):
        with mock.patch.object(ExperimentRunner, "run", side_effect=RuntimeError("boom")):
            experiment_id = self.store.start_experiment_async(inline_scenario=small_scenario())
            self.store.wait(experiment_id, timeout=30)
        status = self.store.get_experiment_status(experiment_id)
        self.assertEqual(status["status"], "failed")
        self.assertIn("boom", status["error"])
        with self.assertRaises(ConflictError):
            self.store.get_experiment_metrics(experiment_id)

    def test_unknown_experiment_is_not_found(self):
        with self.assertRaises(NotFoundError):
            self.store.get_experiment_status("doesnotexist")

    def test_synchronous_runs_report_completed_status(self):
        experiment_id = self.store.run_experiment(inline_scenario=small_scenario())
        self.assertEqual(self.store.get_experiment_status(experiment_id)["status"], "completed")

    def test_list_includes_running_and_completed_with_status(self):
        done = self.store.run_experiment(inline_scenario=small_scenario())
        gated, gate = make_gated_runner()
        with mock.patch("backend.api.store.ExperimentRunner", gated):
            running = self.store.start_experiment_async(inline_scenario=small_scenario(name="in flight"))
            self.assertTrue(wait_until(
                lambda: self.store.get_experiment_status(running)["status"] == "running"))
            listed = {e["id"]: e for e in self.store.list_experiments()}
            self.assertEqual(listed[done]["status"], "completed")
            self.assertEqual(listed[running]["status"], "running")
            self.assertEqual(listed[running]["name"], "in flight")
            gate.set()
            self.store.wait(running, timeout=30)

    def test_experiment_left_running_on_disk_is_reported_interrupted_after_restart(self):
        directory = os.path.join(self.store.experiments_dir, "orphan123")
        os.makedirs(directory)
        with open(os.path.join(directory, "status.json"), "w") as f:
            json.dump({"experiment_id": "orphan123", "status": "running", "scenario_name": "x",
                       "progress": {}, "error": None}, f)
        fresh_store = Store(root_dir=self.tmpdir)  # simulates a server restart
        status = fresh_store.get_experiment_status("orphan123")
        self.assertEqual(status["status"], "failed")
        self.assertIn("Interrupted", status["error"])

    def test_completed_experiments_survive_a_restart(self):
        experiment_id = self.store.start_experiment_async(inline_scenario=small_scenario())
        self.store.wait(experiment_id, timeout=30)
        fresh_store = Store(root_dir=self.tmpdir)
        self.assertEqual(fresh_store.get_experiment_status(experiment_id)["status"], "completed")
        self.assertTrue(fresh_store.get_experiment_metrics(experiment_id))


class TestGuardRailsAndMalformedInput(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store = Store(root_dir=self.tmpdir, max_runs=5, max_nodes=20, max_duration_s=60.0)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_limits_reject_oversized_scenarios_sync_and_async(self):
        for bad in (small_scenario(num_runs=6), small_scenario(num_nodes=21),
                    small_scenario(simulation_duration_s=61.0)):
            with self.assertRaises(ScenarioValidationError):
                self.store.run_experiment(inline_scenario=bad)
            with self.assertRaises(ScenarioValidationError):
                self.store.start_experiment_async(inline_scenario=bad)

    def test_rejected_scenario_queues_nothing(self):
        with self.assertRaises(ScenarioValidationError):
            self.store.start_experiment_async(inline_scenario=small_scenario(num_runs=999))
        self.assertEqual(self.store.list_experiments(), [])

    def test_limits_apply_to_saved_scenarios_too(self):
        scenario_id = self.store.create_scenario(small_scenario(num_runs=50))  # saving is allowed
        with self.assertRaises(ScenarioValidationError):
            self.store.start_experiment_async(scenario_id=scenario_id)  # running it is not

    def test_malformed_scenarios_are_validation_errors_not_crashes(self):
        malformed = [
            small_scenario(num_nodes="abc"),
            small_scenario(failure_events=[{"time": 1.0}]),          # missing keys
            small_scenario(algorithms="AEAR"),                       # string, not a list
            small_scenario(normal_ratio=None),
        ]
        for bad in malformed:
            with self.assertRaises(ScenarioValidationError, msg=repr(bad)):
                self.store.create_scenario(bad)
            with self.assertRaises(ScenarioValidationError, msg=repr(bad)):
                self.store.start_experiment_async(inline_scenario=bad)

    def test_non_object_scenario_rejected(self):
        with self.assertRaises(ScenarioValidationError):
            self.store.create_scenario(["not", "an", "object"])


class HttpTestBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store = Store(root_dir=self.tmpdir, max_runs=5, max_nodes=20, max_duration_s=60.0)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler_class(self.store))
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def request(self, method, path, body=None, raw_body=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = raw_body if raw_body is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")


class TestHttpEndToEnd(HttpTestBase):
    def test_health_and_defaults(self):
        status, body = self.request("GET", "/api/health")
        self.assertEqual((status, body), (200, {"status": "ok"}))
        status, body = self.request("GET", "/api/defaults")
        self.assertEqual(status, 200)
        self.assertEqual(body["num_nodes"], 30)
        self.assertEqual(body["communication_range_m"], 430.0)
        self.assertEqual(body["algorithms"], ["AEAR", "RSSI", "AODV"])

    def test_async_run_full_lifecycle_over_http(self):
        status, body = self.request("POST", "/api/experiments/run",
                                    {"scenario": small_scenario(), "async": True})
        self.assertEqual(status, 202)
        experiment_id = body["experiment_id"]
        self.assertEqual(body["status_url"], f"/api/experiments/{experiment_id}/status")

        def completed():
            return self.request("GET", body["status_url"])[1]["status"] == "completed"
        self.assertTrue(wait_until(completed, timeout=30))

        status, st = self.request("GET", body["status_url"])
        self.assertEqual(st["progress"]["AEAR"], {"completed": 2, "total": 2})
        for suffix in ("results", "metrics", "graphs", ""):
            status, payload = self.request("GET", f"/api/experiments/{experiment_id}/{suffix}".rstrip("/"))
            self.assertEqual(status, 200, suffix)
        status, graphs = self.request("GET", f"/api/experiments/{experiment_id}/graphs")
        self.assertIn("pdr_percent", graphs["metrics"])

    def test_results_while_running_return_409_over_http(self):
        gated, gate = make_gated_runner()
        with mock.patch("backend.api.store.ExperimentRunner", gated):
            _, body = self.request("POST", "/api/experiments/run",
                                   {"scenario": small_scenario(), "async": True})
            experiment_id = body["experiment_id"]
            self.assertTrue(wait_until(
                lambda: self.request("GET", f"/api/experiments/{experiment_id}/status")[1]["status"] == "running"))
            for suffix in ("results", "metrics", "graphs"):
                status, payload = self.request("GET", f"/api/experiments/{experiment_id}/{suffix}")
                self.assertEqual(status, 409, suffix)
                self.assertIn("running", payload["error"])
            gate.set()
            self.store.wait(experiment_id, timeout=30)
        status, _ = self.request("GET", f"/api/experiments/{experiment_id}/metrics")
        self.assertEqual(status, 200)

    def test_sync_run_still_returns_201_with_metadata(self):
        """Backward compatibility: no 'async' flag means the original behavior."""
        status, body = self.request("POST", "/api/experiments/run", {"scenario": small_scenario()})
        self.assertEqual(status, 201)
        self.assertIn("experiment_id", body)
        self.assertEqual(body["metadata"]["scenario"]["name"], "Async Test")

    def test_unknown_experiment_status_is_404(self):
        status, body = self.request("GET", "/api/experiments/nope/status")
        self.assertEqual(status, 404)

    def test_bad_requests_get_400_not_500(self):
        cases = [
            {"scenario": small_scenario(normal_ratio=0.9)},                 # ratios don't sum to 1
            {"scenario": small_scenario(num_nodes="abc")},                  # wrong type
            {"scenario": small_scenario(num_runs=6)},                       # over the limit
            {"scenario": small_scenario(failure_events=[{"time": 1}])},     # malformed nested
            {},                                                             # nothing to run
        ]
        for body in cases:
            for mode in (False, True):
                status, payload = self.request("POST", "/api/experiments/run", dict(body, **{"async": mode}))
                self.assertEqual(status, 400, f"{body} async={mode} -> {status} {payload}")
                self.assertIn("error", payload)

    def test_non_object_and_malformed_json_bodies_are_400(self):
        status, _ = self.request("POST", "/api/experiments/run", raw_body=b'["a", "list"]')
        self.assertEqual(status, 400)
        status, _ = self.request("POST", "/api/experiments/run", raw_body=b"{not json")
        self.assertEqual(status, 400)

    def test_oversized_body_rejected_with_413_before_reading_it(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.putrequest("POST", "/api/experiments/run")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(5_000_000))
        conn.endheaders()  # declare a huge body but never send it
        response = conn.getresponse()
        self.assertEqual(response.status, 413)
        conn.close()

    def test_cors_headers_present(self):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/health")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.headers["Access-Control-Allow-Origin"], "*")


if __name__ == "__main__":
    unittest.main(verbosity=2)
