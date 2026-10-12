"""
LoRaAdapter -- a STUB. There is no hardware integration in this project yet
(the hardware budget was not approved; see README). This class exists so the
extension point is real and testable:

  * it is registered under kind "lora" and appears in /api/adapters,
  * its capabilities describe what a radio adapter could and couldn't do,
  * constructing it raises AdapterUnavailable with an explanation, so a
    client asking for it gets a clear error rather than a fake simulation.

To implement it later: fill in the abstract methods over your radio driver,
emit events in the same dict shape the simulator does (event_id, timestamp,
event_type, node_id, packet_id, algorithm, metadata), and drop the raise in
__init__. Nothing in the WebSocket layer or the frontend needs to change.
Expect step()/advance() to raise UnsupportedOperation (wall-clock only) and
failure injection to be limited to what the hardware allows (e.g. asking a
node to stop transmitting, not severing a radio link).
"""

from __future__ import annotations

from backend.adapters.base import (AdapterCapabilities, AdapterUnavailable, CommunicationAdapter,
                                   UnsupportedOperation)


class LoRaAdapter(CommunicationAdapter):
    available = False
    capabilities = AdapterCapabilities(
        kind="lora", simulated_time=False, failure_injection=False, decision_trace=False,
        algorithms=("AEAR",))

    def __init__(self, scenario=None, algorithm: str = "AEAR"):
        raise AdapterUnavailable(
            "The LoRa adapter is a stub: no hardware integration exists yet. "
            "Use the 'simulator' adapter.")

    def _nope(self, *a, **kw):
        raise UnsupportedOperation("LoRa adapter is not implemented")

    network_dict = snapshot = drain_events = send_packet = _nope
    fail_node = restore_node = fail_link = restore_link = step = advance = _nope
