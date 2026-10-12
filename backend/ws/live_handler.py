"""
Mode 1 message layer: one WebSocket connection = one LiveSession.

Wire format: JSON text frames.

Client -> server (optional "request_id" is echoed back):
    {"type": "session.create", "algorithm": "AEAR", "adapter": "simulator", "scenario": {...}}
    {"type": "packet.send", "source": "N1", "destination": "N9", "packet_type": "EMERGENCY", "ttl": 12}
    {"type": "failure.node_fail" | "failure.node_restore", "node": "N4"}
    {"type": "failure.link_fail" | "failure.link_restore", "node_a": "N2", "node_b": "N5"}
    {"type": "sim.step", "count": 1}          process N events
    {"type": "sim.play", "speed": 1.0}        simulated seconds per wall second
    {"type": "sim.pause"}
    {"type": "sim.reset"}                     rebuild the same session from scratch
    {"type": "state.get"}
    {"type": "ping"}

Server -> client:
    {"type": "hello", "protocol": 1}
    {"type": "session.created", "adapter": {capabilities}, "network": {...}, "state": {...}}   (also after sim.reset)
    {"type": "ack", "request_id": ..., "command": ..., ...}
    {"type": "events", "events": [...raw engine events...], "state": {...}}
    {"type": "state", "state": {...}}
    {"type": "error", "request_id": ..., "error": "..."}
    {"type": "pong"}

Events are the engine's own events, unmodified (CANDIDATE_EVALUATED etc.
already carry the scores the AEAR decision panel needs) -- this layer adds
transport, not interpretation.
"""

from __future__ import annotations

import json
import threading
from typing import Callable, Dict, Optional

from backend.adapters.base import AdapterError, UnsupportedOperation
from backend.adapters.registry import DEFAULT_KIND, create_adapter
from backend.live.session import SessionError
from backend.scenarios.config import ScenarioConfig, ScenarioValidationError
from backend.ws.protocol import WebSocketConnection

PROTOCOL_VERSION = 1
TICK_S = 0.05  # wall-clock interval between play-loop batches
MIN_SPEED, MAX_SPEED = 0.01, 1000.0
MAX_CONCURRENT_CONNECTIONS = 8

_slots = threading.BoundedSemaphore(MAX_CONCURRENT_CONNECTIONS)


class LiveConnection:
    def __init__(self, conn: WebSocketConnection, tick_s: float = TICK_S):
        self.conn = conn
        self.tick_s = tick_s
        self.session = None  # a CommunicationAdapter once session.create succeeds
        self._create_args: Optional[tuple] = None
        self._play_thread: Optional[threading.Thread] = None
        self._play_stop = threading.Event()
        self._speed = 1.0
        self._commands: Dict[str, Callable[[dict], Optional[dict]]] = {
            "session.create": self._session_create,
            "packet.send": self._packet_send,
            "failure.node_fail": lambda m: self._node_cmd(m, "fail_node"),
            "failure.node_restore": lambda m: self._node_cmd(m, "restore_node"),
            "failure.link_fail": lambda m: self._link_cmd(m, "fail_link"),
            "failure.link_restore": lambda m: self._link_cmd(m, "restore_link"),
            "sim.step": self._sim_step,
            "sim.play": self._sim_play,
            "sim.pause": self._sim_pause,
            "sim.reset": self._sim_reset,
            "state.get": self._state_get,
        }

    # -- lifecycle ---------------------------------------------------------------

    def serve(self) -> None:
        if not _slots.acquire(blocking=False):
            self.conn.close(1013)  # try again later
            return
        try:
            self._send({"type": "hello", "protocol": PROTOCOL_VERSION})
            while True:
                text = self.conn.recv_message()
                if text is None:
                    break
                self._handle(text)
        except (ConnectionError, OSError):
            pass
        finally:
            self._stop_playback()
            _slots.release()

    def _send(self, message: dict) -> None:
        self.conn.send_text(json.dumps(message))

    # -- dispatch ----------------------------------------------------------------

    def _handle(self, text: str) -> None:
        request_id = None
        try:
            try:
                msg = json.loads(text)
            except json.JSONDecodeError:
                raise SessionError("message is not valid JSON")
            if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
                raise SessionError('message must be a JSON object with a string "type"')
            request_id = msg.get("request_id")
            kind = msg["type"]
            if kind == "ping":
                self._send({"type": "pong"})
                return
            fn = self._commands.get(kind)
            if fn is None:
                raise SessionError(f"Unknown message type: {kind!r}")
            if kind != "session.create" and self.session is None:
                raise SessionError("No session yet -- send session.create first")
            extra = fn(msg) or {}
            self._send({"type": "ack", "request_id": request_id, "command": kind, **extra})
            if kind not in ("session.create", "sim.reset", "state.get"):
                self._flush_events()
        except (SessionError, AdapterError, ScenarioValidationError) as e:
            self._send({"type": "error", "request_id": request_id, "error": str(e)})
        except (TypeError, ValueError, KeyError) as e:
            # wrong-typed fields in an otherwise well-formed message: a client error, not a crash
            self._send({"type": "error", "request_id": request_id, "error": f"Invalid field: {e}"})

    def _flush_events(self) -> None:
        events = self.session.drain_events()
        if events:
            self._send({"type": "events", "events": events, "state": self.session.snapshot()})

    # -- commands ----------------------------------------------------------------

    def _session_create(self, msg: dict) -> dict:
        scenario_raw = msg.get("scenario") or {}
        if not isinstance(scenario_raw, dict):
            raise SessionError('"scenario" must be an object')
        algorithm = msg.get("algorithm", "AEAR")
        kind = msg.get("adapter", DEFAULT_KIND)
        self._stop_playback()
        scenario = ScenarioConfig.from_dict(scenario_raw)
        new = create_adapter(kind, scenario, algorithm)  # raises before touching the old session
        if self.session is not None:
            self.session.close()
        self.session = new
        self._create_args = (scenario_raw, algorithm, kind)
        self._send({"type": "session.created", "adapter": self.session.capabilities.to_dict(),
                    "network": self.session.network_dict(), "state": self.session.snapshot()})
        return {}

    def _sim_reset(self, msg: dict) -> dict:
        scenario_raw, algorithm, kind = self._create_args
        return self._session_create({"scenario": scenario_raw, "algorithm": algorithm, "adapter": kind})

    def _packet_send(self, msg: dict) -> dict:
        return self.session.send_packet(msg.get("source"), msg.get("destination"),
                                        msg.get("packet_type", "NORMAL"),
                                        ttl=msg.get("ttl"), message=msg.get("message", ""))

    def _require(self, capability: str, what: str) -> None:
        if not getattr(self.session.capabilities, capability):
            raise UnsupportedOperation(
                f"The {self.session.capabilities.kind} adapter does not support {what}")

    def _node_cmd(self, msg: dict, method: str) -> None:
        self._require("failure_injection", "failure injection")
        getattr(self.session, method)(msg.get("node"))

    def _link_cmd(self, msg: dict, method: str) -> None:
        self._require("failure_injection", "failure injection")
        getattr(self.session, method)(msg.get("node_a"), msg.get("node_b"))

    def _sim_step(self, msg: dict) -> dict:
        self._require("simulated_time", "stepping")
        self._stop_playback()  # manual stepping and free-running playback don't mix
        n = self.session.step(msg.get("count", 1))
        return {"processed": n}

    def _state_get(self, msg: dict) -> dict:
        self._send({"type": "state", "state": self.session.snapshot()})
        return {}

    # -- playback ----------------------------------------------------------------

    def _sim_play(self, msg: dict) -> dict:
        self._require("simulated_time", "simulated-time playback")
        speed = float(msg.get("speed", 1.0))
        if not (MIN_SPEED <= speed <= MAX_SPEED):
            raise SessionError(f"speed must be between {MIN_SPEED} and {MAX_SPEED}")
        self._speed = speed  # takes effect immediately if already playing
        if self._play_thread is None or not self._play_thread.is_alive():
            self._play_stop.clear()
            self._play_thread = threading.Thread(target=self._play_loop, daemon=True)
            self._play_thread.start()
        return {"speed": speed}

    def _sim_pause(self, msg: dict) -> None:
        self._stop_playback()

    def _stop_playback(self) -> None:
        self._play_stop.set()
        t = self._play_thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=2)
        self._play_thread = None

    def _play_loop(self) -> None:
        session = self.session
        try:
            while not self._play_stop.wait(self.tick_s):
                session.advance(self._speed * self.tick_s)
                self._flush_events()
        except (ConnectionError, OSError):
            pass
        except Exception as e:  # noqa: BLE001 -- report and stop, never die silently
            try:
                self._send({"type": "error", "request_id": None, "error": f"Playback stopped: {e}"})
            except (ConnectionError, OSError):
                pass


def serve_live_connection(conn: WebSocketConnection) -> None:
    LiveConnection(conn).serve()
