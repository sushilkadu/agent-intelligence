"""Shared test fixtures.

Unit tests must never depend on real DNS/network resolution --
`shared_utils.webhook_safety.validate_webhook_url` calls the real
`socket.getaddrinfo` by default, which would otherwise make every test
that exercises a symbolic (non-literal-IP) webhook hostname depend on
outbound DNS access being available in whatever environment the suite
runs in (CI, a sandboxed dev machine, etc.). This autouse fixture
replaces `socket.getaddrinfo` with a fake that resolves any symbolic
hostname to a fixed public IP and leaves literal-IP lookups (e.g.
"169.254.169.254", "10.0.0.5") untouched -- those don't need real DNS
and are exactly what the SSRF-rejection tests want to exercise for
real.
"""

from __future__ import annotations

import ipaddress
import socket

import pytest

_REAL_GETADDRINFO = socket.getaddrinfo
_FAKE_PUBLIC_IP = "93.184.216.34"  # a real, public, non-sensitive IANA example address


def _fake_getaddrinfo(host, *args, **kwargs):
    try:
        ipaddress.ip_address(host)
    except ValueError:
        # A symbolic hostname -- resolve it to a fixed public IP rather
        # than hitting real DNS.
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_FAKE_PUBLIC_IP, 0))]
    # Already a literal IP address -- real getaddrinfo handles this
    # without any network access, so let it through unmodified (this is
    # exactly what the private-IP/metadata-address rejection tests rely
    # on resolving "for real").
    return _REAL_GETADDRINFO(host, *args, **kwargs)


@pytest.fixture(autouse=True)
def _fake_dns(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    yield
