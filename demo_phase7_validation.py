"""
Phase 7 demo — the validation layer in action: a clean, real experiment run
(should report zero issues), then a deliberately corrupted synthetic event
log to prove the checks actually catch what they claim to.

Run: python3 demo_phase7_validation.py
"""

from backend.scenarios.config import ScenarioConfig
from backend.simulation.failures.schedule import node_failure_scenario
from backend.experiments.runner import ExperimentRunner
from backend.simulation.events.event import Event, EventLog, EventType
from backend.validation.engine import ValidationEngine

print("=== Part 1: validation report on a real, clean multi-run experiment ===\n")

scenario = ScenarioConfig(
    name="Validation demo",
    num_nodes=20, simulation_duration_s=40.0, traffic_rate_pps=1.0,
    failure_events=node_failure_scenario("N8", fail_at=10.0, restore_at=25.0),
    algorithms=["AEAR", "RSSI", "AODV"], num_runs=3, base_seed=3030,
)
result = ExperimentRunner(scenario).run(save_raw_events=False)

for run in result.runs:
    report = run.validation_report
    status = "VALID" if report.is_valid else "INVALID"
    print(f"  {run.algorithm} run {run.run_number}: {status} -- {report.summary()}")
    for issue in report.warnings:
        print(f"      [WARNING] {issue.code}: {issue.message}")

all_valid = all(r.validation_report.is_valid for r in result.runs)
print(f"\nAll {len(result.runs)} runs valid: {all_valid}")


print("\n=== Part 2: deliberately corrupted event log, to prove the checks work ===\n")

bad_log = EventLog()
# A node forwards a packet while it's supposed to be failed.
bad_log.append(Event(bad_log.next_event_id(), 1.0, EventType.NODE_FAILED, node_id="N5"))
bad_log.append(Event(bad_log.next_event_id(), 2.0, EventType.PACKET_FORWARDED,
                     node_id="N5", packet_id="P099", metadata={"to_node": "N6", "ttl_remaining": 5}))
# A packet revisits a node it already passed through.
bad_log.append(Event(bad_log.next_event_id(), 3.0, EventType.PACKET_FORWARDED,
                     node_id="N1", packet_id="P100", metadata={"to_node": "N2", "ttl_remaining": 8}))
bad_log.append(Event(bad_log.next_event_id(), 4.0, EventType.PACKET_FORWARDED,
                     node_id="N2", packet_id="P100", metadata={"to_node": "N1", "ttl_remaining": 7}))
# Bad AEAR weights slipped through somehow.
bad_log.append(Event(bad_log.next_event_id(), 5.0, EventType.CANDIDATE_EVALUATED,
                     node_id="N3", packet_id="P101", algorithm="AEAR",
                     metadata={"weights": {"signal": 0.6, "hop": 0.6, "congestion": 0.1}}))

report = ValidationEngine.validate(bad_log)
print(f"Valid: {report.is_valid}")
print(f"Summary: {report.summary()}\n")
for issue in report.issues:
    print(f"  [{issue.severity.value}] {issue.code}: {issue.message}")

print("\nEvery issue above was deliberately injected -- the point is that a real")
print("simulation run (Part 1) produces none of these, and if a future bug ever")
print("introduced one, this layer would catch it rather than silently corrupting results.")
