from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import httpx


class NetboxAPIError(RuntimeError):
    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"NetBox API {status_code}: {body}")


class NetboxClient:
    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        validate_certs: bool = True,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            verify=validate_certs,
            timeout=timeout,
            transport=transport,
            headers={
                "Authorization": f"Token {token}",
                "Accept": "application/json",
            },
        )

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, params: Iterable[tuple[str, str]] | None = None) -> dict[str, Any]:
        if params is None:
            response = self._client.get(path)
        else:
            response = self._client.get(path, params=list(params))
        if response.is_error:
            body = response.text
            raise NetboxAPIError(response.status_code, body)
        return response.json()

    def _list_paginated(
        self, path: str, params: Iterable[tuple[str, str]] | None = None
    ) -> list[dict[str, Any]]:
        url: str | None = path
        query = list(params or ())
        results: list[dict[str, Any]] = []
        while url:
            payload = self._get(url, query if url == path else None)
            results.extend(payload.get("results", []))
            url = payload.get("next")
            query = None
        return results

    def list_services(self, *, tags: Iterable[str]) -> list[dict[str, Any]]:
        return self._list_paginated(
            "/api/ipam/services/",
            # NetBox ?tag= filters by tag SLUG (colon -> hyphen), not the name.
            [("limit", "1000"), *[("tag", tag.replace(":", "-")) for tag in tags]],
        )

    def list_devices(self, *, tags: Iterable[str]) -> list[dict[str, Any]]:
        return self._list_paginated(
            "/api/dcim/devices/",
            # NetBox ?tag= filters by tag SLUG (colon -> hyphen), not the name.
            [("limit", "1000"), *[("tag", tag.replace(":", "-")) for tag in tags]],
        )

    def list_virtual_machines(self, *, tags: Iterable[str]) -> list[dict[str, Any]]:
        return self._list_paginated(
            "/api/virtualization/virtual-machines/",
            # NetBox ?tag= filters by tag SLUG (colon -> hyphen), not the name.
            [("limit", "1000"), *[("tag", tag.replace(":", "-")) for tag in tags]],
        )
