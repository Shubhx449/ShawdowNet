"""
Regenerate the fixtures the frontend tests read (frontend/src/__fixtures__/*.json) from the REAL routers.

    python3 scripts/make_frontend_fixtures.py

tests/test_phase15_fixtures.py fails when these files no longer match what the routers produce, so a
router change can't leave the JavaScript scoring mirror (frontend/src/algorithms.js) silently out of date.
"""

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.live.session import LiveSession
from backend.scenarios.config import ScenarioConfig

OUT = os.path.join(os.path.dirname(__file__), "..", "frontend", "src", "__fixtures__")


def generate() -> dict:
    files = {}
    # one packet end to end per algorithm: for the decision-grouping tests
    for alg in ("AEAR", "RSSI", "AODV"):
        s = LiveSession(ScenarioConfig.from_dict({"num_nodes": 15, "base_seed": 7}), alg)
        s.send_packet("N1", "N9", "EMERGENCY")
        s.run_until_idle()
        files[f"events_{alg.lower()}.json"] = s.drain_events()
    # candidate inputs + reported scores across priorities and seeds: for the scoring-parity test
    cands = []
    for seed in (1, 2):
        for ptype in ("NORMAL", "IMPORTANT", "EMERGENCY"):
            s = LiveSession(ScenarioConfig.from_dict({"num_nodes": 25, "base_seed": seed}), "AEAR")
            for a, b in (("N1", "N9"), ("N4", "N20"), ("N7", "N15")):
                s.send_packet(a, b, ptype)
            s.run_until_idle()
            for e in s.drain_events():
                if e["event_type"] == "CANDIDATE_EVALUATED":
                    cands.append({"group": f"{seed}-{ptype}-{e['packet_id']}-{e['node_id']}", **e["metadata"]})
    files["aear_candidates.json"] = cands
    return files


def generate_ui_fixtures() -> dict:
    """Response bodies exactly as the HTTP API sends them (produced by calling the real handlers), plus the
    WebSocket messages of a real Mode 1 session, for the page render tests. Contains ids/timestamps, so it is
    compared by SHAPE only (tests/test_phase15_fixtures.py), not byte-for-byte."""
    import tempfile
    from backend.api import handlers
    from backend.api.store import Store
    from backend.adapters.registry import create_adapter

    store = Store(root_dir=tempfile.mkdtemp())
    call = lambda fn, params=None, body=None: fn(store, params or {}, body)[1]
    scenario = {**call(handlers.defaults), "name": "ui fixture", "num_nodes": 15, "num_runs": 2,
                "simulation_duration_s": 20, "base_seed": 7}
    sid = call(handlers.create_scenario, body=scenario)["id"]
    eid = store.run_experiment(scenario_id=sid, inline_scenario=None, save_raw_events=False)
    p = {"id": eid}
    files = {
        "api_defaults.json": call(handlers.defaults), "api_adapters.json": call(handlers.list_adapters),
        "api_scenarios.json": call(handlers.list_scenarios), "api_experiments.json": call(handlers.list_experiments),
        "api_status_completed.json": call(handlers.get_experiment_status, p),
        "api_metrics.json": call(handlers.get_experiment_metrics, p), "api_graphs.json": call(handlers.get_experiment_graphs, p),
        "api_results.json": call(handlers.get_experiment_results, p), "api_experiment.json": call(handlers.get_experiment, p),
    }
    # a real live session: what the server sends on create, then after sending an emergency packet and stepping
    s = LiveSession(ScenarioConfig.from_dict({"num_nodes": 15, "base_seed": 7}), "AEAR")
    ad = create_adapter("simulator", ScenarioConfig.from_dict({"num_nodes": 15, "base_seed": 7}), "AEAR")
    created = {"type": "session.created", "adapter": ad.capabilities.to_dict(), "network": ad.network_dict(), "state": ad.snapshot()}
    ad.send_packet("N1", "N9", "EMERGENCY")
    ad.step(10_000)
    files["ws_session_created.json"] = created
    files["ws_events.json"] = {"type": "events", "events": ad.drain_events(), "state": ad.snapshot()}
    return files


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    ui = generate_ui_fixtures()
    for name, data in {**generate(), **ui}.items():
        with open(os.path.join(OUT, name), "w") as f:
            # router fixtures are byte-compared, so sort them; UI fixtures keep the server's own key order
            # (the results table's column order comes from it)
            json.dump(data, f, sort_keys=name not in ui, indent=0)
        print(f"wrote {name} ({len(data)} items)")
