"""
Phase 9 demo: drive a Mode 1 live session over a real WebSocket, the way the
React frontend will. Starts the server in-process on a free port.

    python3 demo_phase9_live.py
"""

import tempfile
import threading
import time
from http.server import ThreadingHTTPServer

from backend.api.server import make_handler_class
from backend.api.store import Store
from backend.ws.client import WSClient


def drain(c, seconds):
    """Collect every event streamed within `seconds`."""
    c.sock.settimeout(0.3)
    out, end = [], time.time() + seconds
    while time.time() < end:
        m = c.recv()
        if m and m["type"] == "events":
            out += m["events"]
    return out


def show(events):
    for e in events:
        t, md = e["event_type"], e["metadata"]
        if t == "FORWARDING_DECISION":
            print(f"  t={e['timestamp']:.3f}  {e['node_id']}: decision -> {md.get('selected')}")
        elif t in ("PACKET_DELIVERED", "PACKET_BUFFERED", "NODE_FAILED", "NODE_RECOVERED"):
            print(f"  t={e['timestamp']:.3f}  {t} {e['node_id']} {e['packet_id'] or ''}")


server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler_class(Store(root_dir=tempfile.mkdtemp())))
threading.Thread(target=server.serve_forever, daemon=True).start()
c = WSClient("127.0.0.1", server.server_address[1])
print("connected:", c.recv())

c.send({"type": "session.create", "algorithm": "AEAR", "scenario": {"num_nodes": 15, "base_seed": 7}})
created = c.recv_until("session.created")
print(f"session: {len(created['network']['nodes'])} nodes, {len(created['network']['links'])} links")

print("\n-- Play an EMERGENCY packet N1 -> N9 at 100x")
c.send({"type": "packet.send", "source": "N1", "destination": "N9", "packet_type": "EMERGENCY"})
c.send({"type": "sim.play", "speed": 100})
events = drain(c, 1.0)
show(events)
first_eval = next(e for e in events if e["event_type"] == "CANDIDATE_EVALUATED")
print("\n  one CANDIDATE_EVALUATED (what the decision panel shows):")
print("  ", {k: (round(v, 3) if isinstance(v, float) else v) for k, v in first_eval["metadata"].items()})

c.send({"type": "sim.pause"})
c.send({"type": "state.get"})
state = c.recv_until("state")["state"]
p = state["packets"]["P001"]
print(f"\n  P001 is {p['status']} at {p['current_node']} after path {p['path']}")

print("\n-- Fail every neighbour of N2, send a packet from N2, then restore one")
neighbours = [n for n in created["network"]["links"] if "N2" in (n["node_a"], n["node_b"])]
ids = [l["node_b"] if l["node_a"] == "N2" else l["node_a"] for l in neighbours]
for n in ids:
    c.send({"type": "failure.node_fail", "node": n})
c.send({"type": "packet.send", "source": "N2", "destination": "N8"})
c.send({"type": "sim.play", "speed": 100})
show(drain(c, 0.8))
c.send({"type": "failure.node_restore", "node": ids[0]})
show(drain(c, 0.8))
c.close()
server.shutdown()
