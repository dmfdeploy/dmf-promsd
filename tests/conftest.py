from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class FakeSequenceClient:
    services: list[list[dict[str, Any]]]
    devices: list[list[dict[str, Any]]]
    virtual_machines: list[list[dict[str, Any]]]
    calls: list[str]

    def _next(self, series: list[list[dict[str, Any]]], label: str) -> list[dict[str, Any]]:
        self.calls.append(label)
        if not series:
            return []
        if len(series) == 1:
            return series[0]
        return series.pop(0)

    def list_services(self, *, tags: tuple[str, ...]) -> list[dict[str, Any]]:
        return self._next(self.services, f"services:{','.join(tags)}")

    def list_devices(self, *, tags: tuple[str, ...]) -> list[dict[str, Any]]:
        return self._next(self.devices, f"devices:{','.join(tags)}")

    def list_virtual_machines(self, *, tags: tuple[str, ...]) -> list[dict[str, Any]]:
        return self._next(self.virtual_machines, f"virtual_machines:{','.join(tags)}")

    def close(self) -> None:
        return None


@dataclass
class StaticCache:
    snapshot_payloads: list[dict[str, Any]]
    readiness: tuple[int, dict[str, Any]]
    health: dict[str, Any]
    _managed: bool = False

    def health_payload(self) -> dict[str, Any]:
        return self.health

    def ready_payload(self) -> tuple[int, dict[str, Any]]:
        return self.readiness

    def lane_payload(self, lane: str) -> list[dict[str, Any]]:
        return self.snapshot_payloads
