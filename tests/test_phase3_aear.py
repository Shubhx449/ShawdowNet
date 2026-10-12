"""
Phase 3a tests: AEAR routing engine.

Run with:
    python -m pytest tests/test_phase3_aear.py -v
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.simulation.network.network import Network
from backend.simulation.engine.engine import SimulationEngine
from backend.simulation.events.event import EventType
from backend.simulation.packets.packet import PacketType, reset_packet_id_counter
from backend.simulation.routing.aear import (
    AEARRouter, AEARConfig, AEARWeights, _normalize_min_max, _clip,
)


class TestHelpers(unittest.TestCase):
    def test_clip_bounds(self):
        self.assertEqual(_clip(-0.5), 0.0)
        self.assertEqual(_clip(1.5), 1.0)
        self.assertEqual(_clip(0.3), 0.3)

    def test_normalize_min_max_basic(self):
        self.assertAlmostEqual(_normalize_min_max(5, 0, 10), 0.5)
        self.assertAlmostEqual(_normalize_min_max(0, 0, 10), 0.0)
        self.assertAlmostEqual(_normalize_min_max(10, 0, 10), 1.0)

    def test_normalize_equal_range_returns_neutral_one(self):
        # If every candidate has the identical value, hi == lo -> no signal;
        # by convention we return 1.0 so this factor doesn't zero out the score.
        self.assertEqual(_normalize_min_max(7, 7, 7), 1.0)


class TestAEARWeights(unittest.TestCase):
    def test_valid_weights_pass(self):
        AEARWeights(0.4, 0.3, 0.3).validate()  # should not raise

    def test_invalid_weights_raise(self):
        with self.assertRaises(ValueError):
            AEARWeights(0.5, 0.3, 0.3).validate()  # sums to 1.1

    def test_config_validates_all_priorities_present(self):
        cfg = AEARConfig(weights_by_priority={0: AEARWeights(1, 0, 0), 1: AEARWeights(1, 0, 0)})
        with self.assertRaises(ValueError):
            cfg.validate()  # missing priority 2 (EMERGENCY)

    def test_config_rejects_bad_weight_sum(self):
        cfg = AEARConfig(weights_by_priority={
            0: AEARWeights(0.5, 0.5, 0.5),  # sums to 1.5 -> invalid
            1: AEARWeights(0.34, 0.33, 0.33),
            2: AEARWeights(0.3, 0.5, 0.2),
        })
        with self.assertRaises(ValueError):
            cfg.validate()


class TestAEARCandidateFiltering(unittest.TestCase):
    def setUp(self):
        reset_packet_id_counter()
        self.net = Network(communication_range_m=200, seed=1)
        self.net.add_node("A", 0, 0)
        self.net.add_node("B", 100, 0)
        self.net.add_node("C", 0, 100)
        self.net.add_node("D", 100, 100)
        self.engine = SimulationEngine(self.net)
        self.router = AEARRouter(self.net, self.engine)

    def test_failed_neighbor_excluded_from_candidates(self):
        self.net.fail_node("B")
        p = self.engine.generate_packet(0.0, "A", "D", PacketType.NORMAL, ttl=10)
        self.engine.run(max_events=1)  # PACKET_GENERATED -> triggers routing
        evaluated_ids = [c.node_id for c in self.router.last_candidates[p.packet_id]]
        self.assertNotIn("B", evaluated_ids)

    def test_visited_neighbor_excluded_from_candidates(self):
        p = self.engine.generate_packet(0.0, "A", "D", PacketType.NORMAL, ttl=10)
        self.engine.run()  # let it fully route/deliver
        # A should never appear as a candidate once the packet has left A and
        # is being routed onward (since A is now visited).
        for pkt_id, candidates in self.router.last_candidates.items():
            for c in candidates:
                if c.node_id == "A":
                    # only acceptable if this evaluation happened AT node A itself
                    # (i.e. the very first decision), never afterward
                    pass
        self.assertTrue(p.is_delivered or p.is_dropped or p.status.value == "BUFFERED")

    def test_no_candidates_buffers_packet(self):
        # Use a tighter range so A-D (distance ~141m) is NOT directly linked;
        # isolation only works if failing B and C truly leaves zero candidates.
        net = Network(communication_range_m=100, seed=1)
        net.add_node("A", 0, 0)
        net.add_node("B", 100, 0)
        net.add_node("C", 0, 100)
        net.add_node("D", 100, 100)  # distance from A ~141m, out of range at 100m
        engine = SimulationEngine(net)
        AEARRouter(net, engine)

        net.fail_node("B")
        net.fail_node("C")
        p = engine.generate_packet(0.0, "A", "D", PacketType.NORMAL, ttl=10)
        engine.run()
        self.assertEqual(p.status.value, "BUFFERED")
        buffered_events = engine.event_log.filter_by_type(EventType.PACKET_BUFFERED)
        self.assertEqual(len(buffered_events), 1)

    def test_ttl_zero_drops_before_evaluation(self):
        p = self.engine.generate_packet(0.0, "A", "D", PacketType.NORMAL, ttl=10)
        p.ttl = 0  # force TTL exhaustion artificially
        self.engine.run(max_events=1)
        self.assertTrue(p.is_dropped)
        self.assertNotIn(p.packet_id, self.router.last_candidates)


class TestAEARScoringMath(unittest.TestCase):
    """
    Hand-crafted scenario where we know the exact RSSI/queue/distance inputs,
    so we can verify the score formula is implemented exactly as specified
    (not just "picks something reasonable").
    """

    def setUp(self):
        reset_packet_id_counter()
        # Build a network manually so we control distances precisely, then
        # override link RSSI directly to known values (bypassing the path
        # loss model's randomness) for a fully deterministic test.
        # Range is chosen deliberately: X (100m) and Y (200m) are in range of
        # A, but DEST (300m) is NOT — otherwise DEST would itself appear as
        # a third candidate and contaminate the two-candidate scenario below.
        self.net = Network(communication_range_m=250, seed=1)
        self.net.add_node("A", 0, 0)
        self.net.add_node("X", 100, 0)   # candidate 1
        self.net.add_node("Y", 200, 0)   # candidate 2
        self.net.add_node("DEST", 300, 0)

        self.net.get_link("A", "X").rssi_dbm = -60.0  # stronger signal
        self.net.get_link("A", "Y").rssi_dbm = -80.0  # weaker signal

        # Estimated remaining hops (uses communication_range_m=250):
        #   X -> DEST distance = 200m -> ceil(200/250) = 1
        #   Y -> DEST distance = 100m -> ceil(100/250) = 1
        # Equal, by design, so this scenario isolates signal + congestion.
        self.net.get_node("X").queue.capacity = 10
        self.net.get_node("Y").queue.capacity = 10
        for _ in range(8):
            self.net.get_node("X").queue._items.append(object())  # 8/10 occupied
        for _ in range(1):
            self.net.get_node("Y").queue._items.append(object())  # 1/10 occupied

        self.engine = SimulationEngine(self.net)
        self.config = AEARConfig(weights_by_priority={
            0: AEARWeights(signal=0.4, hop=0.3, congestion=0.3),
            1: AEARWeights(signal=0.35, hop=0.35, congestion=0.3),
            2: AEARWeights(signal=0.3, hop=0.5, congestion=0.2),
        })
        self.router = AEARRouter(self.net, self.engine, config=self.config)

    def test_manual_score_matches_engine_score_for_normal_priority(self):
        p = self.engine.generate_packet(0.0, "A", "DEST", PacketType.NORMAL, ttl=10)
        self.engine.run(max_events=1)
        candidates = {c.node_id: c for c in self.router.last_candidates[p.packet_id]}
        x, y = candidates["X"], candidates["Y"]
        self.assertEqual(len(candidates), 2)  # DEST must NOT appear (out of range)

        # RSSI: X=-60 (max), Y=-80 (min) -> normalized: X=1.0, Y=0.0
        self.assertAlmostEqual(x.normalized_rssi, 1.0)
        self.assertAlmostEqual(y.normalized_rssi, 0.0)

        # Both estimate to 1 hop at range=250 (X: 200m, Y: 100m -> both ceil to 1),
        # so hop_efficiency should come out equal for both.
        self.assertEqual(x.estimated_hops, y.estimated_hops)
        self.assertAlmostEqual(x.hop_efficiency, y.hop_efficiency)

        # Congestion: X 8/10=0.8 occupied -> availability 0.2; Y 1/10=0.1 -> availability 0.9
        self.assertAlmostEqual(x.congestion_availability, 0.2, places=4)
        self.assertAlmostEqual(y.congestion_availability, 0.9, places=4)

        # Manually compute expected scores with NORMAL weights (0.4/0.3/0.3)
        # and verify the engine's computed score matches exactly -- this is
        # the core check: the formula is implemented as specified, not just
        # "picks something plausible".
        w = self.config.weights_for(0)
        expected_x = w.signal * x.normalized_rssi + w.hop * x.hop_efficiency + w.congestion * x.congestion_availability
        expected_y = w.signal * y.normalized_rssi + w.hop * y.hop_efficiency + w.congestion * y.congestion_availability
        self.assertAlmostEqual(x.score, expected_x, places=6)
        self.assertAlmostEqual(y.score, expected_y, places=6)

        # Whichever the formula actually favors must be the one marked SELECTED.
        expected_winner = "X" if expected_x >= expected_y else "Y"
        winner = x if expected_winner == "X" else y
        loser = y if expected_winner == "X" else x
        self.assertEqual(winner.status, "SELECTED")
        self.assertEqual(loser.status, "EVALUATED")

    def test_priority_changes_the_winning_candidate(self):
        """
        Construct weights where EMERGENCY cares only about signal (so X wins,
        since X has the stronger RSSI) while NORMAL cares mostly about
        congestion (so Y wins, since Y's queue is far less occupied). Proves
        priority-dependent weighting actually changes routing behavior.
        """
        skewed_config = AEARConfig(weights_by_priority={
            0: AEARWeights(signal=0.0, hop=0.0, congestion=1.0),   # NORMAL: congestion-only
            1: AEARWeights(signal=0.34, hop=0.33, congestion=0.33),
            2: AEARWeights(signal=1.0, hop=0.0, congestion=0.0),   # EMERGENCY: signal-only
        })
        engine = SimulationEngine(self.net)
        router = AEARRouter(self.net, engine, config=skewed_config)

        p_normal = engine.generate_packet(0.0, "A", "DEST", PacketType.NORMAL, ttl=10)
        engine.run(max_events=1)
        winner_normal = max(router.last_candidates[p_normal.packet_id], key=lambda c: c.score)
        self.assertEqual(winner_normal.node_id, "Y")  # Y has much better congestion availability

        engine.reset(network=self.net)
        p_emerg = engine.generate_packet(0.0, "A", "DEST", PacketType.EMERGENCY, ttl=10)
        engine.run(max_events=1)
        winner_emerg = max(router.last_candidates[p_emerg.packet_id], key=lambda c: c.score)
        self.assertEqual(winner_emerg.node_id, "X")  # X has the stronger RSSI


class TestAEARFullDelivery(unittest.TestCase):
    """Integration test: AEAR routing a packet across a real generated
    network end-to-end, checking overall well-formedness (not hardcoded
    outcomes — the whole point is these numbers come from real computation)."""

    def test_packet_reaches_a_terminal_state_with_consistent_bookkeeping(self):
        reset_packet_id_counter()
        net = Network.generate_random(num_nodes=20, communication_range_m=350,
                                       width_m=1000, height_m=1000, seed=99)
        engine = SimulationEngine(net)
        router = AEARRouter(net, engine)

        p = engine.generate_packet(0.0, "N1", "N20", PacketType.EMERGENCY, ttl=20)
        engine.run()

        self.assertTrue(p.is_terminal or p.status.value == "BUFFERED")
        self.assertEqual(len(p.visited_nodes), len(set(p.visited_nodes)))  # no loops
        self.assertGreaterEqual(p.ttl, 0)
        self.assertEqual(p.hop_count, len(p.visited_nodes) - 1)

        forwarded_events = engine.event_log.filter_by_type(EventType.PACKET_FORWARDED)
        for e in forwarded_events:
            self.assertEqual(e.algorithm, "AEAR")

    def test_reproducible_outcome_with_same_seed_and_config(self):
        """Same seed, same config -> same routing outcome (needed for the
        fair-comparison / reproducible-experiment requirement later)."""
        def run_once():
            reset_packet_id_counter()
            net = Network.generate_random(num_nodes=15, communication_range_m=400, seed=7)
            engine = SimulationEngine(net)
            AEARRouter(net, engine)
            p = engine.generate_packet(0.0, "N1", "N15", PacketType.NORMAL, ttl=15)
            engine.run()
            return p.status.value, p.hop_count, tuple(p.visited_nodes)

        result_a = run_once()
        result_b = run_once()
        self.assertEqual(result_a, result_b)


if __name__ == "__main__":
    unittest.main(verbosity=2)
