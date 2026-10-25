"""Deterministic IP classification helper.

Wraps the stdlib `ipaddress` module so extraction subagents never have to
guess at IP semantics themselves.
"""

from __future__ import annotations

import ipaddress


class InvalidNetworkInput(ValueError):
    """Raised when an IP string cannot be parsed."""


def classify_ip(ip: str) -> bool:
    """Return True if `ip` falls within RFC1918 (or other private/reserved) ranges."""
    try:
        address = ipaddress.ip_address(ip)
    except ValueError as exc:
        raise InvalidNetworkInput(f"'{ip}' is not a valid IP address: {exc}") from exc

    return address.is_private
