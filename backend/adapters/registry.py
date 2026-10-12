"""Adapter registry: maps a `kind` string to an adapter class."""

from __future__ import annotations

from typing import Dict, List, Type

from backend.adapters.base import AdapterError, CommunicationAdapter
from backend.adapters.lora import LoRaAdapter
from backend.adapters.simulator import SimulatorAdapter

_ADAPTERS: Dict[str, Type[CommunicationAdapter]] = {"simulator": SimulatorAdapter, "lora": LoRaAdapter}
DEFAULT_KIND = "simulator"


def register_adapter(kind: str, cls: Type[CommunicationAdapter]) -> None:
    if not (isinstance(cls, type) and issubclass(cls, CommunicationAdapter)):
        raise TypeError("adapter must subclass CommunicationAdapter")
    _ADAPTERS[kind] = cls


def create_adapter(kind, scenario, algorithm: str = "AEAR") -> CommunicationAdapter:
    if not isinstance(kind, str) or kind not in _ADAPTERS:
        raise AdapterError(f"Unknown adapter: {kind!r} (available: {sorted(_ADAPTERS)})")
    return _ADAPTERS[kind](scenario, algorithm)


def list_adapters() -> List[dict]:
    out = []
    for kind, cls in _ADAPTERS.items():
        entry = cls.capabilities.to_dict()
        entry["kind"] = kind
        entry["available"] = cls.available
        out.append(entry)
    return out
