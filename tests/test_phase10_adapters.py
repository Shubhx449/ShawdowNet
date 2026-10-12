"""
Phase 10 tests: the adapter interface, the simulator adapter, the LoRa stub,
the registry, and adapter selection over the WebSocket + REST APIs.

Run with:
    python -m pytest tests/test_phase10_adapters.py -v
"""

import http.client
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.adapters.base import (AdapterCapabilities, AdapterError, AdapterUnavailable,
                                   CommunicationAdapter, UnsupportedOperation)
from backend.adapters.lora import LoRaAdapter
from backend.adapters.registry import create_adapter, list_adapters, register_adapter, _ADAPTERS
from backend.adapters.simulator import SimulatorAdapter
from backend.live.session import LiveSession
from backend.scenarios.config import ScenarioConfig
from tests.test_phase9_live import LiveServerCase


def sc(**kw):
    base = {"num_nodes": 15, "base_seed": 7}
    base.update(kw)
    return ScenarioConfig.from_dict(base)


class TestInterface(unittest.TestCase):
    def test_base_class_cannot_be_instantiated(self):
        with self.assertRaises(TypeError):
            CommunicationAdapter()

    def test_incomplete_subclass_cannot_be_instantiated(self):
        class Half(CommunicationAdapter):
            def network_dict(self): return {}
        with self.assertRaises(TypeError):
            Half()

    def test_every_abstract_method_is_implemented_by_the_simulator_adapter(self):
        self.assertEqual(SimulatorAdapter.__abstractmethods__, frozenset())

    def test_error_hierarchy(self):
        self.assertTrue(issubclass(AdapterUnavailable, AdapterError))
        self.assertTrue(issubclass(UnsupportedOperation, AdapterError))


class TestSimulatorAdapter(unittest.TestCase):
    def test_produces_the_same_events_as_a_raw_live_session(self):
        a = SimulatorAdapter(sc(), "AEAR")
        a.send_packet("N1", "N9", "EMERGENCY")
        a.step(10_000)
        raw = LiveSession(sc(), "AEAR")
        raw.send_packet("N1", "N9", "EMERGENCY")
        raw.run_until_idle()
        self.assertEqual(a.drain_events(), raw.drain_events())

    def test_capabilities_are_honest(self):
        c = SimulatorAdapter.capabilities
        self.assertTrue(c.simulated_time and c.failure_injection and c.decision_trace)
        self.assertEqual(set(c.algorithms), {"AEAR", "RSSI", "AODV"})

    def test_errors_surface_as_adapter_error_only(self):
        a = SimulatorAdapter(sc(), "AEAR")
        for fn in (lambda: a.send_packet("N1", "N1"), lambda: a.fail_node("NOPE"),
                   lambda: a.restore_node("N1"), lambda: a.advance(-1)):
            with self.assertRaises(AdapterError):
                fn()

    def test_construction_errors_are_adapter_errors(self):
        with self.assertRaises(AdapterError):
            SimulatorAdapter(sc(), "OSPF")
        with self.assertRaises(AdapterError):
            SimulatorAdapter(sc(num_nodes=101), "AEAR")

    def test_failures_and_snapshot_work_through_the_adapter(self):
        a = SimulatorAdapter(sc(), "AEAR")
        a.fail_node("N3")
        a.step(5)
        self.assertTrue(a.snapshot()["nodes"]["N3"]["failed"])
        self.assertEqual(len(a.network_dict()["nodes"]), 15)


class TestLoRaStub(unittest.TestCase):
    def test_cannot_be_constructed_and_says_why(self):
        with self.assertRaises(AdapterUnavailable) as cm:
            LoRaAdapter(sc(), "AEAR")
        self.assertIn("stub", str(cm.exception))

    def test_capabilities_admit_no_simulated_time_or_failure_injection(self):
        c = LoRaAdapter.capabilities
        self.assertFalse(c.simulated_time or c.failure_injection or c.decision_trace)

    def test_stub_is_still_a_complete_implementation_of_the_interface(self):
        self.assertEqual(LoRaAdapter.__abstractmethods__, frozenset())


class TestRegistry(unittest.TestCase):
    def test_create_known_and_unknown(self):
        self.assertIsInstance(create_adapter("simulator", sc()), SimulatorAdapter)
        for bad in ("nope", None, 5, ["simulator"]):
            with self.assertRaises(AdapterError):
                create_adapter(bad, sc())
        with self.assertRaises(AdapterUnavailable):
            create_adapter("lora", sc())

    def test_listing_marks_the_stub_unavailable(self):
        by_kind = {a["kind"]: a for a in list_adapters()}
        self.assertTrue(by_kind["simulator"]["available"])
        self.assertFalse(by_kind["lora"]["available"])
        json.dumps(list_adapters())  # must be JSON-serialisable as-is

    def test_register_custom_adapter_and_reject_non_adapters(self):
        class Fake(SimulatorAdapter):
            pass
        register_adapter("fake", Fake)
        self.addCleanup(_ADAPTERS.pop, "fake", None)
        self.assertIsInstance(create_adapter("fake", sc()), Fake)
        with self.assertRaises(TypeError):
            register_adapter("bad", object)


class TestOverTheWire(LiveServerCase):
    def test_session_created_reports_capabilities(self):
        c = self.connect()
        created = self.create(c)
        self.assertEqual(created["adapter"]["kind"], "simulator")
        self.assertTrue(created["adapter"]["simulated_time"])

    def test_explicit_simulator_adapter_selection(self):
        c = self.connect()
        c.send({"type": "session.create", "adapter": "simulator", "scenario": {"num_nodes": 15}})
        self.assertEqual(c.recv_until("session.created")["adapter"]["kind"], "simulator")

    def test_lora_and_unknown_adapters_give_clear_errors(self):
        c = self.connect()
        for kind, needle in (("lora", "stub"), ("nonsense", "Unknown adapter"), (7, "Unknown adapter")):
            c.send({"type": "session.create", "adapter": kind, "scenario": {}})
            self.assertIn(needle, c.recv_until("error")["error"])

    def test_failed_create_keeps_the_existing_session(self):
        c = self.connect()
        self.create(c)
        c.send({"type": "session.create", "adapter": "lora", "scenario": {}})
        c.recv_until("error")
        c.send({"type": "packet.send", "source": "N1", "destination": "N9"})
        self.assertEqual(c.recv_until("ack")["packet_id"], "P001")

    def test_reset_keeps_the_chosen_adapter_and_algorithm(self):
        c = self.connect()
        self.create(c, algorithm="AODV")
        c.send({"type": "sim.reset"})
        again = c.recv_until("session.created")
        self.assertEqual(again["adapter"]["kind"], "simulator")
        self.assertEqual(again["state"]["algorithm"], "AODV")

    def test_capability_gate_blocks_unsupported_commands(self):
        """Swap in a hardware-like adapter and check the handler refuses what it can't do."""
        class RadioLike(SimulatorAdapter):
            capabilities = AdapterCapabilities(kind="radiolike", simulated_time=False,
                                               failure_injection=False, decision_trace=False,
                                               algorithms=("AEAR",))
        register_adapter("radiolike", RadioLike)
        self.addCleanup(_ADAPTERS.pop, "radiolike", None)
        c = self.connect()
        c.send({"type": "session.create", "adapter": "radiolike", "scenario": {"num_nodes": 15}})
        self.assertFalse(c.recv_until("session.created")["adapter"]["simulated_time"])
        c.recv_until("ack")
        for msg, needle in (({"type": "sim.step"}, "stepping"),
                            ({"type": "sim.play", "speed": 1}, "playback"),
                            ({"type": "failure.node_fail", "node": "N3"}, "failure injection"),
                            ({"type": "failure.link_fail", "node_a": "N1", "node_b": "N2"}, "failure injection")):
            c.send(msg)
            self.assertIn(needle, c.recv_until("error")["error"])
        c.send({"type": "packet.send", "source": "N1", "destination": "N9"})  # still allowed
        self.assertEqual(c.recv_until("ack")["command"], "packet.send")

    def test_rest_adapters_endpoint(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/api/adapters")
        r = conn.getresponse()
        self.assertEqual(r.status, 200)
        kinds = {a["kind"]: a for a in json.loads(r.read())["adapters"]}
        self.assertTrue(kinds["simulator"]["available"])
        self.assertFalse(kinds["lora"]["available"])


if __name__ == "__main__":
    unittest.main()
