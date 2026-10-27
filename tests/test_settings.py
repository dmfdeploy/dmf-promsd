from __future__ import annotations

import pytest

from dmf_promsd.settings import Settings, load_settings


def _netbox_and_cache(settings: Settings) -> tuple[str, str, bool, int, str]:
    return (
        settings.netbox_url,
        settings.netbox_token,
        settings.netbox_validate_certs,
        settings.cache_ttl_seconds,
        settings.http_sd_refresh,
    )


# dmf-infra's deploy role renders the flag as "true" or "false".
@pytest.mark.parametrize(("validate_certs", "expected"), [("true", True), ("false", False)])
def test_load_settings_reads_the_environment(monkeypatch, validate_certs, expected):
    monkeypatch.setenv("NETBOX_URL", "https://dmf.example.com")
    monkeypatch.setenv("NETBOX_TOKEN", "test-token")
    monkeypatch.setenv("NETBOX_VALIDATE_CERTS", validate_certs)
    monkeypatch.setenv("CACHE_TTL_SECONDS", "7")
    monkeypatch.setenv("HTTP_SD_REFRESH", "11s")
    monkeypatch.setenv("OPENBAO_ADDR", "https://openbao.example.com")
    monkeypatch.setenv("OPENBAO_K8S_AUTH_MOUNT", "cluster-kubernetes")
    monkeypatch.setenv("OPENBAO_K8S_ROLE", "dmf-promsd-observer")
    monkeypatch.setenv("OPENBAO_SA_JWT_FILE", "/var/run/token")
    monkeypatch.setenv("OPENBAO_CA_FILE", "/var/run/openbao-ca/ca.crt")
    monkeypatch.setenv("OPENBAO_OBSERVER_INTERVAL_SECONDS", "75")

    assert _netbox_and_cache(load_settings()) == (
        "https://dmf.example.com",
        "test-token",
        expected,
        7,
        "11s",
    )
    settings = load_settings()
    assert settings.observer.address == "https://openbao.example.com"
    assert settings.observer.auth_mount == "cluster-kubernetes"
    assert settings.observer.auth_role == "dmf-promsd-observer"
    assert settings.observer.jwt_file == "/var/run/token"
    assert settings.observer.ca_file == "/var/run/openbao-ca/ca.crt"
    assert settings.observer.interval_seconds == 75


def test_observer_requires_https_ca_bundle_and_a_bounded_poll_interval(tmp_path):
    ca_path = tmp_path / "openbao-ca.crt"
    ca_path.write_text("test CA bundle", encoding="utf-8")
    assert not Settings().observer.configured
    secure = {"openbao_address": "https://openbao.example.com", "openbao_ca_file": str(ca_path)}
    assert Settings(**secure).observer.configured
    assert not Settings(
        openbao_address="http://openbao.example.com", openbao_ca_file=str(ca_path)
    ).observer.configured
    assert not Settings(
        openbao_address="https://openbao.example.com", openbao_ca_file=""
    ).observer.configured
    assert not Settings(**secure, openbao_observer_interval_seconds=9).observer.configured
    assert not Settings(**secure, openbao_observer_interval_seconds=3601).observer.configured
