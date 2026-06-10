from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

MONITORING_SCRAPE_TAG = "monitoring:scrape"
MONITORING_PROBE_TAG = "monitoring:probe"
MONITORING_SNMP_TAG = "monitoring:snmp"


@dataclass(frozen=True)
class MonitoredRecord:
    kind: str
    data: Mapping[str, Any]


def _tag_name(tag: Any) -> str:
    if isinstance(tag, str):
        return tag
    if isinstance(tag, Mapping):
        name = tag.get("name")
        if isinstance(name, str):
            return name
    return ""


def _tags(data: Mapping[str, Any]) -> set[str]:
    raw_tags = data.get("tags") or ()
    return {name for name in (_tag_name(tag) for tag in raw_tags) if name}


def _first_tag_suffix(data: Mapping[str, Any], prefix: str) -> str | None:
    suffixes = []
    for tag in _tags(data):
        if tag.startswith(prefix + ":"):
            suffix = tag.split(":", 1)[1].strip()
            if suffix:
                suffixes.append(suffix)
    if not suffixes:
        return None
    suffixes.sort()
    return suffixes[0]


def _stringify(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    if isinstance(value, int):
        return str(value)
    return str(value).strip() or None


def _strip_mask(value: str | None) -> str | None:
    if not value:
        return None
    return value.split("/", 1)[0].strip() or None


def _custom_fields(data: Mapping[str, Any]) -> Mapping[str, Any]:
    custom_fields = data.get("custom_fields")
    if isinstance(custom_fields, Mapping):
        return custom_fields
    return {}


def _cluster_service_host(data: Mapping[str, Any]) -> str | None:
    custom_fields = _custom_fields(data)
    service = _stringify(custom_fields.get("cluster_service"))
    namespace = _stringify(custom_fields.get("cluster_namespace"))
    if service and namespace:
        return f"{service}.{namespace}.svc.cluster.local"
    return None


def _extract_address(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return _strip_mask(value)
    if isinstance(value, Mapping):
        for key in ("address", "host", "name", "value"):
            candidate = _extract_address(value.get(key))
            if candidate:
                return candidate
        for key in ("primary_ip4", "ip_address", "device", "virtual_machine"):
            candidate = _extract_address(value.get(key))
            if candidate:
                return candidate
    return None


def _extract_site(data: Mapping[str, Any]) -> str | None:
    for key in ("site",):
        candidate = data.get(key)
        if isinstance(candidate, Mapping):
            for site_key in ("slug", "name"):
                value = _stringify(candidate.get(site_key))
                if value:
                    return value
        value = _stringify(candidate)
        if value:
            return value
    for relation_key in ("device", "virtual_machine"):
        relation = data.get(relation_key)
        if isinstance(relation, Mapping):
            site = relation.get("site")
            if isinstance(site, Mapping):
                for site_key in ("slug", "name"):
                    value = _stringify(site.get(site_key))
                    if value:
                        return value
    return None


def _common_labels(data: Mapping[str, Any]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for prefix in ("app", "exposure"):
        value = _first_tag_suffix(data, prefix)
        if value:
            labels[prefix] = value
    site = _extract_site(data)
    if site:
        labels["site"] = site
    return labels


def _record_address(record: MonitoredRecord) -> str | None:
    data = record.data
    cluster_host = _cluster_service_host(data)
    if cluster_host:
        return cluster_host
    if record.kind == "service":
        return _extract_address(
            data.get("ip_address")
            or data.get("host")
            or data.get("primary_ip4")
            or data.get("device")
            or data.get("virtual_machine")
        )
    if record.kind in {"device", "virtual-machine"}:
        return _extract_address(
            data.get("primary_ip4") or data.get("ip_address") or data.get("host")
        )
    return None


def _probe_target(record: MonitoredRecord) -> str | None:
    data = record.data
    cluster_host = _cluster_service_host(data)
    if record.kind == "service":
        if cluster_host:
            cluster_port = _stringify(_custom_fields(data).get("cluster_port"))
            if cluster_port:
                return f"{cluster_host}:{cluster_port}"
        target = _record_address(record)
        if not target:
            return None
        port = data.get("port") or _custom_fields(data).get("metrics_port")
        port_text = _stringify(port)
        if port_text:
            return f"{target}:{port_text}"
        return target
    target = _record_address(record)
    if not target:
        return None
    return target


def _group(target: str, labels: dict[str, str]) -> dict[str, Any]:
    return {"targets": [target], "labels": labels}


def _build_lane(
    records: Iterable[MonitoredRecord],
    *,
    tag: str,
    module_field: str,
    default_module: str | None = None,
    target_resolver: Callable[[MonitoredRecord], str | None],
    labels_resolver: Callable[[Mapping[str, Any]], dict[str, str]] = _common_labels,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    groups: list[dict[str, Any]] = []
    skipped = Counter[str]()
    for record in records:
        data = record.data
        if tag not in _tags(data):
            continue
        try:
            custom_fields = _custom_fields(data)
            module = custom_fields.get(module_field)
            module_text = _stringify(module) or default_module
            if not module_text:
                skipped["missing_module"] += 1
                continue
            target = target_resolver(record)
            if not target:
                skipped["missing_target"] += 1
                continue
            labels = labels_resolver(data)
            labels["__param_module"] = module_text
            groups.append(_group(target, labels))
        except Exception:
            skipped["invalid_object"] += 1
    return groups, dict(skipped)


def _build_scrape_lane(
    records: Iterable[MonitoredRecord],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    groups: list[dict[str, Any]] = []
    skipped = Counter[str]()
    for record in records:
        data = record.data
        if MONITORING_SCRAPE_TAG not in _tags(data):
            continue
        try:
            custom_fields = _custom_fields(data)
            port = _stringify(custom_fields.get("metrics_port"))
            if not port:
                skipped["missing_metrics_port"] += 1
                continue
            target = _record_address(record)
            if not target:
                skipped["missing_target"] += 1
                continue
            labels = _common_labels(data)
            metrics_path = _stringify(custom_fields.get("metrics_path")) or "/metrics"
            labels["__metrics_path__"] = metrics_path
            groups.append(_group(f"{target}:{port}", labels))
        except Exception:
            skipped["invalid_object"] += 1
    return groups, dict(skipped)


def build_payloads(
    services: Iterable[Mapping[str, Any]],
    devices: Iterable[Mapping[str, Any]],
    virtual_machines: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, int]], dict[str, int]]:
    services_list = list(services)
    devices_list = list(devices)
    virtual_machines_list = list(virtual_machines)
    records = [
        *(MonitoredRecord("service", item) for item in services_list),
        *(MonitoredRecord("device", item) for item in devices_list),
        *(MonitoredRecord("virtual-machine", item) for item in virtual_machines_list),
    ]
    scrape, scrape_skipped = _build_scrape_lane(records)
    probe, probe_skipped = _build_lane(
        records,
        tag=MONITORING_PROBE_TAG,
        module_field="probe_module",
        target_resolver=_probe_target,
    )
    snmp_records = [record for record in records if record.kind == "device"]
    snmp, snmp_skipped = _build_lane(
        snmp_records,
        tag=MONITORING_SNMP_TAG,
        module_field="snmp_module",
        default_module="if_mib",
        target_resolver=_record_address,
    )
    payloads = {"scrape": scrape, "probe": probe, "snmp": snmp}
    skipped = {"scrape": scrape_skipped, "probe": probe_skipped, "snmp": snmp_skipped}
    source_counts = {
        "services": len(services_list),
        "devices": len(devices_list),
        "virtual_machines": len(virtual_machines_list),
    }
    return payloads, skipped, source_counts
