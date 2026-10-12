"""
Minimal RFC 6455 WebSocket server-side protocol, standard library only.

Matches the rest of the backend (plain Python, no framework). Supports what
Mode 1 needs: the opening handshake, text frames (with fragmentation),
ping/pong, and close. Binary frames are refused. Client frames must be
masked (the RFC requires it); server frames never are.

If the project later moves to FastAPI/`websockets`, only this file and
live_handler.py's transport calls change.
"""

from __future__ import annotations

import base64
import hashlib
import struct
import threading
from typing import Optional

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_MESSAGE_BYTES = 1_000_000

OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


class HandshakeError(Exception):
    pass


class ProtocolError(Exception):
    def __init__(self, message: str, code: int = 1002):
        super().__init__(message)
        self.code = code


def accept_key(client_key: str) -> str:
    return base64.b64encode(hashlib.sha1((client_key + GUID).encode("ascii")).digest()).decode("ascii")


def encode_frame(opcode: int, payload: bytes, mask_key: Optional[bytes] = None) -> bytes:
    """Build one final (FIN=1) frame. Pass mask_key to build a client frame."""
    header = bytearray([0x80 | opcode])
    n = len(payload)
    mask_bit = 0x80 if mask_key else 0
    if n < 126:
        header.append(mask_bit | n)
    elif n < 65536:
        header.append(mask_bit | 126)
        header += struct.pack("!H", n)
    else:
        header.append(mask_bit | 127)
        header += struct.pack("!Q", n)
    if mask_key:
        header += mask_key
        payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    return bytes(header) + payload


class WebSocketConnection:
    """Wraps a readable `rfile` and writable `wfile` (socketserver style)."""

    def __init__(self, rfile, wfile, require_mask: bool = True):
        self._rfile = rfile
        self._wfile = wfile
        self._require_mask = require_mask
        self._send_lock = threading.Lock()
        self.closed = False

    # -- receiving ---------------------------------------------------------------

    def _read_exact(self, n: int) -> bytes:
        chunks, remaining = [], n
        while remaining:
            data = self._rfile.read(remaining)  # may return fewer bytes on an unbuffered stream
            if not data:
                raise ConnectionError("connection closed")
            chunks.append(data)
            remaining -= len(data)
        return b"".join(chunks)

    def _read_frame(self):
        b1, b2 = self._read_exact(2)
        fin, opcode = bool(b1 & 0x80), b1 & 0x0F
        if b1 & 0x70:
            raise ProtocolError("reserved bits set")
        masked, length = bool(b2 & 0x80), b2 & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read_exact(8))[0]
        if opcode >= 0x8 and (length > 125 or not fin):
            raise ProtocolError("invalid control frame")
        if length > MAX_MESSAGE_BYTES:
            raise ProtocolError("message too big", 1009)
        if self._require_mask and not masked:
            raise ProtocolError("client frames must be masked")
        mask_key = self._read_exact(4) if masked else None
        payload = self._read_exact(length) if length else b""
        if mask_key:
            payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
        return fin, opcode, payload

    def recv_message(self) -> Optional[str]:
        """Next complete text message, or None once the connection closes."""
        buffer = bytearray()
        started = False
        try:
            while True:
                fin, opcode, payload = self._read_frame()
                if opcode == OP_PING:
                    self._send_frame(OP_PONG, payload)
                elif opcode == OP_PONG:
                    continue
                elif opcode == OP_CLOSE:
                    self.close(1000)
                    return None
                elif opcode in (OP_TEXT, OP_CONT):
                    if opcode == OP_TEXT:
                        if started:
                            raise ProtocolError("new message before previous finished")
                        started = True
                    elif not started:
                        raise ProtocolError("unexpected continuation frame")
                    buffer += payload
                    if len(buffer) > MAX_MESSAGE_BYTES:
                        raise ProtocolError("message too big", 1009)
                    if fin:
                        try:
                            return buffer.decode("utf-8")
                        except UnicodeDecodeError:
                            raise ProtocolError("invalid UTF-8", 1007)
                elif opcode == OP_BINARY:
                    raise ProtocolError("binary frames are not supported", 1003)
                else:
                    raise ProtocolError(f"unknown opcode {opcode}")
        except ProtocolError as e:
            self.close(e.code)
            return None
        except TimeoutError:
            raise  # only possible when the caller set a socket timeout (the test client); not a close
        except (ConnectionError, OSError):
            self.closed = True
            return None

    # -- sending -----------------------------------------------------------------

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        with self._send_lock:
            if self.closed:
                raise ConnectionError("connection closed")
            self._wfile.write(encode_frame(opcode, payload))
            self._wfile.flush()

    def send_text(self, text: str) -> None:
        self._send_frame(OP_TEXT, text.encode("utf-8"))

    def close(self, code: int = 1000) -> None:
        if self.closed:
            return
        try:
            self._send_frame(OP_CLOSE, struct.pack("!H", code))
        except (ConnectionError, OSError):
            pass
        self.closed = True


def perform_handshake(request_handler) -> WebSocketConnection:
    """Complete the upgrade on a BaseHTTPRequestHandler; returns the connection."""
    h = request_handler.headers
    if "websocket" not in h.get("Upgrade", "").lower():
        raise HandshakeError("missing Upgrade: websocket")
    if "upgrade" not in h.get("Connection", "").lower():
        raise HandshakeError("missing Connection: Upgrade")
    if h.get("Sec-WebSocket-Version") != "13":
        raise HandshakeError("unsupported Sec-WebSocket-Version (need 13)")
    key = h.get("Sec-WebSocket-Key")
    if not key:
        raise HandshakeError("missing Sec-WebSocket-Key")
    request_handler.send_response(101, "Switching Protocols")
    request_handler.send_header("Upgrade", "websocket")
    request_handler.send_header("Connection", "Upgrade")
    request_handler.send_header("Sec-WebSocket-Accept", accept_key(key))
    request_handler.end_headers()
    request_handler.wfile.flush()
    return WebSocketConnection(request_handler.rfile, request_handler.wfile)
