# dmf-promsd

[![CI](https://github.com/dmfdeploy/dmf-promsd/actions/workflows/ci.yml/badge.svg)](https://github.com/dmfdeploy/dmf-promsd/actions/workflows/ci.yml)

Turns NetBox inventory into Prometheus `http_sd` service discovery targets.

Part of [dmfdeploy](https://github.com/dmfdeploy/dmfdeploy), an independent open prototype that explores the ideas of the EBU Dynamic Media Facility on commodity infrastructure. Status: prototype, version 0.1.4.

## What it is for

The dmfdeploy platform keeps NetBox as its source of truth for inventory. `dmf-promsd` reads NetBox services, devices and virtual machines, and serves the ones that carry a monitoring tag as Prometheus [HTTP service discovery](https://prometheus.io/docs/prometheus/latest/http_sd/) target groups. Prometheus then scrapes or probes new workloads without anyone editing its configuration.

A record joins a lane when it has the matching tag in NetBox:

| Tag | Endpoint | Lane |
|---|---|---|
| `monitoring:scrape` | `/sd/scrape` | Metrics endpoints. The target is the record's address and its `metrics_port` custom field. |
| `monitoring:probe` | `/sd/probe` | Blackbox probes. The `probe_module` custom field names the probe module. |
| `monitoring:snmp` | `/sd/snmp` | SNMP targets. The `snmp_module` custom field names the SNMP module. |

Records that lack a required field are skipped, and the skip counts appear in the `/healthz` response.

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

## Endpoints

- `GET /sd/scrape`
- `GET /sd/probe`
- `GET /sd/snmp`
- `GET /healthz`
- `GET /readyz`

The adapter serves Prometheus target groups from an in-memory cache refreshed on
its own timer. NetBox is queried at a fixed low rate regardless of how many
Prometheus replicas poll the service.

## License

Licensed under the [Apache 2.0 License](LICENSE).
See [NOTICE](NOTICE) for attribution notes.

## For contributors

`AGENTS.md`, `CLAUDE.md` and `QWEN.md` hold instructions for AI coding agents that help maintain this repository. For the human workflow, see [CONTRIBUTING.md](CONTRIBUTING.md).
