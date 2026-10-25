from __future__ import annotations

from dmf_promsd.sd import build_payloads
from tests.netbox45 import device, service, virtual_machine


def _served(lane, *, services=(), devices=(), virtual_machines=()):
    payloads, _, _ = build_payloads(
        {"services": services, "devices": devices, "virtual_machines": virtual_machines}
    )
    # Prometheus ignores the order of target groups, so each target maps to its labels.
    return {target: group["labels"] for group in payloads[lane] for target in group["targets"]}


# Scrape: service DNS name or device or VM IP, with metrics port and path (/metrics if unset).
def test_scrape_lane_targets_the_metrics_port_and_path():
    node = virtual_machine(
        "k3s-node-1",
        ["monitoring:scrape"],
        site_slug="dc-a",
        address="198.51.100.11/24",
        metrics_port=9100,
        metrics_path=None,
    )
    scrape = _served(
        "scrape",
        services=[
            service(
                "prometheus",
                ["monitoring:scrape"],
                parent=node,
                cluster_service="prometheus-server",
                cluster_namespace="monitoring",
                cluster_port=80,
                metrics_port=9090,
            )
        ],
        devices=[
            device(
                "storage-1",
                ["monitoring:scrape", "app:storage", "exposure:private"],
                site_slug="dc-b",
                address="192.0.2.41/24",
                metrics_port=9100,
                metrics_path="/node/metrics",
            )
        ],
        virtual_machines=[node],
    )

    assert scrape.keys() == {
        "prometheus-server.monitoring.svc.cluster.local:9090",
        "192.0.2.41:9100",
        "198.51.100.11:9100",
    }
    assert scrape["192.0.2.41:9100"] == {
        "app": "storage",
        "exposure": "private",
        "site": "dc-b",
        "__metrics_path__": "/node/metrics",
    }
    assert scrape["198.51.100.11:9100"] == {"site": "dc-a", "__metrics_path__": "/metrics"}


# Probe: service DNS name and cluster port, or device or VM IP; only http_ modules add the path.
def test_probe_lane_targets_the_probe_address_and_path():
    node = virtual_machine(
        "k3s-node-1",
        ["monitoring:probe"],
        site_slug="dc-a",
        address="198.51.100.11/24",
        probe_module="icmp",
    )
    probe = _served(
        "probe",
        services=[
            service(
                "loki",
                ["monitoring:probe"],
                parent=node,
                cluster_service="loki",
                cluster_namespace="monitoring",
                cluster_port=3100,
                probe_path="/ready",
            ),
            service(
                "grafana",
                ["monitoring:probe"],
                parent=node,
                cluster_service="grafana",
                cluster_namespace="monitoring",
                cluster_port=3000,
                probe_path="api/health",
            ),
            service(
                "postgres",
                ["monitoring:probe"],
                parent=node,
                cluster_service="postgres",
                cluster_namespace="db",
                cluster_port=5432,
                probe_module="tcp_connect",
                probe_path="/ready",
            ),
        ],
        devices=[
            device(
                "encoder-1",
                ["monitoring:probe", "app:encoder", "exposure:public"],
                site_slug="dc-b",
                address="192.0.2.21/24",
                probe_module="icmp",
                probe_path="/status",
            )
        ],
        virtual_machines=[node],
    )

    assert probe.keys() == {
        "loki.monitoring.svc.cluster.local:3100/ready",
        "grafana.monitoring.svc.cluster.local:3000/api/health",
        "postgres.db.svc.cluster.local:5432",
        "192.0.2.21",
        "198.51.100.11",
    }
    assert probe["postgres.db.svc.cluster.local:5432"]["__param_module"] == "tcp_connect"
    assert probe["192.0.2.21"] == {
        "app": "encoder",
        "exposure": "public",
        "site": "dc-b",
        "__param_module": "icmp",
    }
    assert probe["198.51.100.11"] == {"site": "dc-a", "__param_module": "icmp"}


# SNMP: devices only, at their IP, with their SNMP module, or if_mib when it is unset.
def test_snmp_lane_polls_devices_with_their_module():
    node = virtual_machine(
        "dns-vm-1",
        ["monitoring:probe", "monitoring:snmp"],
        site_slug="dc-a",
        address="198.51.100.31/24",
        probe_module="icmp",
    )
    snmp = _served(
        "snmp",
        services=[
            service(
                "headless",
                ["monitoring:probe", "monitoring:snmp"],
                parent=node,
                cluster_service="headless",
                cluster_namespace="default",
                cluster_port=8080,
            )
        ],
        devices=[
            device(
                "router-1",
                ["monitoring:snmp", "app:router", "exposure:private"],
                address="192.0.2.31/24",
                snmp_module="arista_sw",
            ),
            device(
                "switch-1",
                ["monitoring:snmp"],
                site_slug="dc-b",
                address="192.0.2.32/24",
                snmp_module=None,
            ),
        ],
        virtual_machines=[node],
    )

    assert snmp == {
        "192.0.2.31": {
            "app": "router",
            "exposure": "private",
            "site": "dc-a",
            "__param_module": "arista_sw",
        },
        "192.0.2.32": {"site": "dc-b", "__param_module": "if_mib"},
    }


# A lane leaves out a record not tagged for it, missing the lane's field, or with no IP.
def test_lanes_leave_out_records_they_cannot_target():
    node = virtual_machine("k3s-node-1", [], site_slug="dc-a", address="198.51.100.11/24")
    payloads, _, _ = build_payloads(
        {
            "services": [
                # Probe module cleared; the metrics port alone doesn't put it on the scrape lane.
                service(
                    "legacy",
                    ["monitoring:probe"],
                    parent=node,
                    cluster_service="legacy",
                    cluster_namespace="default",
                    cluster_port=8080,
                    metrics_port=8080,
                    probe_module=None,
                ),
            ],
            "devices": [
                # No metrics port. Its default probe and SNMP modules don't put it on those lanes.
                device("build-1", ["monitoring:scrape"], address="192.0.2.61/24"),
                # No primary IP, so no lane has an address for it.
                device(
                    "switch-1",
                    ["monitoring:scrape", "monitoring:probe", "monitoring:snmp"],
                    metrics_port=9100,
                ),
            ],
            "virtual_machines": [],
        }
    )

    assert payloads == {"scrape": [], "probe": [], "snmp": []}
