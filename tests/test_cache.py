from __future__ import annotations

from dmf_promsd.cache import PromSDCache
from tests.conftest import FakeSequenceClient


def _service(target: str, port: int, module: str = "http_2xx") -> dict:
    return {
        "id": 1,
        "name": "service-one",
        "tags": [
            {"name": "monitoring:scrape"},
            {"name": "monitoring:probe"},
            {"name": "app:encoder"},
            {"name": "exposure:internal"},
        ],
        "site": {"slug": "site-a"},
        "custom_fields": {"metrics_port": port, "metrics_path": "/metrics", "probe_module": module},
        "ip_address": {"address": target},
    }


def test_cache_refresh_updates_snapshot_and_readiness():
    clock_value = [0.0]

    def clock() -> float:
        return clock_value[0]

    client = FakeSequenceClient(
        # 2 entries per refresh: the cache queries each monitoring tag separately
        # (scrape, probe) and unions by id; same id within a refresh => 1 service.
        services=[
            [_service("dmf.example.com", 9100)],
            [_service("dmf.example.com", 9100)],
            [_service("dmf.example.com", 9200)],
            [_service("dmf.example.com", 9200)],
        ],
        devices=[[]],
        virtual_machines=[[]],
        calls=[],
    )
    cache = PromSDCache(client=client, ttl_seconds=45, freshness_window_seconds=2, clock=clock)

    assert cache.refresh() is True
    assert cache.lane_payload("scrape")[0]["targets"] == ["dmf.example.com:9100"]
    assert cache.is_ready() is True

    clock_value[0] = 1.0
    assert cache.refresh() is True
    assert cache.lane_payload("scrape")[0]["targets"] == ["dmf.example.com:9200"]

    clock_value[0] = 4.1
    status_code, payload = cache.ready_payload()
    assert status_code == 503
    assert payload["ready"] is False


def test_cache_health_reports_last_error_on_refresh_failure():
    class BrokenClient(FakeSequenceClient):
        def list_services(self, *, tags: tuple[str, ...]) -> list[dict]:
            raise RuntimeError("netbox unavailable")

    cache = PromSDCache(
        client=BrokenClient(services=[[]], devices=[[]], virtual_machines=[[]], calls=[]),
        ttl_seconds=45,
        freshness_window_seconds=2,
    )

    assert cache.refresh() is False
    payload = cache.health_payload()
    assert payload["last_error"] == "netbox unavailable"
