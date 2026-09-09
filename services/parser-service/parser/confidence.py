"""Confidence-flag computation.

Flags are computed strictly from what was actually *present* at crawl
time -- a signal that was never fetched can't also be "malformed", and
a domain with at least one signal present is never `no_signals`.
"""

from __future__ import annotations

NO_SIGNALS = "no_signals"
MALFORMED_MANIFEST = "malformed_manifest"
MALFORMED_WEB_BOT_AUTH = "malformed_web_bot_auth"
EXPIRED_KEY = "expired_key"


def compute_confidence_flags(
    *,
    agents_json_present: bool,
    manifest_malformed: bool,
    web_bot_auth_present: bool,
    web_bot_auth_malformed: bool,
    web_bot_auth_valid: bool,
) -> list[str]:
    """Compute the `confidence_flags` list for one crawl.

    - `no_signals`: neither signal was present at all. Deliberately
      does NOT also set malformed_manifest/malformed_web_bot_auth for a
      signal that was never fetched -- those only apply when a signal
      *was* present but couldn't be parsed.
    - `malformed_manifest`: agents.json was present but wasn't a valid
      JSON object (see manifest.py's lenient-validation rationale).
    - `malformed_web_bot_auth`: the Web Bot Auth JWKS directory was
      present but wasn't a parseable JWKS with at least one usable key
      (see webbotauth.py). Kept as a distinct flag from
      `malformed_manifest` rather than reusing it, since the two
      signals are validated independently and a consumer (e.g.
      notifier-service, the API) may care which specific artifact
      failed to parse.
    - `expired_key`: the Web Bot Auth directory was present, parsed
      successfully, but its selected key is not currently valid (its
      `exp` is in the past, or it has no usable `exp` at all -- a key
      whose validity can't be confirmed is treated the same as an
      expired one, since both mean "don't trust this as a live
      signing key").
    """
    flags: list[str] = []

    if not agents_json_present and not web_bot_auth_present:
        flags.append(NO_SIGNALS)
        return flags

    if agents_json_present and manifest_malformed:
        flags.append(MALFORMED_MANIFEST)

    if web_bot_auth_present:
        if web_bot_auth_malformed:
            flags.append(MALFORMED_WEB_BOT_AUTH)
        elif not web_bot_auth_valid:
            flags.append(EXPIRED_KEY)

    return flags


__all__ = [
    "EXPIRED_KEY",
    "MALFORMED_MANIFEST",
    "MALFORMED_WEB_BOT_AUTH",
    "NO_SIGNALS",
    "compute_confidence_flags",
]
