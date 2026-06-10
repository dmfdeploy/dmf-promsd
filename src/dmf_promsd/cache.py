from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .netbox import NetboxClient
from .sd import build_payloads


@dataclass(frozen=True)
class CacheSnapshot:
    refreshed_at: float
    lanes: dict[str, list[dict[str, Any]]]
    skipped: dict[str, dict[str, int]]
    source_counts: dict[str, int]


class PromSDCache:
    def __init__(
        self,
        *,
        client: NetboxClient,
        ttl_seconds: int = 45,
        refresh_hint: str = "30s",
        freshness_window_seconds: int | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        managed: bool = True,
    ) -> None:
        self._client = client
        self._ttl_seconds = ttl_seconds
        self._refresh_hint = refresh_hint
        self._freshness_window_seconds = freshness_window_seconds or (ttl_seconds * 2)
        self._clock = clock
        self._sleeper = sleeper
        self._managed = managed
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._snapshot: CacheSnapshot | None = None
        self._last_error: str | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self.refresh()
        self._stop.clear()
        thread = threading.Thread(target=self._run, name="dmf-promsd-cache", daemon=True)
        thread.start()
        self._thread = thread

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None

    def _run(self) -> None:
        while not self._stop.wait(self._ttl_seconds):
            self.refresh()

    @staticmethod
    def _fetch_union(list_fn, tags):
        # NetBox ?tag= filters are ANDed; query each tag separately and union by
        # id so an object tagged with ANY monitoring lane is included (OR).
        merged: dict[Any, dict[str, Any]] = {}
        for tag in tags:
            for obj in list_fn(tags=(tag,)):
                merged[obj.get("id", id(obj))] = obj
        return list(merged.values())

    def refresh(self) -> bool:
        try:
            services = self._fetch_union(
                self._client.list_services, ("monitoring:scrape", "monitoring:probe")
            )
            devices = self._fetch_union(
                self._client.list_devices,
                ("monitoring:scrape", "monitoring:probe", "monitoring:snmp"),
            )
            virtual_machines = self._fetch_union(
                self._client.list_virtual_machines, ("monitoring:scrape", "monitoring:probe")
            )
            payloads, skipped, source_counts = build_payloads(services, devices, virtual_machines)
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
            return False

        snapshot = CacheSnapshot(
            refreshed_at=self._clock(),
            lanes=payloads,
            skipped=skipped,
            source_counts=source_counts,
        )
        with self._lock:
            self._snapshot = snapshot
            self._last_error = None
        return True

    def get_snapshot(self) -> CacheSnapshot | None:
        with self._lock:
            return self._snapshot

    def is_ready(self) -> bool:
        snapshot = self.get_snapshot()
        if snapshot is None:
            return False
        return (self._clock() - snapshot.refreshed_at) <= self._freshness_window_seconds

    def lane_payload(self, lane: str) -> list[dict[str, Any]]:
        snapshot = self.get_snapshot()
        if snapshot is None:
            return []
        return snapshot.lanes.get(lane, [])

    def health_payload(self) -> dict[str, Any]:
        snapshot = self.get_snapshot()
        age = None if snapshot is None else self._clock() - snapshot.refreshed_at
        return {
            "status": "ok",
            "cache_ttl_seconds": self._ttl_seconds,
            "http_sd_refresh": self._refresh_hint,
            "freshness_window_seconds": self._freshness_window_seconds,
            "snapshot_age_seconds": age,
            "source_counts": snapshot.source_counts if snapshot else {},
            "last_error": self._last_error,
        }

    def ready_payload(self) -> tuple[int, dict[str, Any]]:
        snapshot = self.get_snapshot()
        if snapshot is None:
            return 503, {"status": "stale", "ready": False, "reason": "no snapshot"}
        age = self._clock() - snapshot.refreshed_at
        ready = age <= self._freshness_window_seconds
        status_code = 200 if ready else 503
        payload = {
            "status": "ready" if ready else "stale",
            "ready": ready,
            "snapshot_age_seconds": age,
            "freshness_window_seconds": self._freshness_window_seconds,
            "last_error": self._last_error,
        }
        return status_code, payload
