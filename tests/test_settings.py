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

    assert _netbox_and_cache(load_settings()) == (
        "https://dmf.example.com",
        "test-token",
        expected,
        7,
        "11s",
    )
