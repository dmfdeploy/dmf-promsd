# dmf-promsd

[![CI](https://github.com/dmfdeploy/dmf-promsd/actions/workflows/ci.yml/badge.svg)](https://github.com/dmfdeploy/dmf-promsd/actions/workflows/ci.yml)

Turns NetBox inventory into Prometheus `http_sd` service discovery targets.

Part of [dmfdeploy](https://github.com/dmfdeploy/dmfdeploy), an independent open prototype that explores the ideas of the EBU Dynamic Media Facility on commodity infrastructure. Status: prototype, version 0.1.4.

## What it is for

The dmfdeploy platform keeps NetBox as its source of truth for inventory. `dmf-promsd` reads NetBox services, devices and virtual machines, and serves the ones that carry a monitoring tag as Prometheus [HTTP service discovery](https://prometheus.io/docs/prometheus/latest/http_sd/) target groups. Prometheus then scrapes or probes new workloads without anyone editing its configuration.

A record joins a lane when it has the matching tag in NetBox and the lane reads that record type:

| Tag | Endpoint | Reads | Lane |
|---|---|---|---|
| `monitoring:scrape` | `/sd/scrape` | Services, devices, virtual machines | Metrics endpoints. The target is the record's address plus its `metrics_port` custom field. The metrics path comes from the `metrics_path` custom field and defaults to `/metrics`. |
| `monitoring:probe` | `/sd/probe` | Services, devices, virtual machines | Blackbox probes. The `probe_module` custom field names the probe module and has no default. |
| `monitoring:snmp` | `/sd/snmp` | Devices only | SNMP targets. The `snmp_module` custom field names the SNMP module and defaults to `if_mib` when the device has none. Service and virtual machine records with this tag are ignored. |

A record with the `cluster_service` and `cluster_namespace` custom fields uses the cluster DNS name of that service as its address.

The service skips a record in a lane when it lacks a required field: `metrics_port` for scrape, `probe_module` for probe, or an address for any lane. It keeps the skip counts in memory and does not return them from `/healthz`.

`GET /healthz` returns the status, product name and version, the cache settings (`cache_ttl_seconds`, `http_sd_refresh` and `freshness_window_seconds`), `snapshot_age_seconds`, `source_counts` (the number of services, devices and virtual machines fetched from NetBox) and `last_error`.

`GET /metrics` exposes Prometheus client and OpenBao AppRole rotation metadata
metrics. The observer reads only three configured KV v2 metadata records and
uses the pod's projected Kubernetes service-account token to authenticate to
OpenBao. It never lists OpenBao paths, reads application secrets, or returns
raw metadata, credentials, or request errors. If the observer is not configured
or a record is missing, malformed, or unreadable, it reports target availability
as zero and removes expiry and run-state series derived from stale data. The
existing NetBox health and service-discovery behavior does not depend on the
observer configuration.

## Run it

You need Python 3.12 or later, and a NetBox instance with an API token that can read services, devices and virtual machines.

Supply the NetBox address and token through environment variables. The service refuses to start without `NETBOX_URL` and `NETBOX_TOKEN`. Set the token from your secret store at run time and keep it out of files in this repository.

```sh
python -m pip install -e '.[dev]'
export NETBOX_URL="https://netbox.example.com"
export NETBOX_TOKEN="<your NetBox API token>"
uvicorn dmf_promsd.main:create_app --factory --host 0.0.0.0 --port 8000
```

To run it in a container, build the image from the `Dockerfile` in this repository and pass the same variables:

```sh
docker build -t dmf-promsd .
docker run --rm -p 8000:8000 \
  -e NETBOX_URL -e NETBOX_TOKEN \
  dmf-promsd
```

Then point a Prometheus `http_sd_configs` entry at `http://<host>:8000/sd/scrape`, or at the probe or SNMP endpoint.

## Configuration

The service reads these environment variables:

- `NETBOX_URL` is the base URL of NetBox. Required.
- `NETBOX_TOKEN` is the NetBox API token. Required.
- `NETBOX_VALIDATE_CERTS` turns TLS certificate checks on or off. The default is `true`.
- `CACHE_TTL_SECONDS` sets how often the cache refreshes from NetBox. The default is `45`.
- `HTTP_SD_REFRESH` is a refresh hint only. The default is `30s`.
- `OPENBAO_ADDR` is the HTTPS address of OpenBao. It is unset until the
  observer identity is deployed.
- `OPENBAO_K8S_AUTH_MOUNT` is the OpenBao Kubernetes auth mount. The default is
  `kubernetes`.
- `OPENBAO_K8S_ROLE` is the observer auth role. The default is
  `dmf-promsd-observer`.
- `OPENBAO_SA_JWT_FILE` is the projected service-account token file. The
  default is the Kubernetes pod token path.
- `OPENBAO_OBSERVER_INTERVAL_SECONDS` controls metadata polling. The default
  is `60`; supported values are 10 through 3600 seconds.

## Endpoints

- `GET /sd/scrape`
- `GET /sd/probe`
- `GET /sd/snmp`
- `GET /healthz`
- `GET /readyz`
- `GET /metrics`

The adapter serves Prometheus target groups from an in-memory cache refreshed on
its own timer. NetBox is queried at a fixed low rate regardless of how many
Prometheus replicas poll the service.

The observer target list is currently a fixed three-target configuration and
must stay aligned with the infrastructure rotation registry. Prometheus scrape
configuration and alert rules are separate infrastructure work; this endpoint
does not claim that the metrics are already scraped or alerted on.

## License

Licensed under the [Apache 2.0 License](LICENSE).
See [NOTICE](NOTICE) for attribution notes.

## For contributors

`AGENTS.md`, `CLAUDE.md` and `QWEN.md` hold instructions for AI coding agents that help maintain this repository. For the human workflow, see [CONTRIBUTING.md](CONTRIBUTING.md).
