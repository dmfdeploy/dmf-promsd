from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

SERVICES = "services"
DEVICES = "devices"
VIRTUAL_MACHINES = "virtual_machines"
RECORD_KINDS = (SERVICES, DEVICES, VIRTUAL_MACHINES)


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
    if record.kind == SERVICES:
        return _extract_address(
            data.get("ip_address")
            or data.get("host")
            or data.get("primary_ip4")
            or data.get("device")
            or data.get("virtual_machine")
        )
    if record.kind in {DEVICES, VIRTUAL_MACHINES}:
        return _extract_address(
            data.get("primary_ip4") or data.get("ip_address") or data.get("host")
        )
    return None


def _probe_target(record: MonitoredRecord) -> str | None:
    data = record.data
    cluster_host = _cluster_service_host(data)
    if record.kind == SERVICES:
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


def _normalize_probe_path(value: Any) -> str | None:
    text = _stringify(value)
    if not text:
        return None
    return "/" + text.lstrip("/")


def _group(target: str, labels: dict[str, str]) -> dict[str, Any]:
    return {"targets": [target], "labels": labels}


def _scrape_target(record: MonitoredRecord, port: str) -> str | None:
    target = _record_address(record)
    if not target:
        return None
    return f"{target}:{port}"


def _probe_target_with_path(record: MonitoredRecord, module: str) -> str | None:
    target = _probe_target(record)
    # ADR-0038 Amendment B: a URL path rides the target, so it only
    # applies to http-prober modules — never tcp/icmp/snmp.
    if target and module.startswith("http_"):
        path = _normalize_probe_path(_custom_fields(record.data).get("probe_path"))
        if path:
            target = f"{target}{path}"
    return target


def _snmp_target(record: MonitoredRecord, module: str) -> str | None:
    return _record_address(record)


def _metrics_path_label(custom_fields: Mapping[str, Any], port: str) -> dict[str, str]:
    return {"__metrics_path__": _stringify(custom_fields.get("metrics_path")) or "/metrics"}


def _module_label(custom_fields: Mapping[str, Any], module: str) -> dict[str, str]:
    return {"__param_module": module}


@dataclass(frozen=True)
class Lane:
    name: str
    tag: str
    kinds: tuple[str, ...]
    # The custom field this lane reads, and the skip reason when it is unset with no default.
    field: str
    missing: str
    target: Callable[[MonitoredRecord, str], str | None]
    labels: Callable[[Mapping[str, Any], str], dict[str, str]]
    default: str | None = None


LANES = (
    Lane(
        name="scrape",
        tag="monitoring:scrape",
        kinds=(SERVICES, DEVICES, VIRTUAL_MACHINES),
        field="metrics_port",
        missing="missing_metrics_port",
        target=_scrape_target,
        labels=_metrics_path_label,
    ),
    Lane(
        name="probe",
        tag="monitoring:probe",
        kinds=(SERVICES, DEVICES, VIRTUAL_MACHINES),
        field="probe_module",
        missing="missing_module",
        target=_probe_target_with_path,
        labels=_module_label,
    ),
    Lane(
        name="snmp",
        tag="monitoring:snmp",
        kinds=(DEVICES,),
        field="snmp_module",
        missing="missing_module",
        target=_snmp_target,
        labels=_module_label,
        default="if_mib",
    ),
)


def _build_lane(
    records: Iterable[MonitoredRecord], lane: Lane
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    groups: list[dict[str, Any]] = []
    skipped = Counter[str]()
    for record in records:
        data = record.data
        if record.kind not in lane.kinds or lane.tag not in _tags(data):
            continue
        try:
            custom_fields = _custom_fields(data)
            value = _stringify(custom_fields.get(lane.field)) or lane.default
            if not value:
                skipped[lane.missing] += 1
                continue
            target = lane.target(record, value)
            if not target:
                skipped["missing_target"] += 1
                continue
            labels = _common_labels(data)
            labels.update(lane.labels(custom_fields, value))
            groups.append(_group(target, labels))
        except Exception:
            skipped["invalid_object"] += 1
    return groups, dict(skipped)


def build_payloads(
    records: Mapping[str, Iterable[Mapping[str, Any]]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, int]], dict[str, int]]:
    by_kind = {kind: list(records[kind]) for kind in RECORD_KINDS}
    monitored = [MonitoredRecord(kind, item) for kind, items in by_kind.items() for item in items]
    payloads: dict[str, list[dict[str, Any]]] = {}
    skipped: dict[str, dict[str, int]] = {}
    for lane in LANES:
        payloads[lane.name], skipped[lane.name] = _build_lane(monitored, lane)
    source_counts = {kind: len(items) for kind, items in by_kind.items()}
    return payloads, skipped, source_counts
