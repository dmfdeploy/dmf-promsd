"""Strict projection of OpenBao rotation metadata into safe metric values."""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Gauge, generate_latest

from .settings import ObserverSettings

TARGET_ROLES = {
    "external-secrets": "external-secrets",
    "object-storage-openbao-snapshots": "object-storage-openbao-snapshots",
    "object-storage-app-backups": "object-storage-app-backups",
}
TARGET_TTLS = {
    "external-secrets": 2_592_000,
    "object-storage-openbao-snapshots": 31_536_000,
    "object-storage-app-backups": 31_536_000,
}
REASON_CODES = {
    "create_failed",
    "lookup_failed",
    "delivery_failed",
    "readiness_failed",
    "cleanup_failed",
    "metadata_failed",
    "lease_conflict",
    "invalid_state",
}
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class MetadataError(ValueError):
    """Raised when metadata does not match the supported schema."""


@dataclass(frozen=True)
class RotationRecord:
    role: str
    target: str
    expires_at: int
    ttl_seconds: int
    last_successful_rotation_at: int | None
    last_failure_at: int | None
    last_failure_reason_code: str | None
    latest_run_kind: str
    latest_run_outcome: str
    latest_run_at: int


_REQUIRED = {
    "schema_version",
    "target",
    "approle_name",
    "target_version",
    "secret_id_accessor",
    "issued_at",
    "expires_at",
    "ttl_seconds",
    "last_successful_rotation_at",
    "last_failure_at",
    "last_failure_reason_code",
    "latest_run_kind",
    "latest_run_outcome",
    "latest_run_at",
    "actor",
    "role",
    "request_id",
    "reason",
}


def _timestamp(value: Any, field: str, *, nullable: bool = False) -> int | None:
    if nullable and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise MetadataError(f"invalid {field}")
    return value


def _record_value(payload: Any, expected_target: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise MetadataError("invalid metadata envelope")
    value = payload["data"].get("data")
    if not isinstance(value, dict) or set(value) != _REQUIRED:
        raise MetadataError("unsupported metadata fields")
    if value["schema_version"] != 1 or isinstance(value["schema_version"], bool):
        raise MetadataError("unsupported metadata schema")
    if value["target"] != expected_target:
        raise MetadataError("metadata target mismatch")

    for field in ("approle_name", "actor", "role", "request_id", "reason"):
        if not isinstance(value[field], str) or not value[field]:
            raise MetadataError(f"invalid {field}")
    if (
        not isinstance(value["secret_id_accessor"], str)
        or not value["secret_id_accessor"]
        or len(value["secret_id_accessor"]) > 512
    ):
        raise MetadataError("invalid accessor")
    if _timestamp(value["target_version"], "target_version") == 0:
        raise MetadataError("invalid target version")
    return value


def _expiry_values(value: dict[str, Any]) -> tuple[int, int]:
    issued_at = _timestamp(value["issued_at"], "issued_at")
    expires_at = _timestamp(value["expires_at"], "expires_at")
    ttl_seconds = _timestamp(value["ttl_seconds"], "ttl_seconds")
    if not ttl_seconds or expires_at <= issued_at or expires_at - issued_at != ttl_seconds:
        raise MetadataError("inconsistent expiry")
    return expires_at, ttl_seconds


def _validate_target_contract(value: dict[str, Any], expected_target: str) -> None:
    if value["approle_name"] != TARGET_ROLES.get(expected_target):
        raise MetadataError("metadata role mismatch")
    if value["ttl_seconds"] != TARGET_TTLS.get(expected_target):
        raise MetadataError("metadata TTL mismatch")


def _failure_values(value: dict[str, Any]) -> tuple[int | None, str | None]:
    failure_at = _timestamp(value["last_failure_at"], "last_failure_at", nullable=True)
    failure_reason = value["last_failure_reason_code"]
    if (failure_at is None) != (failure_reason is None):
        raise MetadataError("incomplete failure metadata")
    if failure_reason is not None:
        if not isinstance(failure_reason, str):
            raise MetadataError("invalid failure reason code")
        failure_reason = failure_reason if failure_reason in REASON_CODES else "unknown"
    return failure_at, failure_reason


def _latest_run(value: dict[str, Any], failure_at: int | None) -> tuple[str, str, int]:
    if not isinstance(value["latest_run_kind"], str) or value["latest_run_kind"] not in {
        "scheduled",
        "manual",
    }:
        raise MetadataError("invalid run kind")
    if not isinstance(value["latest_run_outcome"], str) or value["latest_run_outcome"] not in {
        "success",
        "failure",
    }:
        raise MetadataError("invalid run outcome")
    latest_run_at = _timestamp(value["latest_run_at"], "latest_run_at")
    if value["latest_run_outcome"] == "failure" and (
        failure_at is None or failure_at != latest_run_at
    ):
        raise MetadataError("latest failure does not match failure metadata")
    return value["latest_run_kind"], value["latest_run_outcome"], latest_run_at


def parse_record(payload: Any, expected_target: str) -> RotationRecord:
    """Validate a KV-v2 response and return only fields needed for safe metrics."""
    value = _record_value(payload, expected_target)
    _validate_target_contract(value, expected_target)
    expires_at, ttl_seconds = _expiry_values(value)
    last_success = _timestamp(
        value["last_successful_rotation_at"], "last_successful_rotation_at", nullable=True
    )
    failure_at, failure_reason = _failure_values(value)
    run_kind, run_outcome, latest_run_at = _latest_run(value, failure_at)

    return RotationRecord(
        role=value["approle_name"],
        target=expected_target,
        expires_at=expires_at,
        ttl_seconds=ttl_seconds,
        last_successful_rotation_at=last_success,
        last_failure_at=failure_at,
        last_failure_reason_code=failure_reason,
        latest_run_kind=run_kind,
        latest_run_outcome=run_outcome,
        latest_run_at=latest_run_at,
    )


class OpenBaoObserver:
    """Poll fixed KV-v2 metadata paths and expose only bounded metric values."""

    def __init__(
        self,
        settings: ObserverSettings,
        *,
        client: httpx.Client | None = None,
        clock: Any = time.time,
    ) -> None:
        self.settings = settings
        verify = settings.ca_file if settings.configured else True
        self._client = client or httpx.Client(verify=verify, timeout=10.0)
        self._owns_client = client is None
        self._clock = clock
        self._token = ""
        self._auth_retry_used = False
        self._last_poll_at = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._registry = CollectorRegistry(auto_describe=True)
        self._expires = Gauge(
            "dmf_openbao_approle_secret_id_expires_at_seconds",
            "Expiry time of the active AppRole SecretID.",
            ("role", "target"),
            registry=self._registry,
        )
        self._ttl = Gauge(
            "dmf_openbao_approle_secret_id_ttl_seconds",
            "Declared lifetime of the active AppRole SecretID.",
            ("role", "target"),
            registry=self._registry,
        )
        self._last_success = Gauge(
            "dmf_openbao_approle_rotation_last_success_seconds",
            "Time of the last successful AppRole credential rotation.",
            ("role", "target"),
            registry=self._registry,
        )
        self._last_failure = Gauge(
            "dmf_openbao_approle_rotation_last_failure",
            "Whether a failure is recorded for the active AppRole credential.",
            ("role", "target", "reason"),
            registry=self._registry,
        )
        self._last_failure_timestamp = Gauge(
            "dmf_openbao_approle_rotation_last_failure_timestamp_seconds",
            "Time of the last failed AppRole credential rotation.",
            ("role", "target", "reason"),
            registry=self._registry,
        )
        self._latest_run = Gauge(
            "dmf_openbao_approle_rotation_latest_run_timestamp_seconds",
            "Time of the latest rotation run by kind and outcome.",
            ("role", "target", "kind", "outcome"),
            registry=self._registry,
        )
        self._available = Gauge(
            "dmf_openbao_approle_rotation_metadata_available",
            "Whether current metadata is readable and valid for a configured target.",
            ("target",),
            registry=self._registry,
        )
        self._collection_error = Gauge(
            "dmf_openbao_approle_rotation_collection_error",
            "Whether current metadata collection failed for a configured target.",
            ("target",),
            registry=self._registry,
        )
        self._collector_success = Gauge(
            "dmf_openbao_approle_rotation_collector_success",
            "Whether the latest complete observer poll succeeded.",
            registry=self._registry,
        )
        self._poll_timestamp = Gauge(
            "dmf_openbao_approle_rotation_collector_last_poll_timestamp_seconds",
            "Time of the latest observer poll.",
            registry=self._registry,
        )
        self._clear_series()

    def _clear_series(self) -> None:
        for metric in (
            self._expires,
            self._ttl,
            self._last_success,
            self._last_failure,
            self._last_failure_timestamp,
            self._latest_run,
        ):
            metric.clear()
        for target in TARGET_ROLES:
            self._available.labels(target).set(0)
            self._collection_error.labels(target).set(1)
        self._collector_success.set(0)

    def _login(self) -> str:
        if self._token:
            return self._token
        try:
            jwt = Path(self.settings.jwt_file).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            raise MetadataError("service account credential unavailable") from None
        if not jwt:
            raise MetadataError("service account credential unavailable")
        if not _SAFE_COMPONENT.fullmatch(self.settings.auth_mount) or not _SAFE_COMPONENT.fullmatch(
            self.settings.auth_role
        ):
            raise MetadataError("invalid observer authentication configuration")
        try:
            response = self._client.post(
                f"{self.settings.address.rstrip('/')}/v1/auth/{self.settings.auth_mount}/login",
                json={"role": self.settings.auth_role, "jwt": jwt},
            )
            if response.status_code != 200:
                raise MetadataError("observer authentication failed")
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            raise MetadataError("observer authentication failed") from None
        auth = payload.get("auth") if isinstance(payload, dict) else None
        token = auth.get("client_token") if isinstance(auth, dict) else None
        if not isinstance(token, str) or not token:
            raise MetadataError("observer authentication response invalid")
        self._token = token
        return token

    def _read_target(self, target: str) -> RotationRecord:
        token = self._login()
        path = f"/v1/secret/data/platform/approle-rotation/{target}"
        url = f"{self.settings.address.rstrip('/')}{path}"
        try:
            response = self._client.get(
                url,
                headers={"X-Vault-Token": token},
            )
            if response.status_code in {401, 403} and not self._auth_retry_used:
                self._auth_retry_used = True
                self._token = ""
                token = self._login()
                response = self._client.get(
                    url,
                    headers={"X-Vault-Token": token},
                )
            if response.status_code != 200:
                raise MetadataError("metadata request failed")
            return parse_record(response.json(), target)
        except (httpx.HTTPError, ValueError):
            raise MetadataError("metadata request failed") from None

    def poll_once(self) -> None:
        with self._lock:
            self._auth_retry_used = False
            self._clear_series()
            successes = 0
            for target, role in TARGET_ROLES.items():
                try:
                    record = self._read_target(target)
                except MetadataError:
                    self._available.labels(target).set(0)
                    self._collection_error.labels(target).set(1)
                    continue
                successes += 1
                self._available.labels(target).set(1)
                self._collection_error.labels(target).set(0)
                labels = (role, target)
                self._expires.labels(*labels).set(record.expires_at)
                self._ttl.labels(*labels).set(record.ttl_seconds)
                if record.last_successful_rotation_at is not None:
                    self._last_success.labels(*labels).set(record.last_successful_rotation_at)
                if record.last_failure_at is not None:
                    reason = record.last_failure_reason_code or "unknown"
                    self._last_failure.labels(*labels, reason).set(1)
                    self._last_failure_timestamp.labels(*labels, reason).set(record.last_failure_at)
                self._latest_run.labels(
                    *labels, record.latest_run_kind, record.latest_run_outcome
                ).set(record.latest_run_at)
            self._collector_success.set(1 if successes == len(TARGET_ROLES) else 0)
            self._last_poll_at = self._clock()
            self._poll_timestamp.set(self._last_poll_at)

    def start(self) -> None:
        if not self.settings.configured or self._thread is not None:
            return

        def run() -> None:
            while not self._stop.is_set():
                self.poll_once()
                self._stop.wait(self.settings.interval_seconds)

        self._stop.clear()
        self._thread = threading.Thread(target=run, name="dmf-promsd-openbao-observer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=65)
            self._thread = None
        self._token = ""
        if self._owns_client:
            self._client.close()

    def render(self) -> tuple[bytes, str]:
        with self._lock:
            if self._clock() - self._last_poll_at > self.settings.interval_seconds * 3:
                self._clear_series()
            return generate_latest(self._registry), CONTENT_TYPE_LATEST
