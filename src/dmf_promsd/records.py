from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

SERVICES = "services"
DEVICES = "devices"
VIRTUAL_MACHINES = "virtual_machines"
RECORD_KINDS = (SERVICES, DEVICES, VIRTUAL_MACHINES)


@dataclass(frozen=True)
class Record:
    kind: str
    tags: frozenset[str]
    # The primary IPv4 address, without its prefix length.
    address: str | None
    site: str | None
    # Custom field values, as text.
    fields: Mapping[str, str | None]
