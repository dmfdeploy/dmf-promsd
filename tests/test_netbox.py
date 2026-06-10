from __future__ import annotations

import httpx

from dmf_promsd.netbox import NetboxClient


def test_netbox_client_paginates_and_uses_token_auth():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/ipam/services/":
            assert request.headers["authorization"] == "Token test-token"
            if request.url.params.get("offset") is None:
                return httpx.Response(
                    200,
                    json={
                        "count": 2,
                        "next": "https://dmf.example.com/api/ipam/services/?limit=1000&offset=1000",
                        "previous": None,
                        "results": [{"id": 1}],
                    },
                )
            return httpx.Response(
                200,
                json={"count": 2, "next": None, "previous": None, "results": [{"id": 2}]},
            )
        raise AssertionError(f"unexpected path {request.url.path}")

    client = NetboxClient(
        base_url="https://dmf.example.com",
        token="test-token",
        validate_certs=False,
        transport=httpx.MockTransport(handler),
    )

    try:
        results = client.list_services(tags=("monitoring:scrape",))
    finally:
        client.close()

    assert [item["id"] for item in results] == [1, 2]
    assert len(requests) == 2
    # NetBox ?tag= filters by slug (colon -> hyphen), not the tag name.
    assert requests[0].url.params.get("tag") == "monitoring-scrape"
