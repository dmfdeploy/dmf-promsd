from __future__ import annotations

import os
from dataclasses import dataclass

_SERVICE_ACCOUNT_TOKEN = "/var/run/secrets/kubernetes.io/serviceaccount/token"


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
class ObserverSettings:
    address: str = ""
    auth_mount: str = "kubernetes"
    auth_role: str = "dmf-promsd-observer"
    jwt_file: str = _SERVICE_ACCOUNT_TOKEN
    interval_seconds: int = 60

    @property
    def configured(self) -> bool:
        return bool(
            self.address.startswith("https://")
            and self.auth_mount
            and self.auth_role
            and self.jwt_file
            and 10 <= self.interval_seconds <= 3600
        )


@dataclass(frozen=True)
class Settings:
    netbox_url: str = ""
    netbox_token: str = ""
    netbox_validate_certs: bool = True
    cache_ttl_seconds: int = 45
    http_sd_refresh: str = "30s"
    openbao_address: str = ""
    openbao_auth_mount: str = "kubernetes"
    openbao_auth_role: str = "dmf-promsd-observer"
    openbao_jwt_file: str = "/var/run/secrets/kubernetes.io/serviceaccount/token"
    openbao_observer_interval_seconds: int = 60

    @property
    def configured(self) -> bool:
        return bool(self.netbox_url and self.netbox_token)

    @property
    def observer(self) -> ObserverSettings:
        return ObserverSettings(
            address=self.openbao_address,
            auth_mount=self.openbao_auth_mount,
            auth_role=self.openbao_auth_role,
            jwt_file=self.openbao_jwt_file,
            interval_seconds=self.openbao_observer_interval_seconds,
        )


def load_settings() -> Settings:
    return Settings(
        netbox_url=os.getenv("NETBOX_URL", Settings.netbox_url),
        netbox_token=os.getenv("NETBOX_TOKEN", Settings.netbox_token),
        netbox_validate_certs=_env_bool("NETBOX_VALIDATE_CERTS", Settings.netbox_validate_certs),
        cache_ttl_seconds=_env_int("CACHE_TTL_SECONDS", Settings.cache_ttl_seconds),
        http_sd_refresh=os.getenv("HTTP_SD_REFRESH", Settings.http_sd_refresh),
        openbao_address=os.getenv("OPENBAO_ADDR", Settings.openbao_address),
        openbao_auth_mount=os.getenv("OPENBAO_K8S_AUTH_MOUNT", Settings.openbao_auth_mount),
        openbao_auth_role=os.getenv("OPENBAO_K8S_ROLE", Settings.openbao_auth_role),
        openbao_jwt_file=os.getenv("OPENBAO_SA_JWT_FILE", Settings.openbao_jwt_file),
        openbao_observer_interval_seconds=_env_int(
            "OPENBAO_OBSERVER_INTERVAL_SECONDS", Settings.openbao_observer_interval_seconds
        ),
    )
