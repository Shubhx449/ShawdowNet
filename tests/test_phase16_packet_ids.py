"""
Phase 16 -- packet IDs are allocated per engine.

Defect: IDs came from one process-wide counter that the experiment runner and every new Live session reset.
Creating a second engine (starting an experiment, opening another Live tab) made an already-running Live
session reuse IDs, so a new packet silently replaced an earlier in-flight one in that session's packet table.

    python -m pytest tests/test_phase16_packet_ids.py -v
"""

import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.experiments.runner import ExperimentRunner
from backend.live.session import LiveSession
from backend.scenarios.config import ScenarioConfig
from backend.simulation.engine.engine import SimulationEngine
from backend.simulation.packets.packet import PacketType

SC = ScenarioConfig.from_dict({"num_nodes": 15, "base_seed": 7})


def engine_for(seed=7):
    return SimulationEngine(ExperimentRunner(SC)._build_network(seed))


class TestPerEnginePacketIds(unittest.TestCase):
    def test_a_running_live_session_is_not_disturbed_by_another_session(self):
        a = LiveSession(SC, "AEAR")
        first = a.send_packet("N1", "N9", "EMERGENCY")["packet_id"]
        a.send_packet("N2", "N8", "NORMAL")
        a.step(3)
        LiveSession(SC, "AEAR")                      # e.g. a second browser tab
        third = a.send_packet("N3", "N7", "IMPORTANT")["packet_id"]
        self.assertEqual([first, third], ["P001", "P003"])
        a.run_until_idle()
        packets = a.snapshot()["packets"]
        self.assertEqual(sorted(packets), ["P001", "P002", "P003"])
        self.assertEqual(packets["P001"]["type"], "EMERGENCY")  # the original was not overwritten

    def test_a_running_live_session_is_not_disturbed_by_an_experiment(self):
        a = LiveSession(SC, "AEAR")
        a.send_packet("N1", "N9", "EMERGENCY")
        ExperimentRunner(ScenarioConfig.from_dict({"num_nodes": 15, "simulation_duration_s": 5, "num_runs": 1,
                                                   "algorithms": ["RSSI"]})).run(save_raw_events=False)
        self.assertEqual(a.send_packet("N2", "N8", "NORMAL")["packet_id"], "P002")

    def test_each_engine_numbers_its_own_packets_from_one(self):
        e1, e2 = engine_for(), engine_for(8)
        ids = []
        for _ in range(3):
            ids.append((e1.generate_packet(0.0, "N1", "N2", PacketType.NORMAL, ttl=5).packet_id,
                        e2.generate_packet(0.0, "N3", "N4", PacketType.NORMAL, ttl=5).packet_id))
        self.assertEqual(ids, [("P001", "P001"), ("P002", "P002"), ("P003", "P003")])
        self.assertEqual(len(e1.packets), 3)
        self.assertEqual(len(e2.packets), 3)

    def test_engine_reset_restarts_that_engines_numbering_only(self):
        e1, e2 = engine_for(), engine_for()
        for e in (e1, e2):
            e.generate_packet(0.0, "N1", "N2", PacketType.NORMAL, ttl=5)
        e1.reset()
        self.assertEqual(e1.generate_packet(0.0, "N1", "N2", PacketType.NORMAL, ttl=5).packet_id, "P001")
        self.assertEqual(e2.generate_packet(0.0, "N1", "N2", PacketType.NORMAL, ttl=5).packet_id, "P002")

    def test_concurrent_engines_in_threads_never_share_or_lose_ids(self):
        n_threads, per = 8, 400
        engines = [engine_for(i + 1) for i in range(n_threads)]
        barrier = threading.Barrier(n_threads)

        def work(e):
            barrier.wait()
            for _ in range(per):
                e.generate_packet(0.0, "N1", "N2", PacketType.NORMAL, ttl=5)

        threads = [threading.Thread(target=work, args=(e,)) for e in engines]
        [t.start() for t in threads]
        [t.join() for t in threads]
        for e in engines:
            self.assertEqual(sorted(e.packets), [f"P{i:03d}" for i in range(1, per + 1)])

    def test_concurrent_experiments_match_a_solo_run(self):
        sc = ScenarioConfig.from_dict({"num_nodes": 20, "simulation_duration_s": 30, "num_runs": 2, "base_seed": 5})
        key = lambda r: [(x.algorithm, x.run_number, x.event_count, x.metrics.generated_packets, x.metrics.delivered_packets) for x in r.runs]
        solo = key(ExperimentRunner(sc).run(save_raw_events=False))
        out = []
        ts = [threading.Thread(target=lambda: out.append(key(ExperimentRunner(sc).run(save_raw_events=False)))) for _ in range(4)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(len(out), 4)
        for o in out:
            self.assertEqual(o, solo)


if __name__ == "__main__":
    unittest.main()
