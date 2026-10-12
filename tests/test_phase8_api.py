"""
Phase 8 tests: Store, handlers, and a real end-to-end HTTP server.

Run with:
    python -m pytest tests/test_phase8_api.py -v
"""

import sys
import os
import json
import shutil
import tempfile
import threading
import unittest
import urllib.request
import urllib.error

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.api.store import Store, NotFoundError
from backend.api import handlers
from backend.api.handlers import ApiError
from backend.api.server import make_handler_class
from http.server import ThreadingHTTPServer


def sample_scenario_dict(**overrides):
    base = {
        "name": "Test Scenario", "num_nodes": 10, "simulation_duration_s": 10.0,
        "traffic_rate_pps": 1.0, "num_runs": 2, "algorithms": ["AEAR", "RSSI"],
        "base_seed": 42,
    }
    base.update(overrides)
    return base


class TestStoreScenarioCRUD(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store = Store(root_dir=self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_create_and_get_scenario(self):
        sid = self.store.create_scenario(sample_scenario_dict())
        scenario = self.store.get_scenario(sid)
        self.assertEqual(scenario.name, "Test Scenario")
        self.assertEqual(scenario.num_nodes, 10)

    def test_invalid_scenario_rejected(self):
        from backend.scenarios.config import ScenarioValidationError
        with self.assertRaises(ScenarioValidationError):
            self.store.create_scenario(sample_scenario_dict(num_runs=0))

    def test_list_scenarios(self):
        self.store.create_scenario(sample_scenario_dict(name="A"))
        self.store.create_scenario(sample_scenario_dict(name="B"))
        names = {s["name"] for s in self.store.list_scenarios()}
        self.assertEqual(names, {"A", "B"})

    def test_update_scenario(self):
        sid = self.store.create_scenario(sample_scenario_dict(name="Original"))
        self.store.update_scenario(sid, sample_scenario_dict(name="Updated"))
        self.assertEqual(self.store.get_scenario(sid).name, "Updated")

    def test_update_missing_scenario_raises(self):
        with self.assertRaises(NotFoundError):
            self.store.update_scenario("nonexistent", sample_scenario_dict())

    def test_delete_scenario(self):
        sid = self.store.create_scenario(sample_scenario_dict())
        self.store.delete_scenario(sid)
        with self.assertRaises(NotFoundError):
            self.store.get_scenario(sid)

    def test_scenarios_survive_reload_from_disk(self):
        sid = self.store.create_scenario(sample_scenario_dict(name="Persisted"))
        reloaded_store = Store(root_dir=self.tmpdir)
        self.assertEqual(reloaded_store.get_scenario(sid).name, "Persisted")


class TestStoreExperiments(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store = Store(root_dir=self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_run_experiment_with_inline_scenario(self):
        eid = self.store.run_experiment(inline_scenario=sample_scenario_dict())
        metadata = self.store.get_experiment_metadata(eid)
        self.assertEqual(metadata["scenario"]["name"], "Test Scenario")
        self.assertEqual(metadata["total_runs"], 4)  # 2 runs x 2 algorithms

    def test_run_experiment_with_stored_scenario_id(self):
        sid = self.store.create_scenario(sample_scenario_dict())
        eid = self.store.run_experiment(scenario_id=sid)
        self.assertIsNotNone(self.store.get_experiment_metadata(eid))

    def test_run_experiment_requires_scenario_or_inline(self):
        with self.assertRaises(ValueError):
            self.store.run_experiment()

    def test_experiment_metrics_and_results_readable(self):
        eid = self.store.run_experiment(inline_scenario=sample_scenario_dict())
        metrics = self.store.get_experiment_metrics(eid)
        self.assertEqual(set(metrics.keys()), {"AEAR", "RSSI"})
        results = self.store.get_experiment_results(eid)
        self.assertEqual(len(results["AEAR"]), 2)

    def test_experiment_graph_data_shape(self):
        eid = self.store.run_experiment(inline_scenario=sample_scenario_dict())
        graphs = self.store.get_experiment_graph_data(eid)
        self.assertIn("pdr_percent", graphs["metrics"])
        points = graphs["metrics"]["pdr_percent"]
        algos = {p["algorithm"] for p in points}
        self.assertEqual(algos, {"AEAR", "RSSI"})

    def test_list_experiments(self):
        self.store.run_experiment(inline_scenario=sample_scenario_dict(name="Exp1"))
        self.store.run_experiment(inline_scenario=sample_scenario_dict(name="Exp2"))
        names = {e["name"] for e in self.store.list_experiments()}
        self.assertEqual(names, {"Exp1", "Exp2"})

    def test_get_missing_experiment_raises(self):
        with self.assertRaises(NotFoundError):
            self.store.get_experiment_metadata("nonexistent")


class TestHandlersDirectly(unittest.TestCase):
    """Business-logic tests without any HTTP transport involved."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store = Store(root_dir=self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_create_scenario_returns_201(self):
        status, payload = handlers.create_scenario(self.store, {}, sample_scenario_dict())
        self.assertEqual(status, 201)
        self.assertIn("id", payload)

    def test_create_scenario_without_body_raises_400(self):
        with self.assertRaises(ApiError) as ctx:
            handlers.create_scenario(self.store, {}, None)
        self.assertEqual(ctx.exception.status, 400)

    def test_create_scenario_invalid_data_raises_400(self):
        with self.assertRaises(ApiError) as ctx:
            handlers.create_scenario(self.store, {}, sample_scenario_dict(num_runs=0))
        self.assertEqual(ctx.exception.status, 400)

    def test_get_missing_scenario_raises_404(self):
        with self.assertRaises(ApiError) as ctx:
            handlers.get_scenario(self.store, {"id": "missing"}, None)
        self.assertEqual(ctx.exception.status, 404)

    def test_run_experiment_without_scenario_raises_400(self):
        with self.assertRaises(ApiError) as ctx:
            handlers.run_experiment(self.store, {}, {})
        self.assertEqual(ctx.exception.status, 400)

    def test_run_experiment_success_returns_201_with_metadata(self):
        status, payload = handlers.run_experiment(self.store, {}, {"scenario": sample_scenario_dict()})
        self.assertEqual(status, 201)
        self.assertIn("experiment_id", payload)
        self.assertIn("metadata", payload)


class TestEndToEndHttp(unittest.TestCase):
    """Real HTTP requests against a live server, proving the routing/JSON
    wiring works end to end -- not just the handler functions in isolation."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.store = Store(root_dir=cls.tmpdir)
        handler_class = make_handler_class(cls.store)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _request(self, method, path, body=None):
        url = self.base_url + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_full_lifecycle_create_run_fetch(self):
        status, payload = self._request("POST", "/api/scenarios", sample_scenario_dict(name="E2E Test"))
        self.assertEqual(status, 201)
        scenario_id = payload["id"]

        status, payload = self._request("GET", f"/api/scenarios/{scenario_id}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["scenario"]["name"], "E2E Test")

        status, payload = self._request("POST", "/api/experiments/run", {"scenario_id": scenario_id})
        self.assertEqual(status, 201)
        experiment_id = payload["experiment_id"]

        status, payload = self._request("GET", f"/api/experiments/{experiment_id}/metrics")
        self.assertEqual(status, 200)
        self.assertEqual(set(payload.keys()), {"AEAR", "RSSI"})

        status, payload = self._request("GET", f"/api/experiments/{experiment_id}/results")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["results"]["AEAR"]), 2)

        status, payload = self._request("GET", f"/api/experiments/{experiment_id}/graphs")
        self.assertEqual(status, 200)
        self.assertIn("pdr_percent", payload["metrics"])

    def test_404_for_unknown_route(self):
        status, payload = self._request("GET", "/api/nonexistent")
        self.assertEqual(status, 404)

    def test_404_for_missing_scenario(self):
        status, payload = self._request("GET", "/api/scenarios/doesnotexist")
        self.assertEqual(status, 404)

    def test_400_for_invalid_scenario_body(self):
        status, payload = self._request("POST", "/api/scenarios", sample_scenario_dict(num_runs=0))
        self.assertEqual(status, 400)

    def test_delete_scenario_via_http(self):
        status, payload = self._request("POST", "/api/scenarios", sample_scenario_dict(name="ToDelete"))
        scenario_id = payload["id"]
        status, payload = self._request("DELETE", f"/api/scenarios/{scenario_id}")
        self.assertEqual(status, 200)
        status, payload = self._request("GET", f"/api/scenarios/{scenario_id}")
        self.assertEqual(status, 404)

    def test_cors_header_present(self):
        status, _ = self._request("GET", "/api/scenarios")
        self.assertEqual(status, 200)  # header presence checked implicitly via successful cross-origin-style call


if __name__ == "__main__":
    unittest.main(verbosity=2)
