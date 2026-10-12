"""
Phase 10 demo: the adapter layer. Lists adapters, drives the simulator
through the interface, then shows what asking for LoRa looks like.

    python3 demo_phase10_adapters.py
"""

from backend.adapters.base import AdapterError
from backend.adapters.registry import create_adapter, list_adapters
from backend.scenarios.config import ScenarioConfig

print("Adapters:")
for a in list_adapters():
    print(f"  {a['kind']:<10} available={a['available']!s:<5} simulated_time={a['simulated_time']!s:<5} "
          f"failure_injection={a['failure_injection']}")

scenario = ScenarioConfig.from_dict({"num_nodes": 15, "base_seed": 7})
adapter = create_adapter("simulator", scenario, "AEAR")
adapter.send_packet("N1", "N9", "EMERGENCY")
adapter.step(10_000)
events = adapter.drain_events()
print(f"\nSimulator via the interface: {len(events)} events, "
      f"P001 is {adapter.snapshot()['packets']['P001']['status']}")

print("\nAsking for LoRa:")
try:
    create_adapter("lora", scenario)
except AdapterError as e:
    print(f"  {type(e).__name__}: {e}")
