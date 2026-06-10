from __future__ import annotations

from fastapi.testclient import TestClient

from dmf_promsd.main import create_app
from dmf_promsd.settings import Settings
from tests.conftest import StaticCache


def test_health_and_readiness_endpoints_use_cache_state():
    cache = StaticCache(
        snapshot_payloads=[{"targets": ["dmf.example.com:9100"], "labels": {"app": "encoder"}}],
        readiness=(200, {"status": "ready", "ready": True}),
        health={"status": "ok", "cache_ttl_seconds": 45, "http_sd_refresh": "30s"},
    )
    app = create_app(
        settings=Settings(netbox_url="https://dmf.example.com", netbox_token="token"),
        cache=cache,
    )
    client = TestClient(app)

    health = client.get("/healthz")
    ready = client.get("/readyz")
    scrape = client.get("/sd/scrape")

    assert health.status_code == 200
    assert health.json()["product"] == "DMF PromSD"
    assert ready.status_code == 200
    assert scrape.json() == [{"targets": ["dmf.example.com:9100"], "labels": {"app": "encoder"}}]


def test_create_app_requires_netbox_settings_when_no_cache_is_injected():
    settings = Settings()

    try:
        create_app(settings=settings)
    except RuntimeError as exc:
        assert "NETBOX_URL and NETBOX_TOKEN" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
