from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .records import DEVICES, SERVICES, VIRTUAL_MACHINES, Record


def _first_tag_suffix(tags: Iterable[str], prefix: str) -> str | None:
    suffixes = []
    for tag in tags:
        if tag.startswith(prefix + ":"):
            suffix = tag.split(":", 1)[1].strip()
            if suffix:
                suffixes.append(suffix)
    if not suffixes:
        return None
    suffixes.sort()
    return suffixes[0]


def _cluster_service_host(record: Record) -> str | None:
    service = record.fields.get("cluster_service")
    namespace = record.fields.get("cluster_namespace")
    if service and namespace:
        return f"{service}.{namespace}.svc.cluster.local"
    return None


def _common_labels(record: Record) -> dict[str, str]:
    labels: dict[str, str] = {}
    for prefix in ("app", "exposure"):
        value = _first_tag_suffix(record.tags, prefix)
        if value:
            labels[prefix] = value
    if record.site:
        labels["site"] = record.site
    return labels


def _record_address(record: Record) -> str | None:
    cluster_host = _cluster_service_host(record)
    if cluster_host or record.kind == SERVICES:
        return cluster_host
    return record.address


def _probe_target(record: Record) -> str | None:
    cluster_host = _cluster_service_host(record)
    if record.kind == SERVICES:
        if cluster_host:
            cluster_port = record.fields.get("cluster_port")
            if cluster_port:
                return f"{cluster_host}:{cluster_port}"
        target = cluster_host
        if not target:
            return None
        port_text = record.fields.get("metrics_port")
        if port_text:
            return f"{target}:{port_text}"
        return target
    target = _record_address(record)
    if not target:
        return None
    return target


def _normalize_probe_path(text: str | None) -> str | None:
    if not text:
        return None
    return "/" + text.lstrip("/")


def _group(target: str, labels: dict[str, str]) -> dict[str, Any]:
    return {"targets": [target], "labels": labels}


def _scrape_target(record: Record, port: str) -> str | None:
    target = _record_address(record)
    if not target:
        return None
    return f"{target}:{port}"


def _probe_target_with_path(record: Record, module: str) -> str | None:
    target = _probe_target(record)
    # ADR-0038 Amendment B: a URL path rides the target, so it only
    # applies to http-prober modules — never tcp/icmp/snmp.
    if target and module.startswith("http_"):
        path = _normalize_probe_path(record.fields.get("probe_path"))
        if path:
            target = f"{target}{path}"
    return target


def _snmp_target(record: Record, module: str) -> str | None:
    return _record_address(record)


def _metrics_path_label(fields: Mapping[str, str | None], port: str) -> dict[str, str]:
    return {"__metrics_path__": fields.get("metrics_path") or "/metrics"}


def _module_label(fields: Mapping[str, str | None], module: str) -> dict[str, str]:
    return {"__param_module": module}


@dataclass(frozen=True)
class Lane:
    name: str
    tag: str
    kinds: tuple[str, ...]
    # The custom field this lane reads, and the skip reason when it is unset with no default.
    field: str
    missing: str
    target: Callable[[Record, str], str | None]
    labels: Callable[[Mapping[str, str | None], str], dict[str, str]]
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
    records: Iterable[Record], lane: Lane
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    groups: list[dict[str, Any]] = []
    skipped = Counter[str]()
    for record in records:
        if record.kind not in lane.kinds or lane.tag not in record.tags:
            continue
        value = record.fields.get(lane.field) or lane.default
        if not value:
            skipped[lane.missing] += 1
            continue
        target = lane.target(record, value)
        if not target:
            skipped["missing_target"] += 1
            continue
        labels = _common_labels(record)
        labels.update(lane.labels(record.fields, value))
        groups.append(_group(target, labels))
    return groups, dict(skipped)


def build_payloads(
    records: Iterable[Record],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, int]]]:
    records = list(records)
    payloads: dict[str, list[dict[str, Any]]] = {}
    skipped: dict[str, dict[str, int]] = {}
    for lane in LANES:
        payloads[lane.name], skipped[lane.name] = _build_lane(records, lane)
    return payloads, skipped
