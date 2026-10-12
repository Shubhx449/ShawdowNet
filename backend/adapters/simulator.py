"""SimulatorAdapter: the discrete-event simulator behind the adapter interface."""

from __future__ import annotations

from backend.adapters.base import AdapterCapabilities, AdapterError, CommunicationAdapter
from backend.experiments.runner import ExperimentRunner
from backend.live.session import LiveSession, SessionError
from backend.scenarios.config import ScenarioConfig


class SimulatorAdapter(CommunicationAdapter):
    capabilities = AdapterCapabilities(
        kind="simulator", simulated_time=True, failure_injection=True, decision_trace=True,
        algorithms=tuple(ExperimentRunner.ROUTER_CLASSES))

    def __init__(self, scenario: ScenarioConfig, algorithm: str = "AEAR"):
        try:
            self.session = LiveSession(scenario, algorithm)
        except SessionError as e:
            raise AdapterError(str(e)) from None

    def _wrap(self, fn, *args, **kwargs):
        # LiveSession raises SessionError; callers of the adapter only know AdapterError
        try:
            return fn(*args, **kwargs)
        except SessionError as e:
            raise AdapterError(str(e)) from None

    def network_dict(self): return self.session.network_dict()
    def snapshot(self): return self.session.snapshot()
    def drain_events(self): return self.session.drain_events()
    def send_packet(self, *a, **kw): return self._wrap(self.session.send_packet, *a, **kw)
    def fail_node(self, n): return self._wrap(self.session.fail_node, n)
    def restore_node(self, n): return self._wrap(self.session.restore_node, n)
    def fail_link(self, a, b): return self._wrap(self.session.fail_link, a, b)
    def restore_link(self, a, b): return self._wrap(self.session.restore_link, a, b)
    def step(self, count=1): return self._wrap(self.session.step, count)
    def advance(self, dt): return self._wrap(self.session.advance, dt)
