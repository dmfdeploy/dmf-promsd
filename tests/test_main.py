from __future__ import annotations

import threading
import time
from collections.abc import Callable

from fastapi.testclient import TestClient

from dmf_promsd import __version__
from dmf_promsd.main import create_app
from dmf_promsd.settings import Settings
from tests.conftest import StaticCache
from tests.netbox45 import BASE_URL, TOKEN, FakeNetbox, device


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


def _cache_threads() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name == "dmf-promsd-cache"]


def test_lifespan_refreshes_on_startup_and_stops_on_shutdown():
    netbox = FakeNetbox(devices=[device("node-1", ["monitoring:snmp"], address="192.0.2.11/24")])
    client = netbox.client()
    # Values unlike the defaults, so the test sees them reach the cache.
    settings = Settings(
        netbox_url=BASE_URL, netbox_token=TOKEN, cache_ttl_seconds=7, http_sd_refresh="11s"
    )
    app = create_app(settings, client=client)
    try:
        with TestClient(app) as http:
            assert netbox.requests
            assert _cache_threads()
            health = http.get("/healthz")
            ready = http.get("/readyz")
    finally:
        client.close()

    assert not _cache_threads()
    assert health.status_code == 200
    health_body = health.json()
    assert 0 <= health_body.pop("snapshot_age_seconds") < 5
    assert health_body == {
        "status": "ok",
        "product": "DMF PromSD",
        "version": __version__,
        "cache_ttl_seconds": 7,
        "http_sd_refresh": "11s",
        "freshness_window_seconds": 14,
        "source_counts": {"services": 0, "devices": 1, "virtual_machines": 0},
        "last_error": None,
    }
    assert ready.status_code == 200
    ready_body = ready.json()
    assert 0 <= ready_body.pop("snapshot_age_seconds") < 5
    assert ready_body == {
        "status": "ready",
        "ready": True,
        "freshness_window_seconds": 14,
        "last_error": None,
        "product": "DMF PromSD",
        "version": __version__,
    }


def _wait_until(condition: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out waiting for the cache"
        time.sleep(0.05)


def test_rejected_token_serves_empty_lanes_and_not_ready():
    netbox = FakeNetbox(devices=[device("node-1", ["monitoring:snmp"], address="192.0.2.11/24")])
    client = netbox.client(token="wrong-token")
    app = create_app(Settings(netbox_url=BASE_URL, netbox_token="wrong-token"), client=client)
    try:
        with TestClient(app) as http:
            lanes = [http.get(f"/sd/{lane}") for lane in ("scrape", "probe", "snmp")]
            health = http.get("/healthz")
            ready = http.get("/readyz")
    finally:
        client.close()

    assert [(lane.status_code, lane.json()) for lane in lanes] == [(200, [])] * 3
    health_body = health.json()
    assert health_body["snapshot_age_seconds"] is None
    assert health_body["source_counts"] == {}
    assert health_body["last_error"].startswith("NetBox API 403: ")
    assert "Invalid v1 token" in health_body["last_error"]
    assert ready.status_code == 503
    assert ready.json() == {
        "status": "stale",
        "ready": False,
        "reason": "no snapshot",
        "product": "DMF PromSD",
        "version": __version__,
    }


def test_netbox_outage_keeps_the_last_good_lanes_until_it_recovers():
    netbox = FakeNetbox(devices=[device("node-1", ["monitoring:snmp"], address="192.0.2.11/24")])
    client = netbox.client()
    # The shortest TTL: the cache refreshes every second and is stale after two.
    settings = Settings(netbox_url=BASE_URL, netbox_token=TOKEN, cache_ttl_seconds=1)
    app = create_app(settings, client=client)
    try:
        with TestClient(app) as http:
            before = http.get("/sd/snmp").json()
            netbox.down = True
            _wait_until(lambda: http.get("/readyz").json()["last_error"] is not None)
            _wait_until(lambda: http.get("/readyz").status_code == 503)
            stale = http.get("/readyz").json()
            during = http.get("/sd/snmp").json()
            netbox.down = False
            _wait_until(lambda: http.get("/readyz").status_code == 200)
            recovered = http.get("/healthz").json()
    finally:
        client.close()

    assert before
    assert during == before
    assert stale.pop("snapshot_age_seconds") > 2
    assert stale == {
        "status": "stale",
        "ready": False,
        "freshness_window_seconds": 2,
        "last_error": "NetBox API 503: Service Unavailable",
        "product": "DMF PromSD",
        "version": __version__,
    }
    assert recovered["last_error"] is None
