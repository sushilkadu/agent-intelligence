"""Validation of the Web Bot Auth JWKS directory.

Per `draft-meunier-http-message-signatures-directory` (rev 05,
https://datatracker.ietf.org/doc/html/draft-meunier-http-message-signatures-directory-05),
the fetched document is a JWKS (`{"keys": [...]}`) where each key entry
carries standard JWK fields (`kty`, `crv`, `kid`, `x`, `use`, ...) plus
two Web-Bot-Auth-specific fields: `nbf` (not-before) and `exp`
(expiration), both Unix timestamps.

Key-selection judgment call: the draft explicitly supports key
rotation via multiple entries in `keys` with overlapping validity
windows, so there is no single "the" key in general. This module picks
the key with the *latest* `exp` -- i.e. the most-recently-rotated-in
key, which is the one most likely to still be valid and the one a
verifier would want to advertise/report on. A key missing `exp`
entirely is treated as the least-preferred (sorts before any key that
has one), since a key with no stated expiry can't be evaluated as
"valid" per this field anyway. Document this choice here since it's a
judgment call, not something the draft mandates.

Malformed/unparseable input (bad JSON, not an object, no usable `keys`
list) is reported via `malformed=True` rather than raising -- this must
never crash the pipeline, same as manifest.py.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class ParsedWebBotAuth:
    """Result of validating one Web Bot Auth JWKS directory payload.

    `malformed_reason` is a short, specific, human-readable diagnostic,
    always set together with `malformed=True`. This matters more here
    than almost anywhere else in the codebase: real testing against
    Shopify's actual deployed directory found it serves a bare JWK
    object instead of a `{"keys": [...]}` array -- a plain "couldn't be
    parsed" doesn't tell a site owner THAT specific, actionable fact,
    but the reason string does.
    """

    key_id: str | None = None
    expiry: datetime | None = None
    valid: bool = False
    malformed: bool = False
    malformed_reason: str | None = None


def _key_sort_value(key: dict) -> float:
    exp = key.get("exp")
    if isinstance(exp, (int, float)) and not isinstance(exp, bool):
        return float(exp)
    return float("-inf")


def parse_web_bot_auth(raw_bytes: bytes | None, *, now: datetime | None = None) -> ParsedWebBotAuth:
    """Parse `raw_bytes` (the decoded body of a fetched Web Bot Auth
    JWKS directory) and determine validity as of `now` (defaults to
    the current UTC time). `raw_bytes=None` means the signal was never
    fetched -- not malformed, see confidence.py's `no_signals`.
    """
    if raw_bytes is None:
        return ParsedWebBotAuth()

    now = now or datetime.now(timezone.utc)

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return ParsedWebBotAuth(malformed=True, malformed_reason="the response body isn't valid UTF-8 text")

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return ParsedWebBotAuth(malformed=True, malformed_reason=f"not valid JSON ({exc.msg})")

    if not isinstance(parsed, dict):
        return ParsedWebBotAuth(
            malformed=True,
            malformed_reason="the top-level value isn't a JSON object (expected a JWKS: {\"keys\": [...]})",
        )

    keys = parsed.get("keys")
    if not isinstance(keys, list) or not keys:
        # This is exactly what real testing found Shopify's actual
        # deployed directory does: a bare single JWK object with no
        # "keys" wrapper at all, per the current draft's documented
        # JWKS format -- see the module docstring's Cloudflare-docs
        # citation. Naming that specific expectation here is far more
        # actionable than a generic "malformed" for a site owner who
        # (like Shopify, evidently) shipped a directory missing it.
        return ParsedWebBotAuth(
            malformed=True,
            malformed_reason='missing a non-empty top-level "keys" array (a JWKS is expected, not a bare key object)',
        )

    valid_key_dicts = [k for k in keys if isinstance(k, dict)]
    if not valid_key_dicts:
        return ParsedWebBotAuth(
            malformed=True,
            malformed_reason='the "keys" array contains no valid key objects',
        )

    chosen = max(valid_key_dicts, key=_key_sort_value)

    key_id = chosen.get("kid")
    key_id = str(key_id) if key_id is not None else None

    exp = chosen.get("exp")
    expiry: datetime | None = None
    if isinstance(exp, (int, float)) and not isinstance(exp, bool):
        expiry = datetime.fromtimestamp(exp, tz=timezone.utc)

    valid = expiry is not None and expiry > now

    return ParsedWebBotAuth(key_id=key_id, expiry=expiry, valid=valid, malformed=False)


__all__ = ["ParsedWebBotAuth", "parse_web_bot_auth"]
