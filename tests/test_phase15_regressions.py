"""
Phase 15 -- regression tests for defects found while writing the Phase 15 suite, plus coverage
for previously untested behaviour (failure schedule dispatch, API error paths).

    python -m pytest tests/test_phase15_regressions.py -v
"""

import http.client
import json
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.experiments.runner import ExperimentRunner, SimulationRunaway
from backend.scenarios.config import ScenarioConfig
from backend.simulation.engine.engine import SimulationEngine
from backend.simulation.failures.recovery import FailureRecoveryManager
from backend.simulation.failures.schedule import (FailureEvent, apply_failure_schedule,
                                                  link_disconnection_scenario, node_failure_scenario)
from backend.simulation.packets.packet import PacketStatus, PacketType, reset_packet_id_counter
from tests.test_phase15_invariants import build, scenarios
from tests.test_phase9_live import LiveServerCase


def farthest_from(net, start):
    """(node, hops): the reachable node with the most hops from `start` (BFS), so a route to it spans several nodes."""
    dist, frontier = {start: 0}, [start]
    while frontier:
        nxt = []
        for n in frontier:
            for nb in net.neighbors_of(n):
                if nb.node_id not in dist:
                    dist[nb.node_id] = dist[n] + 1
                    nxt.append(nb.node_id)
        frontier = nxt
    node = max(dist, key=lambda k: (dist[k], k))
    return node, dist[node]


def aodv_world(seed=101, nodes=20):
    reset_packet_id_counter()
    sc = ScenarioConfig.from_dict({"num_nodes": nodes, "base_seed": seed})
    runner = ExperimentRunner(sc)
    net = runner._build_network(seed)
    engine = SimulationEngine(net)
    router = runner._build_router("AODV", net, engine)
    FailureRecoveryManager(engine, [router])
    return sc, net, engine, router


class TestAodvFailedDestination(unittest.TestCase):
    """Defect: AODV only invalidated routes THROUGH a failed node. Routes TO it stayed valid, other
    nodes kept answering route requests for it from cache, and a packet looped through
    buffer -> discover -> instant reply -> 'route leads back' -> buffer at one simulated instant,
    forever (~39,000 iterations then out of memory in the reproducing scenario)."""

    def test_route_error_cascade_clears_a_simple_multi_hop_path(self):
        _, net, engine, router = aodv_world()
        dest, hops = farthest_from(net, "N4")
        self.assertGreaterEqual(hops, 3, "precondition: need a multi-hop route")
        engine.generate_packet(0.0, "N4", dest, PacketType.NORMAL, ttl=12)
        engine.run(max_events=50_000)
        holders = [n for n, t in router.routing_tables.items() if dest in t and t[dest].valid]
        self.assertGreater(len(holders), 1, f"precondition: several nodes should hold a route to {dest}")
        engine.fail_node_at(engine.clock, dest)
        engine.run(max_events=50_000)
        still = [n for n, t in router.routing_tables.items() if dest in t and t[dest].valid]
        self.assertEqual(still, [], "nodes still holding a valid route to the failed node")

    def test_root_cause_is_fixed_so_the_loop_safety_net_is_not_needed(self):
        """Discriminating test for the root-cause fix. Either fix alone stops the hang (measured), but
        with only the safety net, packets get dropped as 'route leads back' -- meaning the stale routes
        are still being handed out. With the root cause fixed, the net never fires in this scenario."""
        cfg = {**scenarios()["node_failure"], "num_runs": 1, "algorithms": ["AODV"]}
        with mock.patch.object(ExperimentRunner, "MAX_EVENTS_PER_RUN", 300_000):
            run = ExperimentRunner(build(cfg)).run(save_raw_events=True).runs[0]
        net_drops = [e for e in run.raw_events if e["event_type"] == "PACKET_DROPPED"
                     and "leads back" in str(e["metadata"].get("reason", ""))]
        self.assertEqual(net_drops, [], "safety net fired: stale routes to failed nodes are still being answered")

    def test_original_reproducer_now_terminates_quickly(self):
        cfg = {**scenarios()["node_failure"], "num_runs": 1, "algorithms": ["AODV"]}
        with mock.patch.object(ExperimentRunner, "MAX_EVENTS_PER_RUN", 300_000):
            t0 = time.time()
            result = ExperimentRunner(build(cfg)).run(save_raw_events=False)
        run = result.runs[0]
        self.assertLess(run.event_count, 100_000)
        self.assertLess(time.time() - t0, 20)
        self.assertTrue(run.validation_report.is_valid)

    def test_zero_time_route_loop_is_bounded_and_drops_the_packet(self):
        """The safety net on its own: even if stale routes reappear, the loop can't run forever."""
        sc, net, engine, router = aodv_world()
        packet = engine.generate_packet(0.0, "N4", "N7", PacketType.NORMAL, ttl=12)
        src = packet.current_node
        # a route whose next hop is a node this packet has already visited (itself)
        router._update_route_entry(src, destination="N7", next_hop=src, hop_count=1, dest_seq_num=1, lifetime=100.0)
        buffered = []
        router._buffer_and_discover = lambda p: buffered.append(p.packet_id)  # what the old code looped through
        for _ in range(20):
            if packet.is_terminal:
                break
            router._route_data_packet(packet)
        self.assertEqual(packet.status, PacketStatus.DROPPED_NO_CANDIDATE)
        self.assertEqual(len(buffered), router.config.max_loop_fallbacks)

    def test_loop_counter_resets_when_simulated_time_advances(self):
        """Legitimate retries spread over time must not accumulate toward a drop."""
        sc, net, engine, router = aodv_world()
        packet = engine.generate_packet(0.0, "N4", "N7", PacketType.NORMAL, ttl=12)
        src = packet.current_node
        router._update_route_entry(src, destination="N7", next_hop=src, hop_count=1, dest_seq_num=1, lifetime=1e9)
        router._buffer_and_discover = lambda p: None
        for i in range(router.config.max_loop_fallbacks * 3):
            engine.clock = float(i)  # time moves between attempts
            router._route_data_packet(packet)
        self.assertFalse(packet.is_terminal)


class TestRunnerRunawayGuard(unittest.TestCase):
    def test_runaway_run_fails_loudly_instead_of_truncating(self):
        sc = ScenarioConfig.from_dict({"num_nodes": 15, "simulation_duration_s": 30, "num_runs": 1})
        with mock.patch.object(ExperimentRunner, "MAX_EVENTS_PER_RUN", 50):
            with self.assertRaises(SimulationRunaway) as cm:
                ExperimentRunner(sc).run(save_raw_events=False)
        self.assertIn("routing loop", str(cm.exception))

    def test_a_run_that_finishes_exactly_at_the_limit_is_not_flagged(self):
        sc = ScenarioConfig.from_dict({"num_nodes": 15, "simulation_duration_s": 10, "num_runs": 1, "algorithms": ["RSSI"]})
        n = ExperimentRunner(sc).run(save_raw_events=False).runs[0].event_count
        with mock.patch.object(ExperimentRunner, "MAX_EVENTS_PER_RUN", n):
            ExperimentRunner(sc).run(save_raw_events=False)  # must not raise: nothing was left unprocessed


class TestFailureSchedule(unittest.TestCase):
    def setUp(self):
        reset_packet_id_counter()
        sc = ScenarioConfig.from_dict({"num_nodes": 15, "base_seed": 7})
        self.net = ExperimentRunner(sc)._build_network(7)
        self.engine = SimulationEngine(self.net)
        self.link = next(iter(self.net.links.keys()))

    def test_all_four_actions_take_effect_at_their_times(self):
        a, b = self.link
        apply_failure_schedule(self.engine, [
            FailureEvent(1, "fail_node", "N3"), FailureEvent(2, "restore_node", "N3"),
            FailureEvent(3, "fail_link", (a, b)), FailureEvent(4, "restore_link", (a, b))])
        for until, node_failed, link_active in ((0.5, False, True), (1.5, True, True), (2.5, False, True),
                                                 (3.5, False, False), (4.5, False, True)):
            self.engine.run(until=until)
            self.assertEqual(self.net.get_node("N3").is_failed, node_failed, f"t={until}")
            self.assertEqual(self.net.get_link(a, b).is_active, link_active, f"t={until}")

    def test_unknown_action_is_rejected(self):
        with self.assertRaises(ValueError):
            apply_failure_schedule(self.engine, [FailureEvent(1, "explode_node", "N3")])

    def test_link_failure_on_a_nonexistent_link_fails_the_run_with_a_clear_error(self):
        """Known limitation, pinned so a change is deliberate: a fixed failure schedule can name a
        link that doesn't exist in a given run's topology; that aborts the run with this message."""
        unlinked = next((a, b) for a in self.net.nodes for b in self.net.nodes if a < b and self.net.get_link(a, b) is None)
        apply_failure_schedule(self.engine, [FailureEvent(1, "fail_link", unlinked)])
        with self.assertRaisesRegex(ValueError, "No link between"):
            self.engine.run(until=2)

    def test_helper_scenarios_build_matching_events(self):
        ev = node_failure_scenario("N3", 5.0, restore_at=9.0)
        self.assertEqual([(e.action, e.time) for e in ev], [("fail_node", 5.0), ("restore_node", 9.0)])
        self.assertEqual(len(node_failure_scenario("N3", 5.0)), 1)
        a, b = self.link
        ev = link_disconnection_scenario(a, b, 2.0, 6.0)
        self.assertEqual([e.action for e in ev], ["fail_link", "restore_link"])


class TestApiErrorPaths(LiveServerCase):
    def call(self, method, path, body=None, raw=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        data = raw if raw is not None else (json.dumps(body) if body is not None else None)
        conn.request(method, "/api" + path, body=data, headers={"Content-Type": "application/json", **(headers or {})})
        r = conn.getresponse()
        text = r.read().decode()
        try:
            payload = json.loads(text) if text else None
        except json.JSONDecodeError:
            payload = text
        return r.status, payload, r

    def defaults(self):
        return self.call("GET", "/defaults")[1]

    def test_unknown_scenario_and_experiment_ids_are_404_everywhere(self):
        for method, path, body in (("GET", "/scenarios/nope", None), ("PUT", "/scenarios/nope", self.defaults()),
                                   ("DELETE", "/scenarios/nope", None),
                                   ("POST", "/experiments/run", {"scenario_id": "nope"}),
                                   ("POST", "/experiments/run", {"scenario_id": "nope", "async": True}),
                                   ("GET", "/experiments/nope", None), ("GET", "/experiments/nope/status", None),
                                   ("GET", "/experiments/nope/results", None), ("GET", "/experiments/nope/metrics", None),
                                   ("GET", "/experiments/nope/graphs", None)):
            status, payload, _ = self.call(method, path, body)
            self.assertEqual(status, 404, f"{method} {path}")
            self.assertIn("error", payload)

    def test_invalid_scenario_update_is_400_and_leaves_the_stored_one_unchanged(self):
        _, created, _ = self.call("POST", "/scenarios", {**self.defaults(), "name": "keep-me"})
        sid = created["id"]
        status, payload, _ = self.call("PUT", f"/scenarios/{sid}", {**self.defaults(), "name": "bad", "normal_ratio": 0.9})
        self.assertEqual(status, 400)
        self.assertIn("ratios", payload["error"].lower())
        self.assertEqual(self.call("GET", f"/scenarios/{sid}")[1]["scenario"]["name"], "keep-me")

    def test_malformed_and_oversized_bodies(self):
        status, payload, _ = self.call("POST", "/scenarios", raw="{not json")
        self.assertEqual(status, 400)
        self.assertIn("JSON", payload["error"])
        # oversized: the server rejects on Content-Length without reading the body, so send headers only
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.putrequest("POST", "/api/scenarios")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", "1100000")
        conn.endheaders()
        self.assertEqual(conn.getresponse().status, 413)

    def test_cors_preflight_and_unknown_routes(self):
        status, _, r = self.call("OPTIONS", "/scenarios")
        self.assertEqual(status, 204)
        self.assertEqual(r.getheader("Access-Control-Allow-Origin"), "*")
        self.assertIn("PUT", r.getheader("Access-Control-Allow-Methods"))
        self.assertEqual(self.call("GET", "/no/such/route")[0], 404)
        self.assertEqual(self.call("PATCH", "/scenarios")[0], 501)  # method the server doesn't implement

    def test_a_runaway_async_experiment_ends_as_failed_with_the_reason(self):
        with mock.patch.object(ExperimentRunner, "MAX_EVENTS_PER_RUN", 50):
            status, payload, _ = self.call("POST", "/experiments/run", {"scenario": {**self.defaults(), "num_runs": 1,
                                            "simulation_duration_s": 20, "num_nodes": 15}, "async": True, "save_raw_events": False})
            self.assertEqual(status, 202)
            eid = payload["experiment_id"]
            for _ in range(100):
                st = self.call("GET", f"/experiments/{eid}/status")[1]
                if st["status"] in ("completed", "failed"):
                    break
                time.sleep(0.1)
        self.assertEqual(st["status"], "failed")
        self.assertIn("routing loop", st["error"])
        self.assertIn(self.call("GET", f"/experiments/{eid}/results")[0], (404, 409))  # no results are served for it


if __name__ == "__main__":
    unittest.main()
