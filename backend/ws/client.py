"""
Tiny blocking WebSocket client (stdlib only) for tests and demos.
Not a general-purpose client: text frames only, no TLS, no fragmentation on send.
"""

from __future__ import annotations

import base64
import json
import os
import socket
from typing import Optional

from backend.ws.protocol import (OP_CLOSE, OP_PING, OP_PONG, OP_TEXT, WebSocketConnection,
                                 accept_key, encode_frame)


class _SockReader:
    """read(n) over a raw socket. Unlike socket.makefile(), a timeout here leaves the
    stream usable (SocketIO poisons itself after the first timeout)."""

    def __init__(self, sock):
        self._sock = sock

    def read(self, n: int) -> bytes:
        return self._sock.recv(n)


class WSClient:
    def __init__(self, host: str, port: int, path: str = "/ws/live", timeout: float = 5.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((
            f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        ).encode())
        self._file = self.sock.makefile("rwb", buffering=0)
        status = self._file.readline().decode().strip()
        headers = {}
        while True:
            line = self._file.readline().decode().strip()
            if not line:
                break
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()
        self.status_line = status
        if " 101 " not in status + " ":
            raise ConnectionError(f"handshake failed: {status}")
        if headers.get("sec-websocket-accept") != accept_key(key):
            raise ConnectionError("bad Sec-WebSocket-Accept")
        # server frames are unmasked, so don't require masks when reading
        self._conn = WebSocketConnection(_SockReader(self.sock), None, require_mask=False)

    def send_raw_frame(self, opcode: int, payload: bytes) -> None:
        self.sock.sendall(encode_frame(opcode, payload, mask_key=os.urandom(4)))

    def send(self, message: dict) -> None:
        self.send_raw_frame(OP_TEXT, json.dumps(message).encode())

    def recv(self) -> Optional[dict]:
        """Next message; None if the connection closed OR the socket timeout elapsed
        with nothing to read (the connection stays usable after a timeout, provided
        it happened between frames -- fine for tests, not for production use)."""
        try:
            text = self._conn.recv_message()
        except TimeoutError:
            return None
        return None if text is None else json.loads(text)

    def recv_until(self, wanted_type: str, limit: int = 500) -> dict:
        for _ in range(limit):
            msg = self.recv()
            if msg is None:
                raise ConnectionError("closed or timed out while waiting for " + wanted_type)
            if msg.get("type") == wanted_type:
                return msg
        raise TimeoutError(f"no {wanted_type} within {limit} messages")

    def close(self) -> None:
        try:
            self.send_raw_frame(OP_CLOSE, b"\x03\xe8")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
