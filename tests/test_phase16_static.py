"""
Phase 16 -- serving the built frontend from the backend, and the launcher.

    python -m pytest tests/test_phase16_static.py -v
"""

import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.api.server import make_handler_class
from backend.api.static import describe, resolve_static
from backend.api.store import Store
from backend.ws.client import WSClient

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def make_dist(base):
    dist = os.path.join(base, "dist")
    os.makedirs(os.path.join(dist, "assets"))
    with open(os.path.join(dist, "index.html"), "w") as f: f.write("<!doctype html><title>SN</title><div id=root></div>")
    with open(os.path.join(dist, "assets", "app-abc123.js"), "w") as f: f.write("console.log('hi')")
    with open(os.path.join(dist, "assets", "app-abc123.css"), "w") as f: f.write("body{}")
    with open(os.path.join(dist, "favicon.svg"), "w") as f: f.write("<svg/>")
    with open(os.path.join(base, "secret.txt"), "w") as f: f.write("TOP SECRET")  # outside the served root
    return dist


class TestResolveStatic(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dist = make_dist(self.tmp.name)

    def r(self, p): return resolve_static(self.dist, p)

    def test_serves_index_assets_and_root_files(self):
        self.assertTrue(self.r("/").endswith("index.html"))
        self.assertTrue(self.r("/assets/app-abc123.js").endswith("app-abc123.js"))
        self.assertTrue(self.r("/favicon.svg").endswith("favicon.svg"))

    def test_client_side_routes_fall_back_to_index(self):
        for p in ("/experiments", "/experiments/3dc80c8700f7", "/live", "/scenarios/"):
            self.assertTrue(self.r(p).endswith("index.html"), p)

    def test_missing_file_with_extension_is_404_not_html(self):
        for p in ("/assets/missing.js", "/nope.css", "/x.png"):
            self.assertIsNone(self.r(p), p)

    def test_api_and_ws_paths_never_reach_the_ui(self):
        for p in ("/api", "/api/", "/api/nope", "/ws", "/ws/live", "/ws/anything"):
            self.assertIsNone(self.r(p), p)

    def test_path_traversal_is_refused_in_every_encoding(self):
        for p in ("/../secret.txt", "/assets/../../secret.txt", "/%2e%2e/secret.txt", "/%2e%2e%2fsecret.txt",
                  "/assets/%2e%2e/%2e%2e/secret.txt", "/..%2fsecret.txt", "/..\\secret.txt", "/assets\\..\\..\\secret.txt",
                  "/secret.txt%00.js", "//../secret.txt", "/%252e%252e/secret.txt"):
            got = self.r(p)
            self.assertTrue(got is None or open(got).read() != "TOP SECRET", f"leaked via {p}")
            if got is not None:
                self.assertTrue(os.path.realpath(got).startswith(os.path.realpath(self.dist)), p)

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_symlink_pointing_outside_the_root_is_refused(self):
        os.symlink(os.path.join(self.tmp.name, "secret.txt"), os.path.join(self.dist, "assets", "link.txt"))
        os.symlink(self.tmp.name, os.path.join(self.dist, "escape"))
        self.assertIsNone(self.r("/assets/link.txt"))
        self.assertIsNone(self.r("/escape/secret.txt"))

    def test_content_types_and_cache_policy(self):
        js = describe(self.dist, self.r("/assets/app-abc123.js"))
        self.assertEqual(js[0], "application/javascript; charset=utf-8")
        self.assertIn("immutable", js[1])
        self.assertEqual(describe(self.dist, self.r("/assets/app-abc123.css"))[0], "text/css; charset=utf-8")
        html = describe(self.dist, self.r("/"))
        self.assertEqual(html, ("text/html; charset=utf-8", "no-cache"))  # a rebuild must show up on refresh
        self.assertEqual(describe(self.dist, self.r("/favicon.svg"))[1], "no-cache")


class TestServedOverHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dist = make_dist(cls.tmp.name)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler_class(Store(root_dir=os.path.join(cls.tmp.name, "data")), cls.dist))
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()

    def get(self, path, method="GET", body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request(method, path, body=body)
        r = c.getresponse()
        return r.status, dict(r.getheaders()), r.read()

    def test_root_deep_link_and_asset(self):
        s, h, b = self.get("/")
        self.assertEqual((s, h["Content-Type"]), (200, "text/html; charset=utf-8"))
        self.assertIn(b"<div id=root>", b)
        self.assertEqual(self.get("/experiments/abc123")[2], b)  # deep link -> same index
        s, h, b = self.get("/assets/app-abc123.js")
        self.assertEqual((s, b), (200, b"console.log('hi')"))
        self.assertIn("immutable", h["Cache-Control"])

    def test_api_still_answers_json_and_unknown_api_is_json_404(self):
        s, h, b = self.get("/api/health")
        self.assertEqual((s, json.loads(b)), (200, {"status": "ok"}))
        s, h, b = self.get("/api/nope")
        self.assertEqual(s, 404)
        self.assertEqual(h["Content-Type"], "application/json")

    def test_websocket_still_works_on_the_same_port(self):
        c = WSClient("127.0.0.1", self.port, timeout=5)
        self.addCleanup(c.close)
        self.assertEqual(c.recv()["type"], "hello")

    def test_head_matches_get_headers_without_a_body(self):
        s, h, b = self.get("/assets/app-abc123.js", method="HEAD")
        self.assertEqual((s, b), (200, b""))
        self.assertEqual(h["Content-Length"], str(len("console.log('hi')")))

    def test_traversal_over_http_never_returns_the_secret(self):
        for p in ("/../secret.txt", "/%2e%2e/secret.txt", "/assets/../../secret.txt", "/..%2fsecret.txt"):
            s, _, b = self.get(p)
            self.assertNotIn(b"TOP SECRET", b, p)

    def test_post_to_a_ui_path_is_not_served_as_html(self):
        s, _, b = self.get("/", method="POST", body="{}")
        self.assertNotIn(b"<div id=root>", b)

    def test_server_without_static_dir_behaves_as_before(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler_class(Store(root_dir=os.path.join(self.tmp.name, "d2"))))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
        c.request("GET", "/")
        self.assertEqual(c.getresponse().status, 404)


class TestLauncher(unittest.TestCase):
    def free_port(self):
        s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

    def launch(self, *args, cwd):
        return subprocess.Popen([sys.executable, "-m", "backend.api", *args], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env={**os.environ, "PYTHONPATH": ROOT}, text=True)

    def wait_up(self, port):
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close(); return
            except OSError:
                time.sleep(0.1)
        self.fail("server did not start")

    def test_starts_from_any_directory_stores_data_where_told_and_exits_cleanly_on_sigint_with_a_live_websocket(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = make_dist(tmp)
            port, data = self.free_port(), os.path.join(tmp, "mydata")
            p = self.launch(str(port), "--frontend", dist, "--data", data, cwd=tmp)  # cwd is NOT the project
            try:
                self.wait_up(port)
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                c.request("POST", "/api/scenarios", body=json.dumps({"name": "x"}), headers={"Content-Type": "application/json"})
                self.assertEqual(c.getresponse().status, 201)
                self.assertTrue(os.path.isdir(data), "data should go where --data says")
                self.assertFalse(os.path.exists(os.path.join(tmp, "data")), "must not create data/ in the cwd")
                ws = WSClient("127.0.0.1", port, timeout=5)  # open session held during shutdown
                ws.recv()
                p.send_signal(signal.SIGINT)
                self.assertEqual(p.wait(timeout=5), 0, "should exit promptly and cleanly, not hang on the open socket")
                ws.close()
            finally:
                if p.poll() is None: p.kill()

    def test_port_in_use_gives_a_readable_error_not_a_traceback(self):
        s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(); port = s.getsockname()[1]
        try:
            p = self.launch(str(port), "--no-frontend", cwd=ROOT)
            out, err = p.communicate(timeout=10)
        finally:
            s.close()
        self.assertEqual(p.returncode, 1)
        self.assertIn("another server", err)
        self.assertNotIn("Traceback", err)

    def test_missing_frontend_build_prints_how_to_build_it_and_still_serves_the_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            port = self.free_port()
            p = self.launch(str(port), "--frontend", os.path.join(tmp, "nope"), "--data", os.path.join(tmp, "d"), cwd=tmp)
            try:
                self.wait_up(port)
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                c.request("GET", "/api/health")
                self.assertEqual(c.getresponse().status, 200)
                p.send_signal(signal.SIGINT); p.wait(timeout=5)
                self.assertIn("npm run build", p.stderr.read())
            finally:
                if p.poll() is None: p.kill()


if __name__ == "__main__":
    unittest.main()
