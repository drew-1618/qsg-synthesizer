"""Deterministic IP/subnet validation helpers.

Wraps the stdlib `ipaddress` module so extraction subagents never have to
guess at IP semantics themselves.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SubnetInfo:
    cidr: str
    network_address: str
    netmask: str
    prefix_length: int
    num_addresses: int
    is_private: bool


@dataclass(frozen=True)
class GatewayCheck:
    gateway: str
    subnet: str
    is_valid_ip: bool
    in_subnet: bool
    is_private: Optional[bool]


class InvalidNetworkInput(ValueError):
    """Raised when a subnet or IP string cannot be parsed."""


def parse_subnet(cidr: str) -> SubnetInfo:
    """Parse and validate a subnet given in CIDR notation (e.g. '192.168.1.0/24')."""
    try:
        network = ipaddress.ip_network(cidr, strict=False)
    except ValueError as exc:
        raise InvalidNetworkInput(f"'{cidr}' is not a valid subnet: {exc}") from exc

    return SubnetInfo(
        cidr=str(network),
        network_address=str(network.network_address),
        netmask=str(network.netmask),
        prefix_length=network.prefixlen,
        num_addresses=network.num_addresses,
        is_private=network.is_private,
    )


def classify_ip(ip: str) -> bool:
    """Return True if `ip` falls within RFC1918 (or other private/reserved) ranges."""
    try:
        address = ipaddress.ip_address(ip)
    except ValueError as exc:
        raise InvalidNetworkInput(f"'{ip}' is not a valid IP address: {exc}") from exc

    return address.is_private


def validate_gateway(gateway: str, subnet: str) -> GatewayCheck:
    """Check whether `gateway` is a valid IP address that lives inside `subnet`."""
    try:
        gateway_ip = ipaddress.ip_address(gateway)
    except ValueError:
        return GatewayCheck(
            gateway=gateway,
            subnet=subnet,
            is_valid_ip=False,
            in_subnet=False,
            is_private=None,
        )

    try:
        network = ipaddress.ip_network(subnet, strict=False)
    except ValueError as exc:
        raise InvalidNetworkInput(f"'{subnet}' is not a valid subnet: {exc}") from exc

    return GatewayCheck(
        gateway=str(gateway_ip),
        subnet=str(network),
        is_valid_ip=True,
        in_subnet=gateway_ip in network,
        is_private=gateway_ip.is_private,
    )
