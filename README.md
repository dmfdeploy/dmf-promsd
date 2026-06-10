# dmf-promsd

NetBox to Prometheus `http_sd` adapter for the DMF platform.

## Configuration

Set:

- `NETBOX_URL`
- `NETBOX_TOKEN`
- `NETBOX_VALIDATE_CERTS`
- `CACHE_TTL_SECONDS` (default `45`)
- `HTTP_SD_REFRESH` (hint only, default `30s`)

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

Copyright 2026 DMF Platform contributors. Licensed under the [Apache 2.0 License](LICENSE).
See [NOTICE](NOTICE) for attribution notes.
