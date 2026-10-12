"""
CommunicationAdapter -- the seam between "what Mode 1 asks for" and "what
actually carries the packets".

Mode 1's message layer (backend/ws/live_handler.py) talks only to this
interface. Today the only working implementation is SimulatorAdapter (the
discrete-event engine). A LoRa adapter would implement the same operations
over real radios, so the WebSocket protocol and the UI would not change.

The interface is deliberately the same shape LiveSession already had, so
Phase 9 code moved over with almost no edits. It is NOT a guess at the
final hardware API: real radios have no simulated clock, can't "step", and
can't fail a link on demand. Rather than pretend, each adapter declares
`capabilities`, and the message layer refuses commands the adapter can't do
with a clear error instead of silently faking them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import List, Optional


class AdapterError(Exception):
    """A bad command for this adapter (unknown node, wrong state...). Reported to the client."""


class AdapterUnavailable(AdapterError):
    """The adapter exists but cannot be used right now (e.g. no hardware attached)."""


class UnsupportedOperation(AdapterError):
    """The adapter does not support this operation (see `capabilities`)."""


@dataclass(frozen=True)
class AdapterCapabilities:
    """What an adapter can honestly do. Sent to the client in session.created."""
    kind: str
    simulated_time: bool        # has a controllable clock: step / advance / play are meaningful
    failure_injection: bool     # fail/restore node or link on demand
    decision_trace: bool        # emits NEIGHBOR_DISCOVERED / CANDIDATE_EVALUATED / FORWARDING_DECISION
    algorithms: tuple           # routing algorithms selectable at session creation

    def to_dict(self) -> dict:
        d = asdict(self)
        d["algorithms"] = list(self.algorithms)
        return d


class CommunicationAdapter(ABC):
    capabilities: AdapterCapabilities  # set by each subclass
    available: bool = True  # False for stubs whose constructor always raises AdapterUnavailable

    # -- topology / state --------------------------------------------------------
    @abstractmethod
    def network_dict(self) -> dict: ...

    @abstractmethod
    def snapshot(self) -> dict: ...

    @abstractmethod
    def drain_events(self) -> List[dict]:
        """Events since the previous call, in order, as plain dicts."""

    # -- actions -----------------------------------------------------------------
    @abstractmethod
    def send_packet(self, source: str, destination: str, packet_type: str = "NORMAL",
                    ttl: Optional[int] = None, message: str = "") -> dict: ...

    @abstractmethod
    def fail_node(self, node_id: str) -> None: ...

    @abstractmethod
    def restore_node(self, node_id: str) -> None: ...

    @abstractmethod
    def fail_link(self, node_a: str, node_b: str) -> None: ...

    @abstractmethod
    def restore_link(self, node_a: str, node_b: str) -> None: ...

    # -- time (only meaningful when capabilities.simulated_time) -----------------
    @abstractmethod
    def step(self, count: int = 1) -> int: ...

    @abstractmethod
    def advance(self, dt: float) -> int: ...

    def close(self) -> None:
        """Release any resources (serial ports, threads). Default: nothing to release."""
