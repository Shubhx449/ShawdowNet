"""
HTTP server.

Deliberately built on Python's stdlib http.server rather than a framework
(FastAPI/Flask) -- this project's backend is plain Python for now, and this
is genuinely all a JSON REST API needs at this scale: a route table, a JSON
in/out convenience layer, and CORS headers for a future browser frontend.
When the project is ready for FastAPI (async experiment execution, request
validation via Pydantic, auto-generated docs), swapping this file out is a
contained change -- handlers.py's functions don't know or care what's
calling them, so nothing else needs to move.
"""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, List, Optional, Pattern, Tuple

from backend.api import handlers
from backend.api.handlers import ApiError
from backend.api.static import describe, resolve_static
from backend.api.store import Store
from backend.ws.live_handler import serve_live_connection
from backend.ws.protocol import HandshakeError, perform_handshake

Route = Tuple[str, Pattern, Callable]

MAX_BODY_BYTES = 1_000_000  # a scenario is a few KB; ~1MB means a mistake or abuse

ROUTES: List[Route] = [
    ("GET", re.compile(r"^/api/health$"), handlers.health),
    ("GET", re.compile(r"^/api/defaults$"), handlers.defaults),
    ("GET", re.compile(r"^/api/adapters$"), handlers.list_adapters),

    ("POST", re.compile(r"^/api/scenarios$"), handlers.create_scenario),
    ("GET", re.compile(r"^/api/scenarios$"), handlers.list_scenarios),
    ("GET", re.compile(r"^/api/scenarios/(?P<id>[\w-]+)$"), handlers.get_scenario),
    ("PUT", re.compile(r"^/api/scenarios/(?P<id>[\w-]+)$"), handlers.update_scenario),
    ("DELETE", re.compile(r"^/api/scenarios/(?P<id>[\w-]+)$"), handlers.delete_scenario),

    ("POST", re.compile(r"^/api/experiments/run$"), handlers.run_experiment),
    ("GET", re.compile(r"^/api/experiments$"), handlers.list_experiments),
    ("GET", re.compile(r"^/api/experiments/(?P<id>[\w-]+)$"), handlers.get_experiment),
    ("GET", re.compile(r"^/api/experiments/(?P<id>[\w-]+)/status$"), handlers.get_experiment_status),
    ("GET", re.compile(r"^/api/experiments/(?P<id>[\w-]+)/results$"), handlers.get_experiment_results),
    ("GET", re.compile(r"^/api/experiments/(?P<id>[\w-]+)/metrics$"), handlers.get_experiment_metrics),
    ("GET", re.compile(r"^/api/experiments/(?P<id>[\w-]+)/graphs$"), handlers.get_experiment_graphs),
]


def make_handler_class(store: Store, static_dir: Optional[str] = None):
    """Returns a BaseHTTPRequestHandler subclass bound to `store` via closure.
    If `static_dir` is given, non-API GET/HEAD requests serve the built frontend from it."""

    class ShadowNetRequestHandler(BaseHTTPRequestHandler):
        server_version = "ShadowNetAPI/0.1"

        def _dispatch(self, method: str) -> None:
            path = self.path.split("?", 1)[0]
            for route_method, pattern, handler_fn in ROUTES:
                if route_method != method:
                    continue
                match = pattern.match(path)
                if not match:
                    continue
                try:
                    body = self._read_json_body() if method in ("POST", "PUT") else None
                    status, payload = handler_fn(store, match.groupdict(), body)
                except ApiError as e:
                    status, payload = e.status, {"error": str(e)}
                except json.JSONDecodeError:
                    status, payload = 400, {"error": "Request body is not valid JSON"}
                except Exception as e:  # noqa: BLE001 -- last-resort 500, never crash the server
                    status, payload = 500, {"error": f"Internal error: {e}"}
                self._respond(status, payload)
                return
            self._respond(404, {"error": f"No route for {method} {path}"})

        def _read_json_body(self) -> Optional[dict]:
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length == 0:
                return None
            if length > MAX_BODY_BYTES:
                raise ApiError(413, f"Request body too large (limit {MAX_BODY_BYTES} bytes)")
            raw = self.rfile.read(length)
            return json.loads(raw) if raw else None

        def _respond(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/ws/live" and "websocket" in self.headers.get("Upgrade", "").lower():
                self._handle_websocket()
                return
            if static_dir is not None and not (path == "/api" or path.startswith("/api/")):
                self._serve_static(path, head=False)
                return
            self._dispatch("GET")

        def do_HEAD(self) -> None:
            path = self.path.split("?", 1)[0]
            if static_dir is not None and not (path == "/api" or path.startswith("/api/")):
                self._serve_static(path, head=True)
            else:
                self.send_error(501, "Unsupported method ('HEAD')")

        def _serve_static(self, path: str, head: bool) -> None:
            file_path = resolve_static(static_dir, path)
            if file_path is None:
                self._respond(404, {"error": f"Not found: {path}"})
                return
            try:
                with open(file_path, "rb") as f:
                    data = f.read()
            except OSError:
                self._respond(404, {"error": f"Not found: {path}"})
                return
            ctype, cache = describe(static_dir, file_path)
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", cache)
            self.end_headers()
            if not head:
                self.wfile.write(data)

        def _handle_websocket(self) -> None:
            try:
                conn = perform_handshake(self)
            except HandshakeError as e:
                self._respond(400, {"error": f"WebSocket handshake failed: {e}"})
                return
            self.close_connection = True  # the socket belongs to the WebSocket from here on
            serve_live_connection(conn)

        def do_POST(self) -> None:
            self._dispatch("POST")

        def do_PUT(self) -> None:
            self._dispatch("PUT")

        def do_DELETE(self) -> None:
            self._dispatch("DELETE")

        def do_OPTIONS(self) -> None:  # CORS preflight
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            pass  # quiet by default; swap for real logging if needed

    return ShadowNetRequestHandler


def run_server(store: Store, host: str = "127.0.0.1", port: int = 8000, static_dir: Optional[str] = None) -> ThreadingHTTPServer:
    """Builds and starts (via serve_forever, blocking) an HTTP server bound to `store`.
    For programmatic control (e.g. in tests), construct the server yourself with make_handler_class()
    and call serve_forever() / shutdown() on your own thread instead of calling this directly."""
    server = ThreadingHTTPServer((host, port), make_handler_class(store, static_dir))
    where = f"http://{host}:{port}"
    print(f"ShadowNet listening on {where}")
    print(f"  UI:  {where}/" if static_dir else "  UI:  not served (no built frontend; run `npm run build` in frontend/, or use `npm run dev`)")
    print(f"  API: {where}/api/health   WebSocket: ws://{host}:{port}/ws/live   (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()
    return server
