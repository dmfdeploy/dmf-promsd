from __future__ import annotations

from dmf_promsd.sd import _extract_address, _strip_mask, build_payloads


def _service(**overrides):
    base = {
        "id": 1,
        "name": "service-one",
        "tags": [
            {"name": "monitoring:scrape"},
            {"name": "monitoring:probe"},
            {"name": "app:encoder"},
            {"name": "exposure:internal"},
        ],
        "site": {"slug": "site-a"},
        "custom_fields": {
            "metrics_port": 9100,
            "metrics_path": "/metrics",
            "probe_module": "http_2xx",
        },
        "ip_address": {"address": "dmf.example.com"},
    }
    base.update(overrides)
    return base


def _device(**overrides):
    base = {
        "id": 2,
        "name": "device-one",
        "tags": [
            {"name": "monitoring:snmp"},
            {"name": "monitoring:probe"},
            {"name": "app:router"},
            {"name": "exposure:public"},
        ],
        "site": {"slug": "site-b"},
        "custom_fields": {"snmp_module": "if_mib", "probe_module": "icmp"},
        "primary_ip4": {"address": "dmf.example.com"},
    }
    base.update(overrides)
    return base


def _vm(**overrides):
    base = {
        "id": 3,
        "name": "vm-one",
        "tags": [
            {"name": "monitoring:scrape"},
            {"name": "app:worker"},
            {"name": "exposure:internal"},
        ],
        "site": {"slug": "site-c"},
        "custom_fields": {"metrics_port": 9200},
        "primary_ip4": {"address": "vm.example.com"},
    }
    base.update(overrides)
    return base


def test_build_payloads_shapes_all_lanes():
    payloads, skipped, source_counts = build_payloads(
        services=[_service()],
        devices=[_device()],
        virtual_machines=[_vm()],
    )

    assert source_counts == {"services": 1, "devices": 1, "virtual_machines": 1}

    scrape = payloads["scrape"]
    assert scrape == [
        {
            "targets": ["dmf.example.com:9100"],
            "labels": {
                "app": "encoder",
                "exposure": "internal",
                "site": "site-a",
                "__metrics_path__": "/metrics",
            },
        },
        {
            "targets": ["vm.example.com:9200"],
            "labels": {
                "app": "worker",
                "exposure": "internal",
                "site": "site-c",
                "__metrics_path__": "/metrics",
            },
        },
    ]

    probe = payloads["probe"]
    assert probe == [
        {
            "targets": ["dmf.example.com:9100"],
            "labels": {
                "app": "encoder",
                "exposure": "internal",
                "site": "site-a",
                "__param_module": "http_2xx",
            },
        },
        {
            "targets": ["dmf.example.com"],
            "labels": {
                "app": "router",
                "exposure": "public",
                "site": "site-b",
                "__param_module": "icmp",
            },
        },
    ]

    snmp = payloads["snmp"]
    assert snmp == [
        {
            "targets": ["dmf.example.com"],
            "labels": {
                "app": "router",
                "exposure": "public",
                "site": "site-b",
                "__param_module": "if_mib",
            },
        }
    ]
    assert skipped == {"scrape": {}, "probe": {}, "snmp": {}}


def test_cluster_service_uses_service_dns_name_and_port():
    payloads, skipped, _ = build_payloads(
        services=[
            _service(
                custom_fields={
                    "metrics_port": 8080,
                    "metrics_path": "/metrics",
                    "probe_module": "http_2xx",
                    "cluster_service": "grafana",
                    "cluster_namespace": "monitoring",
                    "cluster_port": 3000,
                }
            )
        ],
        devices=[],
        virtual_machines=[],
    )

    assert payloads["scrape"] == [
        {
            "targets": ["grafana.monitoring.svc.cluster.local:8080"],
            "labels": {
                "app": "encoder",
                "exposure": "internal",
                "site": "site-a",
                "__metrics_path__": "/metrics",
            },
        }
    ]
    assert payloads["probe"] == [
        {
            "targets": ["grafana.monitoring.svc.cluster.local:3000"],
            "labels": {
                "app": "encoder",
                "exposure": "internal",
                "site": "site-a",
                "__param_module": "http_2xx",
            },
        }
    ]
    assert payloads["snmp"] == []
    assert skipped == {"scrape": {}, "probe": {}, "snmp": {}}


def test_missing_required_fields_and_tagless_objects_are_skipped():
    payloads, skipped, _ = build_payloads(
        services=[
            _service(custom_fields={"metrics_path": "/metrics"}),
            _service(tags=[{"name": "app:encoder"}]),
        ],
        devices=[
            _device(custom_fields={"snmp_module": "if_mib"}),
            _device(tags=[{"name": "app:router"}]),
        ],
        virtual_machines=[
            _vm(custom_fields={}),
        ],
    )

    assert payloads["scrape"] == []
    assert payloads["probe"] == []
    assert payloads["snmp"] == [
        {
            "targets": ["dmf.example.com"],
            "labels": {
                "app": "router",
                "exposure": "public",
                "site": "site-b",
                "__param_module": "if_mib",
            },
        }
    ]
    assert skipped["scrape"]["missing_metrics_port"] == 2
    assert skipped["probe"]["missing_module"] == 2
    assert skipped["snmp"] == {}


def test_snmp_defaults_to_if_mib_when_module_is_unset():
    payloads, skipped, _ = build_payloads(
        services=[],
        devices=[_device(custom_fields={"probe_module": "icmp"})],
        virtual_machines=[],
    )

    assert payloads["snmp"] == [
        {
            "targets": ["dmf.example.com"],
            "labels": {
                "app": "router",
                "exposure": "public",
                "site": "site-b",
                "__param_module": "if_mib",
            },
        }
    ]
    assert skipped["snmp"] == {}


def test_extract_address_strips_prefix_length():
    assert _strip_mask("192.0.2.5/24") == "192.0.2.5"
    assert _extract_address({"address": "192.0.2.5/24"}) == "192.0.2.5"
