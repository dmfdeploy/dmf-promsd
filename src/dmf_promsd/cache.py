from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .netbox import NetboxClient
from .sd import LANES, RECORD_KINDS, build_payloads


@dataclass(frozen=True)
class CacheSnapshot:
    refreshed_at: float
    lanes: dict[str, list[dict[str, Any]]]
    skipped: dict[str, dict[str, int]]
    source_counts: dict[str, int]


@dataclass(frozen=True)
class CacheState:
    snapshot: CacheSnapshot | None
    age_seconds: float | None
    fresh: bool
    last_error: str | None


class PromSDCache:
    def __init__(
        self,
        *,
        client: NetboxClient,
        ttl_seconds: int = 45,
        refresh_hint: str = "30s",
        freshness_window_seconds: int | None = None,
        clock: Callable[[], float] = time.monotonic,
        managed: bool = True,
    ) -> None:
        self._client = client
        self.ttl_seconds = ttl_seconds
        self.refresh_hint = refresh_hint
        self.freshness_window_seconds = freshness_window_seconds or (ttl_seconds * 2)
        self._clock = clock
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
        while not self._stop.wait(self.ttl_seconds):
            self.refresh()

    def refresh(self) -> bool:
        try:
            records = {
                kind: self._client.list_tagged(
                    kind, [lane.tag for lane in LANES if kind in lane.kinds]
                )
                for kind in RECORD_KINDS
            }
            payloads, skipped, source_counts = build_payloads(records)
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

    def state(self) -> CacheState:
        with self._lock:
            snapshot = self._snapshot
            last_error = self._last_error
        if snapshot is None:
            return CacheState(snapshot=None, age_seconds=None, fresh=False, last_error=last_error)
        age = self._clock() - snapshot.refreshed_at
        return CacheState(
            snapshot=snapshot,
            age_seconds=age,
            fresh=age <= self.freshness_window_seconds,
            last_error=last_error,
        )

    def lane_payload(self, lane: str) -> list[dict[str, Any]]:
        snapshot = self.get_snapshot()
        if snapshot is None:
            return []
        return snapshot.lanes.get(lane, [])
