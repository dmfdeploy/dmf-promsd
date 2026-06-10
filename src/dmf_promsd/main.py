from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from . import __version__
from .cache import PromSDCache
from .netbox import NetboxClient
from .settings import Settings, load_settings


def create_app(
    settings: Settings | None = None,
    *,
    cache: PromSDCache | None = None,
    client: NetboxClient | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    if not settings.configured and cache is None and client is None:
        raise RuntimeError("NETBOX_URL and NETBOX_TOKEN must be configured")

    managed_client = client is None
    managed_cache = cache is None
    if client is None and settings.configured:
        client = NetboxClient(
            base_url=settings.netbox_url,
            token=settings.netbox_token,
            validate_certs=settings.netbox_validate_certs,
        )
    if cache is None:
        if client is None:
            raise RuntimeError("NetBox client is required when no cache is injected")
        cache = PromSDCache(
            client=client,
            ttl_seconds=settings.cache_ttl_seconds,
            refresh_hint=settings.http_sd_refresh,
            managed=managed_cache,
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime_cache = app.state.cache
        runtime_client = app.state.client
        if getattr(runtime_cache, "_managed", False):
            runtime_cache.start()
        try:
            yield
        finally:
            if getattr(runtime_cache, "_managed", False):
                runtime_cache.stop()
            if runtime_client is not None and managed_client:
                runtime_client.close()

    app = FastAPI(
        title="DMF PromSD",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.client = client
    app.state.cache = cache
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> JSONResponse:
        payload = app.state.cache.health_payload()
        payload.update({"status": "ok", "product": "DMF PromSD", "version": __version__})
        return JSONResponse(payload)

    @app.get("/readyz", include_in_schema=False)
    async def readyz() -> JSONResponse:
        status_code, payload = app.state.cache.ready_payload()
        payload.update({"product": "DMF PromSD", "version": __version__})
        return JSONResponse(payload, status_code=status_code)

    @app.get("/sd/scrape", include_in_schema=False)
    async def sd_scrape() -> JSONResponse:
        return JSONResponse(app.state.cache.lane_payload("scrape"))

    @app.get("/sd/probe", include_in_schema=False)
    async def sd_probe() -> JSONResponse:
        return JSONResponse(app.state.cache.lane_payload("probe"))

    @app.get("/sd/snmp", include_in_schema=False)
    async def sd_snmp() -> JSONResponse:
        return JSONResponse(app.state.cache.lane_payload("snmp"))

    return app
