from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return int(value)


@dataclass(frozen=True)
class Settings:
    netbox_url: str = ""
    netbox_token: str = ""
    netbox_validate_certs: bool = True
    cache_ttl_seconds: int = 45
    http_sd_refresh: str = "30s"
    bind_host: str = "0.0.0.0"
    bind_port: int = 8000

    @property
    def configured(self) -> bool:
        return bool(self.netbox_url and self.netbox_token)


def load_settings() -> Settings:
    return Settings(
        netbox_url=os.getenv("NETBOX_URL", ""),
        netbox_token=os.getenv("NETBOX_TOKEN", ""),
        netbox_validate_certs=_env_bool("NETBOX_VALIDATE_CERTS", True),
        cache_ttl_seconds=_env_int("CACHE_TTL_SECONDS", 45),
        http_sd_refresh=os.getenv("HTTP_SD_REFRESH", "30s"),
        bind_host=os.getenv("BIND_HOST", "0.0.0.0"),
        bind_port=_env_int("BIND_PORT", 8000),
    )
