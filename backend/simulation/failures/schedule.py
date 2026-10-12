"""
Declarative failure scheduling.

A small helper so failure/restoration timing can be described as data (a
list of FailureEvent) rather than a sequence of scattered engine calls --
this is exactly the shape Mode 2's Scenario Manager (Phase 14) and the
paper's own Scenario 4 ("Forwarding Node Failure During Transmission") and
Scenario 5 ("Temporary Link Disconnection Followed by Restoration") need:
one reusable, serializable description of "what fails, when, and for how
long" that can be replayed identically across AEAR/RSSI/AODV runs for a
fair comparison.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple, Union

from backend.simulation.engine.engine import SimulationEngine

Target = Union[str, Tuple[str, str]]


@dataclass
class FailureEvent:
    """One scheduled failure/restoration action."""

    time: float
    action: str  # "fail_node" | "restore_node" | "fail_link" | "restore_link"
    target: Target  # node_id for node actions, (node_a, node_b) for link actions

    def to_dict(self) -> dict:
        return {"time": self.time, "action": self.action, "target": self.target}


def apply_failure_schedule(engine: SimulationEngine, events: List[FailureEvent]) -> None:
    """Schedule every event in `events` onto `engine`. Does not run the engine."""
    for e in events:
        if e.action == "fail_node":
            engine.fail_node_at(e.time, e.target)  # type: ignore[arg-type]
        elif e.action == "restore_node":
            engine.restore_node_at(e.time, e.target)  # type: ignore[arg-type]
        elif e.action == "fail_link":
            a, b = e.target  # type: ignore[misc]
            engine.fail_link_at(e.time, a, b)
        elif e.action == "restore_link":
            a, b = e.target  # type: ignore[misc]
            engine.restore_link_at(e.time, a, b)
        else:
            raise ValueError(f"Unknown failure action: {e.action}")


def node_failure_scenario(node_id: str, fail_at: float,
                           restore_at: Optional[float] = None) -> List[FailureEvent]:
    """
    Paper Scenario 4: "Forwarding Node Failure During Transmission".
    Fails `node_id` at `fail_at`; optionally restores it at `restore_at`.
    """
    events = [FailureEvent(time=fail_at, action="fail_node", target=node_id)]
    if restore_at is not None:
        if restore_at <= fail_at:
            raise ValueError("restore_at must be after fail_at")
        events.append(FailureEvent(time=restore_at, action="restore_node", target=node_id))
    return events


def link_disconnection_scenario(node_a: str, node_b: str, fail_at: float,
                                 restore_at: float) -> List[FailureEvent]:
    """
    Paper Scenario 5: "Temporary Link Disconnection Followed by Restoration".
    Disconnects the node_a<->node_b link at `fail_at`, restores it at
    `restore_at`.
    """
    if restore_at <= fail_at:
        raise ValueError("restore_at must be after fail_at")
    return [
        FailureEvent(time=fail_at, action="fail_link", target=(node_a, node_b)),
        FailureEvent(time=restore_at, action="restore_link", target=(node_a, node_b)),
    ]
