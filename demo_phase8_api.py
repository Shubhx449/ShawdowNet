"""
Phase 8 demo -- the Mode 2 REST API, used the way the React frontend will use
it: submit an experiment, poll per-algorithm progress, then read the results.

Run: python3 demo_phase8_api.py

(To run the server on its own instead: `python3 -m backend.api 8000`, then use
curl or a browser against http://127.0.0.1:8000/api/health)
"""

import json
import shutil
import tempfile
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

from backend.api.server import make_handler_class
from backend.api.store import Store


def call(port, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


tmp = tempfile.mkdtemp()
server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler_class(Store(root_dir=tmp)))
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()

try:
    print(f"API up on port {port}\n")

    status, defaults = call(port, "GET", "/api/defaults")
    print(f"GET /api/defaults -> {status}: paper defaults = {defaults['num_nodes']} nodes, "
          f"{defaults['communication_range_m']} m range, {defaults['simulation_duration_s']} s\n")

    scenario = dict(defaults, name="API demo (paper defaults, 4 runs)", num_runs=4, base_seed=2024)
    status, body = call(port, "POST", "/api/experiments/run", {"scenario": scenario, "async": True})
    experiment_id = body["experiment_id"]
    print(f"POST /api/experiments/run (async) -> {status}: {experiment_id}\n")

    status, early = call(port, "GET", f"/api/experiments/{experiment_id}/metrics")
    print(f"Asking for metrics immediately -> {status}: {early['error']}\n")

    print("Polling status:")
    while True:
        _, st = call(port, "GET", f"/api/experiments/{experiment_id}/status")
        bars = "  ".join(f"{a} {p['completed']}/{p['total']}" for a, p in st["progress"].items())
        print(f"  {st['status']:<9} {bars}")
        if st["status"] in ("completed", "failed"):
            break
        time.sleep(0.7)

    print()
    _, graphs = call(port, "GET", f"/api/experiments/{experiment_id}/graphs")
    print("Chart data from GET /api/experiments/{id}/graphs (mean +/- std over 4 runs):\n")
    for metric, label in [("pdr_percent", "PDR (%)"), ("emergency_pdr_percent", "Emergency PDR (%)"),
                          ("avg_hop_count", "Avg hop count"),
                          ("routing_overhead_control_messages", "Routing overhead (msgs)")]:
        cells = "   ".join(f"{p['algorithm']}: {p['mean']:.1f} +/- {p['std']:.1f}" for p in graphs["metrics"][metric])
        print(f"  {label:<24} {cells}")

    _, results = call(port, "GET", f"/api/experiments/{experiment_id}/results")
    bad = [(a, r["run_number"]) for a, runs in results["results"].items() for r in runs
           if not r["validation"]["is_valid"]]
    total = sum(len(runs) for runs in results["results"].values())
    print(f"\nValidation (Phase 7) travelled with the results: {total - len(bad)}/{total} runs valid"
          + (f"; INVALID: {bad}" if bad else ""))

    status, err = call(port, "POST", "/api/experiments/run",
                       {"scenario": dict(defaults, normal_ratio=0.9), "async": True})
    print(f"\nBad scenario (ratios sum to 1.2) -> {status}: {err['error']}")
finally:
    server.shutdown()
    server.server_close()
    shutil.rmtree(tmp, ignore_errors=True)
