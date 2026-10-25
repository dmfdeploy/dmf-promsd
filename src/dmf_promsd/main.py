from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from . import __version__
from .cache import PromSDCache
from .netbox import NetboxClient
from .sd import LANES
from .settings import Settings, load_settings

_PRODUCT = "DMF PromSD"


def _health_payload(cache: PromSDCache, refresh_hint: str) -> dict[str, Any]:
    state = cache.state()
    return {
        "status": "ok",
        "cache_ttl_seconds": cache.ttl_seconds,
        "http_sd_refresh": refresh_hint,
        "freshness_window_seconds": cache.freshness_window_seconds,
        "snapshot_age_seconds": state.age_seconds,
        "source_counts": state.snapshot.source_counts if state.snapshot else {},
        "last_error": state.last_error,
    }


def _ready_payload(cache: PromSDCache) -> tuple[int, dict[str, Any]]:
    state = cache.state()
    if state.snapshot is None:
        return 503, {"status": "stale", "ready": False, "reason": "no snapshot"}
    status_code = 200 if state.fresh else 503
    payload = {
        "status": "ready" if state.fresh else "stale",
        "ready": state.fresh,
        "snapshot_age_seconds": state.age_seconds,
        "freshness_window_seconds": cache.freshness_window_seconds,
        "last_error": state.last_error,
    }
    return status_code, payload


def _serve_lane(app: FastAPI, lane: str) -> None:
    @app.get(f"/sd/{lane}", include_in_schema=False, name=f"sd_{lane}")
    async def sd() -> JSONResponse:
        return JSONResponse(app.state.cache.lane_payload(lane))


def create_app(
    settings: Settings | None = None,
    *,
    cache: PromSDCache | None = None,
    client: NetboxClient | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    if not settings.configured and cache is None and client is None:
        raise RuntimeError("NETBOX_URL and NETBOX_TOKEN must be configured")

    own_client = None
    if client is None and settings.configured:
        client = own_client = NetboxClient(
            base_url=settings.netbox_url,
            token=settings.netbox_token,
            validate_certs=settings.netbox_validate_certs,
        )
    own_cache = None
    if cache is None:
        cache = own_cache = PromSDCache(client=client, ttl_seconds=settings.cache_ttl_seconds)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if own_cache is not None:
            own_cache.start()
        try:
            yield
        finally:
            if own_cache is not None:
                own_cache.stop()
            if own_client is not None:
                own_client.close()

    app = FastAPI(
        title=_PRODUCT,
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
        payload = _health_payload(app.state.cache, app.state.settings.http_sd_refresh)
        payload.update({"product": _PRODUCT, "version": __version__})
        return JSONResponse(payload)

    @app.get("/readyz", include_in_schema=False)
    async def readyz() -> JSONResponse:
        status_code, payload = _ready_payload(app.state.cache)
        payload.update({"product": _PRODUCT, "version": __version__})
        return JSONResponse(payload, status_code=status_code)

    for lane in LANES:
        _serve_lane(app, lane.name)

    return app
