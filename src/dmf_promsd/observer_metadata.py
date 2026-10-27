"""Strict validation and safe projection of OpenBao rotation metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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


class MetadataError(ValueError):
    """Raised when metadata does not match the supported schema."""


@dataclass(frozen=True)
class RotationRecord:
    role: str
    target: str
    expires_at: int
    ttl_seconds: int
    last_successful_rotation_at: int | None
    last_scheduled_success_at: int | None
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
    "last_scheduled_success_at",
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
    last_scheduled_success = _timestamp(
        value["last_scheduled_success_at"], "last_scheduled_success_at", nullable=True
    )
    failure_at, failure_reason = _failure_values(value)
    run_kind, run_outcome, latest_run_at = _latest_run(value, failure_at)

    return RotationRecord(
        role=value["approle_name"],
        target=expected_target,
        expires_at=expires_at,
        ttl_seconds=ttl_seconds,
        last_successful_rotation_at=last_success,
        last_scheduled_success_at=last_scheduled_success,
        last_failure_at=failure_at,
        last_failure_reason_code=failure_reason,
        latest_run_kind=run_kind,
        latest_run_outcome=run_outcome,
        latest_run_at=latest_run_at,
    )
