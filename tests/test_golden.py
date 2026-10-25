"""promsd's behaviour for a fixed NetBox v4.5.0 inventory, pinned to a golden file.

The file holds the three /sd responses, the source counts from /healthz, and the
queries promsd sends NetBox, so a change in what promsd fetches shows up as well
as a change in what it serves. After a deliberate change, rewrite the file with
`PROMSD_UPDATE_GOLDEN=1 pytest tests/test_golden.py` and review its diff.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi.testclient import TestClient

from dmf_promsd.main import create_app
from dmf_promsd.settings import Settings
from tests.netbox45 import BASE_URL, TOKEN, FakeNetbox, device, service, virtual_machine

GOLDEN = Path(__file__).parent / "golden" / "sd-netbox-4.5.json"
LANES = ("scrape", "probe", "snmp")

NODE = device(
    "node-1",
    ["monitoring:scrape", "monitoring:probe", "monitoring:snmp", "exposure:private"],
    address="192.0.2.11/24",
    metrics_port=9100,
    probe_module="icmp",
)
# No primary IP, so no SNMP target.
SWITCH = device("switch-1", ["monitoring:snmp"])
ENCODER = device(
    "encoder-1",
    ["monitoring:probe", "app:encoder", "app:av", "exposure:public"],
    site_slug="dc-b",
    address="192.0.2.21/24",
    probe_path="status",
)
# SNMP goes to the cluster DNS name when the cluster fields are set.
GATEWAY = device(
    "gateway-1",
    ["monitoring:snmp"],
    site_slug="dc-b",
    address="192.0.2.31/24",
    cluster_service="gateway",
    cluster_namespace="edge",
    snmp_module=None,
)
# Scraping is its only monitoring tag, and its metrics path isn't the default.
STORAGE = device(
    "storage-1",
    ["monitoring:scrape", "app:storage", "app:nas"],
    address="192.0.2.41/24",
    metrics_port=9100,
    metrics_path="/node/metrics",
)
# A cluster service without a namespace isn't a cluster target.
MULTIVIEWER = device(
    "multiviewer-1",
    ["monitoring:probe", "app:multiviewer", "app:video"],
    address="192.0.2.51/24",
    cluster_service="multiviewer",
    probe_module="icmp",
)
K3S_NODE = virtual_machine(
    "k3s-node-1", ["monitoring:scrape"], address="198.51.100.11/24", metrics_port=9100
)
MEDIA_VM = virtual_machine(
    "media-vm-1",
    ["monitoring:probe", "app:media"],
    site_slug="dc-a",
    address="198.51.100.21/24",
    host=NODE,
    probe_module="tcp_connect",
    probe_path="/ready",
)
# IPv6 only, so no lane picks it up.
IPV6_VM = virtual_machine(
    "v6-vm-1", ["monitoring:scrape"], address="2001:db8::5/64", metrics_port=9100
)
# The SNMP lane covers devices only.
DNS_VM = virtual_machine(
    "dns-vm-1",
    ["monitoring:probe", "monitoring:snmp", "app:dns", "app:infra"],
    address="198.51.100.31/24",
    probe_module="icmp",
)
# Tagged for scraping without a metrics port, so skipped.
BUILD_VM = virtual_machine("build-vm-1", ["monitoring:scrape"], address="198.51.100.41/24")
# A namespace without a cluster service isn't a cluster target.
TRANSCODER_VM = virtual_machine(
    "transcoder-vm-1",
    ["monitoring:probe"],
    address="198.51.100.51/24",
    cluster_namespace="media",
    probe_module="tcp_connect",
)

SERVICES = [
    service(
        "grafana",
        ["monitoring:probe", "exposure:private"],
        parent=K3S_NODE,
        cluster_service="grafana",
        cluster_namespace="monitoring",
        cluster_port=80,
        probe_module="http_2xx_302",
    ),
    # Same name as the grafana above, on another host.
    service(
        "grafana",
        ["monitoring:probe"],
        parent=MEDIA_VM,
        cluster_service="grafana",
        cluster_namespace="media",
        cluster_port=3000,
    ),
    service(
        "loki",
        ["monitoring:probe", "exposure:private"],
        parent=K3S_NODE,
        cluster_service="loki",
        cluster_namespace="monitoring",
        cluster_port=3100,
        probe_path="/ready",
    ),
    service(
        "prometheus",
        ["monitoring:scrape", "monitoring:probe", "exposure:private"],
        parent=K3S_NODE,
        cluster_service="prometheus-server",
        cluster_namespace="monitoring",
        cluster_port=80,
        metrics_port=9090,
    ),
    # No cluster port, so the probe uses the metrics port.
    service(
        "zot",
        ["monitoring:probe"],
        parent=K3S_NODE,
        cluster_service="zot",
        cluster_namespace="zot",
        metrics_port=5000,
    ),
    # No port of any kind, so the probe target is the bare DNS name.
    service(
        "headless",
        ["monitoring:probe", "monitoring:snmp"],
        parent=K3S_NODE,
        cluster_service="headless",
        cluster_namespace="default",
    ),
    # Scraping is its only monitoring tag, and its metrics path was cleared.
    service(
        "node-exporter",
        ["monitoring:scrape", "app:node", "app:host"],
        parent=K3S_NODE,
        cluster_service="node-exporter",
        cluster_namespace="monitoring",
        metrics_port=9100,
        metrics_path=None,
    ),
    # No cluster fields, so no target, though NetBox gives it an address and a port.
    service(
        "nmos-registry",
        ["monitoring:scrape", "monitoring:probe", "app:nmos"],
        parent=ENCODER,
        addresses=["192.0.2.21/24"],
        ports=[8010],
        metrics_port=8010,
    ),
    # A cleared probe module drops it from the probe lane.
    service(
        "legacy",
        ["monitoring:probe"],
        parent=K3S_NODE,
        cluster_service="legacy",
        cluster_namespace="default",
        cluster_port=8080,
        probe_module=None,
    ),
    # No monitoring tag, so never fetched.
    service("landing", ["exposure:public"], parent=K3S_NODE),
]
DEVICES = [NODE, SWITCH, ENCODER, GATEWAY, STORAGE, MULTIVIEWER]
VIRTUAL_MACHINES = [K3S_NODE, MEDIA_VM, IPV6_VM, DNS_VM, BUILD_VM, TRANSCODER_VM]


def _canonical(group: dict) -> str:
    return json.dumps(group, sort_keys=True)


def _query(request: httpx.Request) -> str:
    return f"{request.url.path}?{urlencode(sorted(request.url.params.multi_items()))}"


def test_sd_lanes_match_golden_file():
    # A page size of 2 makes promsd follow `next` links.
    netbox = FakeNetbox(
        services=SERVICES, devices=DEVICES, virtual_machines=VIRTUAL_MACHINES, max_page_size=2
    )
    client = netbox.client()
    app = create_app(Settings(netbox_url=BASE_URL, netbox_token=TOKEN), client=client)
    try:
        with TestClient(app) as http:
            health = http.get("/healthz").json()
            responses = {lane: http.get(f"/sd/{lane}") for lane in LANES}
    finally:
        client.close()

    assert health["last_error"] is None
    for response in responses.values():
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/json"
    observed = {
        # Prometheus ignores the order of target groups, so the comparison does too.
        "sd": {
            lane: sorted(response.json(), key=_canonical) for lane, response in responses.items()
        },
        "source_counts": health["source_counts"],
        "netbox_queries": sorted(
            _query(request) for request in netbox.requests if "offset" not in request.url.params
        ),
    }

    if os.environ.get("PROMSD_UPDATE_GOLDEN") == "1":
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(observed, indent=2, sort_keys=True) + "\n")

    assert observed == json.loads(GOLDEN.read_text())
    assert any("offset" in request.url.params for request in netbox.requests)
