from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from dmf_promsd.cache import CacheSnapshot, CacheState


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

    def list_tagged(self, kind: str, tags: Iterable[str]) -> list[dict[str, Any]]:
        return self._next(getattr(self, kind), f"{kind}:{','.join(tags)}")

    def close(self) -> None:
        return None


@dataclass
class StaticCache:
    snapshot_payloads: list[dict[str, Any]]
    ttl_seconds: int = 45
    refresh_hint: str = "30s"
    freshness_window_seconds: int = 90

    def state(self) -> CacheState:
        snapshot = CacheSnapshot(refreshed_at=0.0, lanes={}, skipped={}, source_counts={})
        return CacheState(snapshot=snapshot, age_seconds=0.0, fresh=True, last_error=None)

    def lane_payload(self, lane: str) -> list[dict[str, Any]]:
        return self.snapshot_payloads
