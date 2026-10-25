from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import httpx


def _tag_slug(name: str) -> str:
    # NetBox ?tag= filters by tag SLUG (colon -> hyphen), not the name.
    return name.replace(":", "-")


_LIST_PATHS = {
    "services": "/api/ipam/services/",
    "devices": "/api/dcim/devices/",
    "virtual_machines": "/api/virtualization/virtual-machines/",
}


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

    def list_tagged(self, kind: str, tags: Iterable[str]) -> list[dict[str, Any]]:
        path = _LIST_PATHS[kind]
        # NetBox ?tag= filters are ANDed; query each tag separately and union by
        # id so an object tagged with ANY of the tags is included (OR).
        merged: dict[Any, dict[str, Any]] = {}
        for tag in tags:
            for obj in self._list_paginated(path, [("limit", "1000"), ("tag", _tag_slug(tag))]):
                merged[obj.get("id", id(obj))] = obj
        return list(merged.values())
