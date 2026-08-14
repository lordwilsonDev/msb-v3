"""Transport admission policy for the Vesta control surface.

WireGuard deployment is an operations concern, but the application still
needs a fail-closed admission check once tunnel-only mode is enabled. The
check uses the direct peer address and never trusts X-Forwarded-For headers.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

from fastapi import HTTPException, Request

from msb_v3.core.config import settings


@dataclass(frozen=True)
class TransportAdmission:
    required: bool
    allowed_networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]

    @classmethod
    def from_settings(cls) -> "TransportAdmission":
        values = [value.strip() for value in settings.vesta_allowed_cidrs.split(",") if value.strip()]
        try:
            networks = tuple(ipaddress.ip_network(value, strict=False) for value in values)
        except ValueError as exc:
            raise RuntimeError("MSB_VESTA_ALLOWED_CIDRS contains an invalid network") from exc
        return cls(settings.vesta_require_tunnel, networks)

    def allows(self, peer_host: str | None) -> bool:
        if not self.required:
            return True
        if not peer_host:
            return False
        try:
            address = ipaddress.ip_address(peer_host)
        except ValueError:
            return False
        return any(address in network for network in self.allowed_networks)


def require_vesta_transport(request: Request) -> None:
    admission = TransportAdmission.from_settings()
    peer_host = request.client.host if request.client else None
    if not admission.allows(peer_host):
        raise HTTPException(
            status_code=403,
            detail="Vesta requires an authenticated private transport peer",
        )
