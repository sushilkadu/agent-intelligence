"""API key generation + hashing, shared between api-service (which
verifies keys on every request) and billing-service (which mints them
at Stripe checkout time).

This is deliberately a *shared* module rather than a copy-pasted helper
in each service, unlike most of this codebase's "each service gets its
own small copy" convention (see api-service's `api/db.py` docstring for
that general rule and why). The one exception that matters here: the
hash api-service computes to look a key up MUST be byte-for-byte the
same hash billing-service computed at issuance, or every real key would
fail auth. That's a correctness invariant across a service boundary,
not just a style preference -- so the one function that has to agree
lives in one place both services already depend on (`shared-utils`),
instead of two independently-maintained copies that could silently
drift (e.g. one gets updated to a different digest and the other
doesn't).

--- Key shape -------------------------------------------------------------

Issued keys look like `ai_live_<43 url-safe base64 chars>` --
`secrets.token_urlsafe(32)` produces 32 bytes of CSPRNG entropy encoded
as ~43 URL-safe characters, comfortably more entropy than the "32+
chars" the build plan asks for. The `ai_live_` prefix is a common
pattern (Stripe's own `sk_live_`/`pk_test_` keys, GitHub's `ghp_`, etc.)
that makes a leaked key immediately recognizable as "an Agent
Intelligence API key" in logs/secret scanners, at no cost to entropy
(the prefix isn't counted as part of the secret).

--- Hashing scheme --------------------------------------------------------

Plain SHA-256 over the full token string (prefix included), hex-encoded.
Not a slow/salted password hash (bcrypt/scrypt/argon2): those exist to
slow down offline brute-forcing of *low-entropy, human-chosen* secrets
(passwords). This token has ~256 bits of CSPRNG entropy already --
brute-forcing the hash back to the token is infeasible regardless of
hash speed, so a fast general-purpose digest is the right tool (and
lets auth do a plain indexed equality lookup instead of needing to
iterate + verify against every stored hash the way bcrypt's per-hash
salt would require). This mirrors how most API-key-based systems
(Stripe, GitHub PATs, AWS access keys) hash their own high-entropy
tokens.
"""

from __future__ import annotations

import hashlib
import secrets

# The header api-service's auth dependency reads the key from, and the
# one clients are told to send it in. Documented here (not just in
# api-service) since billing-service's retrieval endpoint tells
# customers to use this exact header.
API_KEY_HEADER = "X-API-Key"

API_KEY_PREFIX = "ai_live_"

# secrets.token_urlsafe(32) -- 32 raw bytes (256 bits) of CSPRNG
# entropy, base64url-encoded to ~43 characters. Comfortably clears the
# build plan's "32+ url-safe random chars" bar once the prefix is added.
_TOKEN_BYTES = 32


def generate_api_key_secret() -> str:
    """Generate a new opaque bearer secret, e.g.
    `ai_live_9f2m1qz...`. This is shown to the customer exactly once
    (see billing-service's checkout-webhook flow) -- callers must hash
    it (`hash_api_key_secret`) before persisting anything, never the
    raw return value.
    """
    return f"{API_KEY_PREFIX}{secrets.token_urlsafe(_TOKEN_BYTES)}"


def hash_api_key_secret(secret: str) -> str:
    """Return the SHA-256 hex digest of `secret` -- the only form of an
    API key ever persisted to `api_keys.key_hash` or looked up against.
    """
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


__all__ = ["API_KEY_HEADER", "API_KEY_PREFIX", "generate_api_key_secret", "hash_api_key_secret"]
