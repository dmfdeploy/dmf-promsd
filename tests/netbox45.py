"""NetBox v4.5.0 records and REST API, as promsd reads them.

Records carry a subset of each v4.5.0 serializer's fields with their real types:
services have `parent`, `ipaddresses` and `ports` and no `site`; addresses are
CIDR strings; nested objects use their brief form. Every custom field that
netbox-sot assigns to the object type is present, holding its default or null.
"""

from __future__ import annotations

import itertools
import re
from collections import defaultdict
from collections.abc import Iterable, Iterator
from typing import Any

import httpx

from dmf_promsd.netbox import NetboxClient

BASE_URL = "https://dmf.example.com"
TOKEN = "test-token"

# NetBox numbers each table on its own, so a device and a VM can share an id.
_ids: defaultdict[str, Iterator[int]] = defaultdict(lambda: itertools.count(1))

_CUSTOM_FIELD_DEFAULTS = {
    "metrics_port": None,
    "metrics_path": "/metrics",
    "probe_module": "http_2xx",
    "probe_path": None,
    "cluster_service": None,
    "cluster_namespace": None,
    "cluster_port": None,
}
_DEVICE_ONLY_DEFAULTS = {"snmp_module": "if_mib"}

_LIST_PATHS = {
    "services": "/api/ipam/services/",
    "devices": "/api/dcim/devices/",
    "virtual_machines": "/api/virtualization/virtual-machines/",
}

# Enough for any one test; a pagination bug would otherwise loop forever.
_MAX_REQUESTS = 500

# netbox-sot creates these on every NetBox, so they exist before any object carries them.
SEEDED_TAGS = (
    "monitoring:scrape",
    "monitoring:probe",
    "monitoring:snmp",
    "exposure:private",
    "exposure:public",
)


def _slug(name: str) -> str:
    # The slug netbox-sot posts; NetBox's own slugify would delete the colon.
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def tag(name: str) -> dict[str, Any]:
    slug = _slug(name)
    tag_id = next(_ids["tags"])
    return {
        "id": tag_id,
        "url": f"{BASE_URL}/api/extras/tags/{tag_id}/",
        "display_url": f"{BASE_URL}/extras/tags/{tag_id}/",
        "display": name,
        "name": name,
        "slug": slug,
        "color": "9e9e9e",
    }


def ip_address(address: str) -> dict[str, Any]:
    family = 6 if ":" in address else 4
    ip_id = next(_ids["ip-addresses"])
    return {
        "id": ip_id,
        "url": f"{BASE_URL}/api/ipam/ip-addresses/{ip_id}/",
        "display": address,
        "family": {"value": family, "label": f"IPv{family}"},
        "address": address,
        "description": "",
    }


def site(slug: str) -> dict[str, Any]:
    site_id = next(_ids["sites"])
    return {
        "id": site_id,
        "url": f"{BASE_URL}/api/dcim/sites/{site_id}/",
        "display": slug.upper(),
        "name": slug.upper(),
        "slug": slug,
        "description": "",
    }


def brief(record: dict[str, Any]) -> dict[str, Any]:
    return {key: record[key] for key in ("id", "url", "display", "name", "description")}


def _custom_fields(defaults: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    unknown = values.keys() - defaults.keys()
    if unknown:
        raise TypeError(f"not a custom field on this object type: {sorted(unknown)}")
    return {**defaults, **values}


def _primary_ips(address: str | None) -> dict[str, Any]:
    ip = ip_address(address) if address else None
    ip4 = ip if ip and ip["family"]["value"] == 4 else None
    ip6 = ip if ip and ip["family"]["value"] == 6 else None
    return {"primary_ip": ip, "primary_ip4": ip4, "primary_ip6": ip6}


def device(
    name: str,
    tags: Iterable[str],
    *,
    site_slug: str = "dc-a",
    address: str | None = None,
    **custom_fields: Any,
) -> dict[str, Any]:
    device_id = next(_ids["devices"])
    return {
        "id": device_id,
        "url": f"{BASE_URL}/api/dcim/devices/{device_id}/",
        "display_url": f"{BASE_URL}/dcim/devices/{device_id}/",
        "display": name,
        "name": name,
        "status": {"value": "active", "label": "Active"},
        "site": site(site_slug),
        "parent_device": None,
        **_primary_ips(address),
        "description": "",
        "tags": [tag(tag_name) for tag_name in tags],
        "custom_fields": _custom_fields(
            {**_CUSTOM_FIELD_DEFAULTS, **_DEVICE_ONLY_DEFAULTS}, custom_fields
        ),
    }


def virtual_machine(
    name: str,
    tags: Iterable[str],
    *,
    site_slug: str | None = None,
    address: str | None = None,
    host: dict[str, Any] | None = None,
    **custom_fields: Any,
) -> dict[str, Any]:
    vm_id = next(_ids["virtual-machines"])
    return {
        "id": vm_id,
        "url": f"{BASE_URL}/api/virtualization/virtual-machines/{vm_id}/",
        "display_url": f"{BASE_URL}/virtualization/virtual-machines/{vm_id}/",
        "display": name,
        "name": name,
        "status": {"value": "active", "label": "Active"},
        "site": site(site_slug) if site_slug else None,
        "device": brief(host) if host else None,
        **_primary_ips(address),
        "description": "",
        "tags": [tag(tag_name) for tag_name in tags],
        "custom_fields": _custom_fields(_CUSTOM_FIELD_DEFAULTS, custom_fields),
    }


def service(
    name: str,
    tags: Iterable[str],
    *,
    parent: dict[str, Any],
    addresses: Iterable[str] = (),
    ports: Iterable[int] = (443,),
    **custom_fields: Any,
) -> dict[str, Any]:
    service_id = next(_ids["services"])
    port_list = list(ports)
    parent_type = (
        "dcim.device" if "/api/dcim/devices/" in parent["url"] else "virtualization.virtualmachine"
    )
    return {
        "id": service_id,
        "url": f"{BASE_URL}/api/ipam/services/{service_id}/",
        "display_url": f"{BASE_URL}/ipam/services/{service_id}/",
        "display": f"{name} (TCP/{', '.join(map(str, port_list))})",
        "parent_object_type": parent_type,
        "parent_object_id": parent["id"],
        "parent": brief(parent),
        "name": name,
        "protocol": {"value": "tcp", "label": "TCP"},
        "ports": port_list,
        "ipaddresses": [ip_address(address) for address in addresses],
        "description": "",
        "owner": None,
        "comments": "",
        "tags": [tag(tag_name) for tag_name in tags],
        "custom_fields": _custom_fields(_CUSTOM_FIELD_DEFAULTS, custom_fields),
    }


class FakeNetbox:
    """The three list endpoints promsd calls, answering as NetBox v4.5.0 does.

    While `down` is set, every request gets the 503 of a proxy whose NetBox is
    gone. A request without the token is a 403. `?tag=` matches tag slugs, repeated
    `tag` parameters must all match, and a slug that is neither seeded nor on a
    record is a 400. Pages follow `limit` and `offset` under NetBox's page-size
    rules, with an absolute `next` URL.
    """

    def __init__(
        self,
        *,
        services: Iterable[dict[str, Any]] = (),
        devices: Iterable[dict[str, Any]] = (),
        virtual_machines: Iterable[dict[str, Any]] = (),
        max_page_size: int = 1000,
    ) -> None:
        self._records = {
            _LIST_PATHS["services"]: list(services),
            _LIST_PATHS["devices"]: list(devices),
            _LIST_PATHS["virtual_machines"]: list(virtual_machines),
        }
        self._slugs = {_slug(name) for name in SEEDED_TAGS} | {
            tag["slug"]
            for records in self._records.values()
            for record in records
            for tag in record["tags"]
        }
        self._max_page_size = max_page_size
        self.requests: list[httpx.Request] = []
        self.down = False

    def client(self, token: str = TOKEN) -> NetboxClient:
        return NetboxClient(base_url=BASE_URL, token=token, transport=httpx.MockTransport(self))

    def _page_size(self, limit: str) -> int:
        # NetBox's get_limit with MAX_PAGE_SIZE set: 0 asks for the largest page, and a
        # value that isn't a whole number falls back to PAGINATE_COUNT (50).
        default = min(50, self._max_page_size)
        try:
            size = int(limit)
        except ValueError:
            return default
        if size < 0:
            return default
        if size == 0:
            return self._max_page_size
        return min(size, self._max_page_size)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if len(self.requests) > _MAX_REQUESTS:
            raise RuntimeError(f"promsd sent NetBox more than {_MAX_REQUESTS} requests")
        if self.down:
            return httpx.Response(503, text="Service Unavailable")
        if request.headers.get("Authorization") not in {f"Token {TOKEN}", f"Bearer {TOKEN}"}:
            return httpx.Response(403, json={"detail": "Invalid v1 token"})
        records = self._records.get(request.url.path)
        if records is None:
            return httpx.Response(404, json={"detail": "Not found."})
        params = request.url.params
        slugs = params.get_list("tag")
        unknown = [slug for slug in slugs if slug not in self._slugs]
        if unknown:
            choice = f"Select a valid choice. {unknown[0]} is not one of the available choices."
            return httpx.Response(400, json={"tag": [choice]})
        matches = [
            record for record in records if set(slugs) <= {tag["slug"] for tag in record["tags"]}
        ]
        limit = self._page_size(params.get("limit", ""))
        offset = int(params.get("offset", 0))
        end = offset + limit
        next_url = None
        if end < len(matches):
            next_url = str(
                request.url.copy_set_param("limit", str(limit)).copy_set_param("offset", str(end))
            )
        return httpx.Response(
            200,
            json={
                "count": len(matches),
                "next": next_url,
                "previous": None,
                "results": matches[offset:end],
            },
        )
