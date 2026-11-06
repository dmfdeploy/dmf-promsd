from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import httpx

from .records import DEVICES, SERVICES, VIRTUAL_MACHINES, Record

_TAGS_PATH = "/api/extras/tags/"

_LIST_PATHS = {
    SERVICES: "/api/ipam/services/",
    DEVICES: "/api/dcim/devices/",
    VIRTUAL_MACHINES: "/api/virtualization/virtual-machines/",
}


def _text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).strip() or None


def _string(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError(f"expected text, got {type(value).__name__}")
    return value


def parse_record(kind: str, raw: Mapping[str, Any]) -> Record:
    primary_ip4 = raw.get("primary_ip4")
    site = raw.get("site")
    return Record(
        kind=kind,
        tags=frozenset(_string(tag["name"]) for tag in raw["tags"]),
        address=_string(primary_ip4["address"]).split("/", 1)[0] if primary_ip4 else None,
        site=_string(site["slug"]) if site else None,
        fields={name: _text(value) for name, value in raw["custom_fields"].items()},
    )


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

    def tag_slugs(self, names: Iterable[str]) -> dict[str, str]:
        """Map each tag name that exists in NetBox to the slug NetBox gave it.

        NetBox derives the slug when a client sends none, and the rule differs between
        clients, so the slug is read back instead of computed. Names NetBox doesn't know
        are left out.
        """
        wanted = list(names)
        params = [("limit", "1000"), *(("name", name) for name in wanted)]
        found = {tag["name"]: tag["slug"] for tag in self._list_paginated(_TAGS_PATH, params)}
        return {name: found[name] for name in wanted if name in found}

    def list_tagged(self, kind: str, slugs: Iterable[str]) -> list[dict[str, Any]]:
        path = _LIST_PATHS[kind]
        # NetBox ?tag= filters are ANDed; query each tag separately and union by
        # id so an object tagged with ANY of the tags is included (OR).
        merged: dict[Any, dict[str, Any]] = {}
        for slug in slugs:
            for obj in self._list_paginated(path, [("limit", "1000"), ("tag", slug)]):
                merged[obj.get("id", id(obj))] = obj
        return list(merged.values())

    def list_records(self, kind: str, slugs: Iterable[str]) -> tuple[list[Record], int]:
        records: list[Record] = []
        unreadable = 0
        for raw in self.list_tagged(kind, slugs):
            try:
                records.append(parse_record(kind, raw))
            except (KeyError, TypeError, AttributeError):
                unreadable += 1
        return records, unreadable
