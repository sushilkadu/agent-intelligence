"""SSRF-safe validation for customer-supplied webhook URLs.

Shared between api-service (validates `webhook_url` once, at
`POST /v1/monitors` registration time) and notifier-service (re-validates
the SAME url immediately before every delivery attempt) -- like
`shared_utils/api_keys.py`'s hashing scheme, this is one of the few
helpers in this codebase that lives in `shared-utils` rather than being
copy-pasted per-service, because the two call sites MUST agree on
exactly what "safe" means or one of them becomes a false sense of
security.

--- Why this exists ---------------------------------------------------

A "register a URL, then our infrastructure repeatedly POSTs to it"
feature (monitor webhooks) is a classic SSRF vector: without
validation, a caller could register `http://169.254.169.254/latest/
meta-data/...` (the AWS/GCP/Azure instance-metadata address) or an
internal service's address (e.g. `http://10.0.0.5:6379`) and use this
product's own infrastructure as a proxy to probe or attack internal
resources it would otherwise have no network path to.

--- What this checks --------------------------------------------------

  1. Scheme MUST be `https://` -- also rules out `file://`, `gopher://`,
     etc. entirely.
  2. The hostname MUST resolve (via `socket.getaddrinfo`, so both IPv4
     and IPv6 results and any local `/etc/hosts`-style overrides are
     covered), and EVERY resolved address must be a normal, public
     address: not private (RFC 1918), not loopback, not link-local
     (this is the bucket the cloud-metadata address 169.254.169.254
     falls into -- 169.254.0.0/16 is link-local), not multicast, not
     "reserved," and not unspecified (0.0.0.0/::).

--- What this does NOT fully close: DNS rebinding ----------------------

Validating at registration time (api-service) proves the hostname
resolved somewhere safe AT THAT MOMENT. Nothing stops a malicious
registrant from later pointing the same hostname's DNS record at a
private/metadata address ("DNS rebinding") so that a FUTURE delivery
connects somewhere unsafe even though registration looked fine -- a
TTL-respecting resolver has no way to guarantee the answer won't change
between the two lookups (registration and delivery are minutes to
months apart).

The strongest mitigation available without a much larger rework
(pinning the resolved IP at registration time and connecting to that
literal IP thereafter, with a Host header override -- unnecessary
complexity for this phase's scale) is to run this SAME check again
immediately before every delivery attempt, not just once at
registration. That's exactly why notifier-service calls this function
too, not just api-service -- see notifier/webhook.py. This is
documented as a known residual risk, not silently assumed away.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Callable
from urllib.parse import urlparse


class UnsafeWebhookURLError(ValueError):
    """Raised when a webhook URL fails the safety checks below. Callers
    (api-service's route, notifier-service's delivery path) turn this
    into a 400 response / a skipped delivery, never an unhandled crash.
    """


def _default_resolve(hostname: str) -> list[str]:
    """Resolve `hostname` to every distinct IP address it maps to (IPv4
    and IPv6), via the standard library's resolver -- no third-party
    DNS dependency needed.
    """
    infos = socket.getaddrinfo(hostname, None)
    return sorted({info[4][0] for info in infos})


def _is_unsafe_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True if `ip` is anything other than a normal public address.

    Each property is checked explicitly (rather than relying solely on
    `is_private`, which already happens to cover most of these ranges)
    so the intent of each check -- loopback, link-local (which is where
    the 169.254.169.254 cloud metadata address lives), multicast,
    reserved, unspecified -- is legible on its own, not folded into one
    opaque boolean.
    """
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return True
    # An IPv4-mapped IPv6 address (::ffff:169.254.169.254) wraps an
    # IPv4 address that the checks above, run against the IPv6 form
    # alone, would not catch -- unwrap and re-check the embedded IPv4
    # address too.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None and _is_unsafe_ip(mapped):
        return True
    return False


def validate_webhook_url(url: str, *, resolver: Callable[[str], list[str]] | None = None) -> None:
    """Raise `UnsafeWebhookURLError` if `url` is not safe for this
    product to register or connect to as a webhook destination.

    `resolver` is injectable for tests (avoid real DNS lookups) --
    production callers should omit it and get the real
    `socket.getaddrinfo`-backed resolution.
    """
    parsed = urlparse(url)

    if parsed.scheme != "https":
        raise UnsafeWebhookURLError("webhook_url must use the https:// scheme")

    hostname = parsed.hostname
    if not hostname:
        raise UnsafeWebhookURLError("webhook_url must include a hostname")

    resolve = resolver or _default_resolve
    try:
        addresses = resolve(hostname)
    except socket.gaierror as exc:
        raise UnsafeWebhookURLError(f"webhook_url host '{hostname}' could not be resolved") from exc

    if not addresses:
        raise UnsafeWebhookURLError(f"webhook_url host '{hostname}' did not resolve to any address")

    for address in addresses:
        ip = ipaddress.ip_address(address)
        if _is_unsafe_ip(ip):
            raise UnsafeWebhookURLError(
                f"webhook_url host '{hostname}' resolves to a disallowed address ({address}) -- "
                "private, loopback, link-local (including the cloud metadata address), multicast, "
                "reserved, and unspecified addresses are not permitted"
            )


__all__ = ["UnsafeWebhookURLError", "validate_webhook_url"]
