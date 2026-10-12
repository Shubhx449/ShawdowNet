"""
Phase 9 tests: live session logic, the WebSocket protocol, and the Mode 1
message layer end to end over a real socket.

Run with:
    python -m pytest tests/test_phase9_live.py -v
"""

import http.client
import io
import json
import os
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.api.server import make_handler_class
from backend.api.store import Store
from backend.experiments.runner import ExperimentRunner
from backend.live.session import LiveSession, SessionError
from backend.scenarios.config import ScenarioConfig
from backend.simulation.engine.engine import SimulationEngine
from backend.simulation.failures.recovery import FailureRecoveryManager
from backend.simulation.packets.packet import PacketType, reset_packet_id_counter
from backend.ws import protocol
from backend.ws.client import WSClient
from backend.ws.protocol import (OP_BINARY, OP_CLOSE, OP_PING, OP_TEXT, WebSocketConnection,
                                 accept_key, encode_frame)


def scenario(**kw):
    base = {"num_nodes": 15, "base_seed": 7}
    base.update(kw)
    return ScenarioConfig.from_dict(base)


# ---------------------------------------------------------------------------
# LiveSession
# ---------------------------------------------------------------------------

class TestLiveSession(unittest.TestCase):
    def test_matches_a_hand_built_engine_exactly(self):
        """A live session is the same simulation Mode 2 builds, not a look-alike."""
        sc = scenario()
        s = LiveSession(sc, "AEAR")
        s.send_packet("N1", "N9", "EMERGENCY")
        s.run_until_idle()
        live = [(e["event_type"], e["node_id"], e["metadata"]) for e in s.drain_events()]

        reset_packet_id_counter()
        runner = ExperimentRunner(sc)
        net = runner._build_network(sc.base_seed)
        eng = SimulationEngine(net)
        router = runner._build_router("AEAR", net, eng)
        FailureRecoveryManager(eng, [router])
        eng.generate_packet(0.0, "N1", "N9", PacketType.EMERGENCY, ttl=sc.ttl,
                            max_retransmissions=sc.max_retransmissions)
        eng.run()
        manual = [(e.event_type.value, e.node_id, e.metadata) for e in eng.event_log.all()]
        self.assertEqual(live, manual)

    def test_drain_events_never_repeats(self):
        s = LiveSession(scenario(), "RSSI")
        s.send_packet("N1", "N9")
        s.step(3)
        first = s.drain_events()
        s.run_until_idle()
        second = s.drain_events()
        self.assertTrue(first and second)
        self.assertEqual(len(set(e["event_id"] for e in first + second)), len(first) + len(second))
        self.assertEqual(s.drain_events(), [])

    def test_fail_all_neighbours_buffers_then_restore_recovers(self):
        s = LiveSession(scenario(), "AEAR")
        neighbours = [n.node_id for n in s.network.neighbors_of("N1")]
        self.assertGreater(len(neighbours), 1)
        for n in neighbours:
            s.fail_node(n)
        s.run_until_idle()
        s.send_packet("N1", "N9")
        s.run_until_idle()
        self.assertEqual(s.snapshot()["packets"]["P001"]["status"], "BUFFERED")
        s.restore_node(neighbours[0])
        s.run_until_idle()
        types = [e["event_type"] for e in s.drain_events()]
        self.assertIn("NODE_RECOVERED", types)
        self.assertGreaterEqual(s.snapshot()["packets"]["P001"]["hop_count"], 1)

    def test_failures_show_up_in_snapshot(self):
        s = LiveSession(scenario(), "AEAR")
        link = next(iter(s.network.links.keys()))
        s.fail_node("N3")
        s.fail_link(*link)
        s.run_until_idle()
        snap = s.snapshot()
        self.assertTrue(snap["nodes"]["N3"]["failed"])
        self.assertEqual(snap["nodes"]["N3"]["status"], "FAILED")
        self.assertIn(list(link), snap["failed_links"])
        s.restore_node("N3")
        s.restore_link(*link)
        s.run_until_idle()
        self.assertFalse(s.snapshot()["nodes"]["N3"]["failed"])
        self.assertEqual(s.snapshot()["failed_links"], [])

    def test_advance_moves_playhead_through_idle_time(self):
        s = LiveSession(scenario(), "AEAR")
        s.advance(5.0)
        self.assertAlmostEqual(s.playhead, 5.0)
        s.send_packet("N1", "N9")  # scheduled at the playhead, not at 0
        s.run_until_idle()
        first = s.drain_events()[0]
        self.assertGreaterEqual(first["timestamp"], 5.0)

    def test_step_processes_exactly_one_event(self):
        s = LiveSession(scenario(), "AEAR")
        s.send_packet("N1", "N9")
        self.assertEqual(s.step(1), 1)
        self.assertEqual([e["event_type"] for e in s.drain_events()][0], "PACKET_GENERATED")

    def test_all_three_algorithms_run(self):
        for alg in ("AEAR", "RSSI", "AODV"):
            s = LiveSession(scenario(), alg)
            s.send_packet("N1", "N9")
            s.run_until_idle()
            self.assertGreater(len(s.drain_events()), 0, alg)

    def test_bad_commands_raise_session_error(self):
        s = LiveSession(scenario(), "AEAR")
        bad = [
            lambda: s.send_packet("NOPE", "N2"),
            lambda: s.send_packet("N1", "NOPE"),
            lambda: s.send_packet("N1", "N1"),
            lambda: s.send_packet("N1", "N2", "URGENT"),
            lambda: s.send_packet("N1", "N2", ttl=0),
            lambda: s.send_packet("N1", "N2", ttl="5"),
            lambda: s.send_packet(None, "N2"),
            lambda: s.restore_node("N1"),
            lambda: s.restore_link(*next(iter(s.network.links.keys()))),
            lambda: s.fail_node("NOPE"),
            lambda: s.advance(-1),
        ]
        for i, fn in enumerate(bad):
            with self.assertRaises(SessionError, msg=f"case {i}"):
                fn()

    def test_double_failure_and_missing_link_rejected(self):
        s = LiveSession(scenario(), "AEAR")
        s.fail_node("N2")
        s.run_until_idle()
        with self.assertRaises(SessionError):
            s.fail_node("N2")
        with self.assertRaises(SessionError):
            s.send_packet("N2", "N5")  # failed source cannot transmit
        unlinked = next((a, b) for a in s.network.nodes for b in s.network.nodes
                        if a < b and s.network.get_link(a, b) is None)
        with self.assertRaises(SessionError):
            s.fail_link(*unlinked)

    def test_unknown_algorithm_and_size_cap(self):
        with self.assertRaises(SessionError):
            LiveSession(scenario(), "OSPF")
        with self.assertRaises(SessionError):
            LiveSession(scenario(num_nodes=101), "AEAR")

    def test_rejected_command_leaves_no_pending_event(self):
        s = LiveSession(scenario(), "AEAR")
        with self.assertRaises(SessionError):
            s.send_packet("N1", "N1")
        self.assertFalse(s.has_pending)


# ---------------------------------------------------------------------------
# WebSocket protocol
# ---------------------------------------------------------------------------

def masked(opcode, payload, fin=True):
    key = b"\x01\x02\x03\x04"
    frame = bytearray(encode_frame(opcode, payload, mask_key=key))
    if not fin:
        frame[0] &= 0x7F
    return bytes(frame)


def conn_from(*frames, require_mask=True):
    out = io.BytesIO()
    return WebSocketConnection(io.BytesIO(b"".join(frames)), out, require_mask=require_mask), out


class TestProtocol(unittest.TestCase):
    def test_rfc_6455_accept_key_vector(self):
        self.assertEqual(accept_key("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_masked_text_roundtrip_all_length_encodings(self):
        for n in (0, 5, 125, 126, 300, 65535, 65536, 70000):
            text = "a" * n
            conn, _ = conn_from(masked(OP_TEXT, text.encode()))
            self.assertEqual(conn.recv_message(), text, n)

    def test_server_frames_are_unmasked_and_well_formed(self):
        conn, out = conn_from()
        conn.send_text("héllo")
        data = out.getvalue()
        self.assertEqual(data[0], 0x81)
        self.assertEqual(data[1] & 0x80, 0)
        self.assertEqual(data[2:].decode(), "héllo")

    def test_unmasked_client_frame_closes_with_1002(self):
        conn, out = conn_from(encode_frame(OP_TEXT, b"hi"))
        self.assertIsNone(conn.recv_message())
        self.assertEqual(struct.unpack("!H", out.getvalue()[2:4])[0], 1002)

    def test_ping_gets_pong_then_message_still_arrives(self):
        conn, out = conn_from(masked(OP_PING, b"x"), masked(OP_TEXT, b"after"))
        self.assertEqual(conn.recv_message(), "after")
        self.assertEqual(out.getvalue()[0], 0x8A)  # FIN + PONG
        self.assertEqual(out.getvalue()[2:], b"x")

    def test_fragmented_message_is_reassembled(self):
        conn, _ = conn_from(masked(OP_TEXT, b"hel", fin=False), masked(0x0, b"lo"))
        self.assertEqual(conn.recv_message(), "hello")

    def test_orphan_continuation_is_protocol_error(self):
        conn, out = conn_from(masked(0x0, b"x"))
        self.assertIsNone(conn.recv_message())
        self.assertEqual(struct.unpack("!H", out.getvalue()[2:4])[0], 1002)

    def test_binary_frame_rejected_with_1003(self):
        conn, out = conn_from(masked(OP_BINARY, b"\x00"))
        self.assertIsNone(conn.recv_message())
        self.assertEqual(struct.unpack("!H", out.getvalue()[2:4])[0], 1003)

    def test_invalid_utf8_rejected_with_1007(self):
        conn, out = conn_from(masked(OP_TEXT, b"\xff\xfe"))
        self.assertIsNone(conn.recv_message())
        self.assertEqual(struct.unpack("!H", out.getvalue()[2:4])[0], 1007)

    def test_oversized_message_rejected_with_1009(self):
        header = bytes([0x81, 0x80 | 127]) + struct.pack("!Q", protocol.MAX_MESSAGE_BYTES + 1) + b"\0\0\0\0"
        conn, out = conn_from(header)
        self.assertIsNone(conn.recv_message())
        self.assertEqual(struct.unpack("!H", out.getvalue()[2:4])[0], 1009)

    def test_close_frame_is_answered_and_ends_the_stream(self):
        conn, out = conn_from(masked(OP_CLOSE, b"\x03\xe8"))
        self.assertIsNone(conn.recv_message())
        self.assertEqual(out.getvalue()[0], 0x88)
        self.assertTrue(conn.closed)

    def test_truncated_stream_returns_none_not_exception(self):
        conn, _ = conn_from(masked(OP_TEXT, b"hello")[:4])
        self.assertIsNone(conn.recv_message())


# ---------------------------------------------------------------------------
# End to end over a real socket
# ---------------------------------------------------------------------------

class LiveServerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler_class(Store(root_dir=cls.tmp)))
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def connect(self):
        c = WSClient("127.0.0.1", self.port, timeout=5)
        self.addCleanup(c.close)
        self.assertEqual(c.recv()["type"], "hello")
        return c

    def create(self, c, algorithm="AEAR", **scenario_kw):
        sc = {"num_nodes": 15, "base_seed": 7}
        sc.update(scenario_kw)
        c.send({"type": "session.create", "algorithm": algorithm, "scenario": sc})
        created = c.recv_until("session.created")
        c.recv_until("ack")  # session.create's own ack, so later recv_until("ack") is unambiguous
        return created

    def collect_events(self, c, until_type=None, timeout=3.0):
        c.sock.settimeout(0.4)
        events, deadline = [], time.time() + timeout
        while time.time() < deadline:
            m = c.recv()
            if m is None:
                break
            if m["type"] == "events":
                events += m["events"]
        c.sock.settimeout(5)
        return events


class TestLiveOverSocket(LiveServerCase):
    def test_full_flow_step_by_step(self):
        c = self.connect()
        created = self.create(c)
        self.assertEqual(len(created["network"]["nodes"]), 15)
        self.assertTrue(created["network"]["links"])
        c.send({"type": "packet.send", "source": "N1", "destination": "N9",
                "packet_type": "EMERGENCY", "request_id": "abc"})
        ack = c.recv_until("ack")
        self.assertEqual(ack["request_id"], "abc")
        self.assertEqual(ack["packet_id"], "P001")
        c.send({"type": "sim.step", "count": 1})
        ev = c.recv_until("events")
        # one step = one scheduled event, *including* the routing decision it triggers
        kinds = [e["event_type"] for e in ev["events"]]
        self.assertEqual(kinds[0], "PACKET_GENERATED")
        self.assertIn("FORWARDING_DECISION", kinds)
        self.assertIn("P001", ev["state"]["packets"])

    def test_play_streams_decision_events_then_pause_stops_them(self):
        c = self.connect()
        self.create(c)
        c.send({"type": "packet.send", "source": "N1", "destination": "N9", "packet_type": "EMERGENCY"})
        c.recv_until("ack")
        c.send({"type": "sim.play", "speed": 100})
        events = self.collect_events(c, timeout=1.5)
        kinds = {e["event_type"] for e in events}
        self.assertTrue({"PACKET_GENERATED", "CANDIDATE_EVALUATED", "FORWARDING_DECISION",
                         "PACKET_FORWARDED"} <= kinds)
        ids = [e["event_id"] for e in events]
        self.assertEqual(ids, sorted(set(ids)))  # ordered, no duplicates across batches
        c.send({"type": "sim.pause"})
        c.recv_until("ack")
        c.send({"type": "packet.send", "source": "N2", "destination": "N8"})
        c.recv_until("ack")
        self.assertEqual(self.collect_events(c, timeout=0.8), [])  # paused: nothing streams

    def test_candidate_events_carry_scores_for_the_decision_panel(self):
        c = self.connect()
        self.create(c)
        c.send({"type": "packet.send", "source": "N1", "destination": "N9"})
        c.send({"type": "sim.step", "count": 20})
        cand = []
        for _ in range(10):
            m = c.recv()
            if m and m["type"] == "events":
                cand += [e for e in m["events"] if e["event_type"] == "CANDIDATE_EVALUATED"]
                if cand:
                    break
        self.assertTrue(cand)
        self.assertIn("score", cand[0]["metadata"])
        self.assertIn("candidate", cand[0]["metadata"])

    def test_failure_controls_over_the_wire(self):
        c = self.connect()
        created = self.create(c)
        c.send({"type": "failure.node_fail", "node": "N3"})
        c.recv_until("ack")
        c.send({"type": "sim.step"})
        ev = c.recv_until("events")
        self.assertEqual(ev["events"][0]["event_type"], "NODE_FAILED")
        self.assertTrue(ev["state"]["nodes"]["N3"]["failed"])
        a, b = created["network"]["links"][0]["node_a"], created["network"]["links"][0]["node_b"]
        c.send({"type": "failure.link_fail", "node_a": a, "node_b": b})
        c.recv_until("ack")
        c.send({"type": "sim.step"})
        self.assertEqual(c.recv_until("events")["events"][0]["event_type"], "LINK_FAILED")

    def test_reset_rebuilds_identical_session(self):
        c = self.connect()
        first = self.create(c)
        c.send({"type": "packet.send", "source": "N1", "destination": "N9"})
        c.send({"type": "sim.step", "count": 5})
        c.recv_until("events")
        c.send({"type": "sim.reset"})
        again = c.recv_until("session.created")
        self.assertEqual(first["network"], again["network"])
        self.assertEqual(again["state"]["packets"], {})
        self.assertEqual(again["state"]["clock"], 0.0)

    def test_errors_are_reported_and_the_connection_survives(self):
        c = self.connect()
        c.send({"type": "packet.send", "source": "N1", "destination": "N2"})
        self.assertIn("session.create", c.recv_until("error")["error"])
        self.create(c)
        cases = [
            {"type": "packet.send", "source": "N1", "destination": "NOPE", "request_id": 9},
            {"type": "no.such.command"},
            {"type": "sim.play", "speed": 0},
            {"type": "sim.play", "speed": "fast"},
            {"type": "sim.step", "count": "many"},
            {"type": "failure.node_restore", "node": "N1"},
            {"type": 5},
            [1, 2, 3],
        ]
        for msg in cases:
            c.send(msg)
            self.assertEqual(c.recv_until("error")["type"], "error", msg)
        c.sock.sendall(encode_frame(OP_TEXT, b"{not json", mask_key=b"abcd"))
        self.assertIn("JSON", c.recv_until("error")["error"])
        c.send({"type": "ping"})
        self.assertEqual(c.recv_until("pong")["type"], "pong")  # still alive

    def test_bad_scenario_is_an_error_not_a_crash(self):
        c = self.connect()
        for sc in ({"num_nodes": 1}, {"num_nodes": 500}, {"normal_ratio": 0.9}, {"num_nodes": "abc"}, "text"):
            c.send({"type": "session.create", "scenario": sc})
            self.assertEqual(c.recv_until("error")["type"], "error", sc)
        c.send({"type": "session.create", "algorithm": "OSPF", "scenario": {}})
        self.assertEqual(c.recv_until("error")["type"], "error")

    def test_two_connections_are_independent_sessions(self):
        a, b = self.connect(), self.connect()
        self.create(a)
        self.create(b)
        a.send({"type": "failure.node_fail", "node": "N4"})
        a.recv_until("ack")
        a.send({"type": "sim.step"})
        a.recv_until("events")
        b.send({"type": "state.get"})
        self.assertFalse(b.recv_until("state")["state"]["nodes"]["N4"]["failed"])

    def test_disconnect_mid_play_does_not_wedge_the_server(self):
        c = self.connect()
        self.create(c)
        c.send({"type": "sim.play", "speed": 1000})
        c.recv_until("ack")
        c.close()
        time.sleep(0.3)
        d = self.connect()  # server still accepting, slot was released
        self.create(d)

    def test_rest_api_unaffected_on_same_port(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/api/health")
        r = conn.getresponse()
        self.assertEqual(r.status, 200)
        self.assertEqual(json.loads(r.read())["status"], "ok")

    def test_bad_handshake_gets_400(self):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall(b"GET /ws/live HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                  b"Sec-WebSocket-Key: abc\r\nSec-WebSocket-Version: 8\r\n\r\n")
        self.assertIn(b" 400 ", s.recv(4096).split(b"\r\n")[0] + b" ")
        s.close()

    def test_plain_get_on_ws_path_is_not_an_upgrade(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/ws/live")
        self.assertEqual(conn.getresponse().status, 404)


if __name__ == "__main__":
    unittest.main()
