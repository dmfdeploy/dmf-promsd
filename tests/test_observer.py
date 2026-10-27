from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest

from dmf_promsd.observer import (
    TARGET_ROLES,
    MetadataError,
    OpenBaoObserver,
    parse_record,
)
from dmf_promsd.settings import ObserverSettings

TARGET = "external-secrets"
SENTINELS = ("ACCESSOR_SENTINEL", "JWT_SENTINEL", "ACTOR_SENTINEL", "REQUEST_SENTINEL")


def record(target: str = TARGET) -> dict[str, object]:
    return {
        "schema_version": 1,
        "target": target,
        "approle_name": TARGET_ROLES[target],
        "target_version": 2,
        "secret_id_accessor": SENTINELS[0],
        "issued_at": 1_700_000_000,
        "expires_at": 1_702_592_000,
        "ttl_seconds": 2_592_000,
        "last_successful_rotation_at": 1_700_000_000,
        "last_failure_at": 1_699_999_000,
        "last_failure_reason_code": "delivery_failed",
        "latest_run_kind": "scheduled",
        "latest_run_outcome": "success",
        "latest_run_at": 1_700_000_000,
        "actor": SENTINELS[2],
        "role": "scheduled-machine",
        "request_id": SENTINELS[3],
        "reason": "sensitive free text reason",
    }


def kv_response(target: str = TARGET) -> dict[str, object]:
    return {"data": {"data": record(target)}}


def make_observer(tmp_path: Path, handler, *, clock=lambda: 1_700_000_100):
    jwt_path = tmp_path / "jwt"
    jwt_path.write_text("JWT_SENTINEL", encoding="utf-8")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    observer = OpenBaoObserver(
        ObserverSettings(
            address="https://openbao.example.invalid",
            auth_role="dmf-promsd-observer",
            jwt_file=str(jwt_path),
        ),
        client=client,
        clock=clock,
    )
    return observer, client


def test_parse_record_projects_only_safe_metric_fields():
    parsed = parse_record(kv_response(), TARGET)

    assert parsed.role == TARGET
    assert parsed.target == TARGET
    assert parsed.expires_at == 1_702_592_000
    assert parsed.ttl_seconds == 2_592_000
    assert parsed.latest_run_outcome == "success"
    sensitive_fields = ("secret_id_accessor", "actor", "request_id")
    assert not any(hasattr(parsed, field) for field in sensitive_fields)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(schema_version=2),
        lambda value: value.update(target="unexpected-target"),
        lambda value: value.update(approle_name="unexpected-role"),
        lambda value: value.update(expires_at="secret-value"),
        lambda value: value.update(ttl_seconds=True),
        lambda value: value.update(unexpected_secret="SECRET_SENTINEL"),
        lambda value: value.update(last_failure_reason_code=[]),
        lambda value: value.update(last_failure_at=None),
        lambda value: value.update(latest_run_outcome="failure"),
    ],
)
def test_parse_record_rejects_unknown_or_ambiguous_fields(mutate):
    value = record()
    mutate(value)
    with pytest.raises(MetadataError):
        parse_record({"data": {"data": value}}, TARGET)


def test_unknown_reason_code_is_bounded_to_unknown():
    value = record()
    value["last_failure_reason_code"] = "new-unrecognized-code"

    parsed = parse_record({"data": {"data": value}}, TARGET)

    assert parsed.last_failure_reason_code == "unknown"


def test_poll_reads_only_fixed_paths_and_redacts_credentials_and_attribution(tmp_path):
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["jwt"] == "JWT_SENTINEL"
            return httpx.Response(200, json={"auth": {"client_token": "TOKEN_SENTINEL"}})
        seen.append((request.url.path, request.headers.get("X-Vault-Token", "")))
        target = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=kv_response(target))

    observer, client = make_observer(tmp_path, handler)
    try:
        observer.poll_once()
        body, content_type = observer.render()
    finally:
        observer.stop()
        client.close()

    exposition = body.decode()
    assert "text/plain" in content_type
    assert len(seen) == len(TARGET_ROLES)
    assert {path.rsplit("/", 1)[-1] for path, _ in seen} == set(TARGET_ROLES)
    assert all(path.startswith("/v1/secret/data/platform/approle-rotation/") for path, _ in seen)
    assert all(token == "TOKEN_SENTINEL" for _, token in seen)
    assert 'dmf_openbao_approle_secret_id_expires_at_seconds{role="external-secrets"' in exposition
    assert "dmf_openbao_approle_rotation_last_failure{" in exposition
    assert 'role="external-secrets"' in exposition
    assert "dmf_openbao_approle_rotation_collector_success 1.0" in exposition
    assert all(sentinel not in exposition for sentinel in SENTINELS)
    assert "sensitive free text reason" not in exposition


def test_failed_metadata_poll_drops_old_success_series_and_hides_response_body(tmp_path):
    fail = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal fail
        if request.method == "POST":
            return httpx.Response(200, json={"auth": {"client_token": "TOKEN_SENTINEL"}})
        target = request.url.path.rsplit("/", 1)[-1]
        if fail and target == TARGET:
            return httpx.Response(403, text="ACCESSOR_SENTINEL SECRET_BODY_SENTINEL")
        return httpx.Response(200, json=kv_response(target))

    observer, client = make_observer(tmp_path, handler)
    try:
        observer.poll_once()
        fail = True
        observer.poll_once()
        exposition = observer.render()[0].decode()
    finally:
        observer.stop()
        client.close()

    assert f'dmf_openbao_approle_rotation_metadata_available{{target="{TARGET}"}} 0.0' in exposition
    assert f'dmf_openbao_approle_rotation_collection_error{{target="{TARGET}"}} 1.0' in exposition
    expiry_samples = [
        line
        for line in exposition.splitlines()
        if line.startswith("dmf_openbao_approle_secret_id_expires_at_seconds{")
    ]
    assert not any(f'target="{TARGET}"' in sample for sample in expiry_samples)
    assert "ACCESSOR_SENTINEL" not in exposition
    assert "SECRET_BODY_SENTINEL" not in exposition
    assert "TOKEN_SENTINEL" not in exposition


def test_old_cache_becomes_unavailable_after_three_poll_intervals(tmp_path):
    now = [1_700_000_100]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"auth": {"client_token": "TOKEN_SENTINEL"}})
        target = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=kv_response(target))

    observer, client = make_observer(tmp_path, handler, clock=lambda: now[0])
    try:
        observer.poll_once()
        now[0] += observer.settings.interval_seconds * 3 + 1
        exposition = observer.render()[0].decode()
    finally:
        observer.stop()
        client.close()

    assert "dmf_openbao_approle_rotation_collector_success 0.0" in exposition
    unavailable = 'dmf_openbao_approle_rotation_metadata_available{target="external-secrets"} 0.0'
    assert unavailable in exposition
    expiry_samples = [
        line
        for line in exposition.splitlines()
        if line.startswith("dmf_openbao_approle_secret_id_expires_at_seconds{")
    ]
    assert not expiry_samples


def test_background_poller_stops_cleanly_on_shutdown(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"auth": {"client_token": "TOKEN_SENTINEL"}})
        target = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=kv_response(target))

    observer, client = make_observer(tmp_path, handler)
    observer.start()
    deadline = time.monotonic() + 2
    while observer._last_poll_at == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    thread = observer._thread
    observer.stop()
    client.close()

    assert thread is not None
    assert not thread.is_alive()
    assert observer._token == ""
