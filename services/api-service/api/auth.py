"""API-key auth for api-service.

--- Design: header-optional, key-aware ------------------------------------

Every route in this service can be called two ways:

  1. No `X-API-Key` header at all -- the existing Phase 3 unauthenticated
     free-tier path. Must keep working byte-for-byte as before (see
     `api/routes.py`'s `enforce_rate_limit`): IP-keyed rate limiting,
     no auth failure of any kind.
  2. An `X-API-Key` header present -- it must hash to an `active` row
     in `api_keys`, or this is a clean 401 (`invalid_api_key`), never a
     silent fallback to free-tier and never a 500. A present-but-wrong
     key is a caller error worth telling them about, not something to
     paper over.

This module implements exactly that two-outcome-plus-error shape as one
small pure function (`authenticate`), independent of FastAPI's request
object so it's trivial to unit test. `api/routes.py` wraps it as a
FastAPI dependency that supplies the header value + a DB connection.

--- Why 401 here but 403 on the bulk endpoint -----------------------------

`authenticate` raises 401 for "key given but invalid" because that's an
authentication failure -- the caller claims an identity (the key) and
it doesn't check out. The bulk endpoint's *separate* dependency
(`api/routes.py`'s `require_bulk_api_key`) treats "no key", "invalid
key", AND "valid free-tier key" as one undifferentiated 403 -- that's
an authorization failure (this endpoint requires a paid plan), not an
authentication one, and the build plan explicitly asks for all three
cases to look the same (403) rather than leaking which case applies.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException
from shared_utils import hash_api_key_secret

from .db import fetch_api_key_by_hash


@dataclass(frozen=True)
class ApiKeyContext:
    """The authenticated identity for one request, once a valid, active
    API key has been resolved.
    """

    key_id: str
    plan_tier: str
    rate_limit: int


def _invalid_api_key_error() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={
            "error": "invalid_api_key",
            "message": "The API key in the X-API-Key header is invalid, inactive, or unrecognized.",
        },
    )


def lookup_api_key_context(conn, header_value: str | None) -> ApiKeyContext | None:
    """Resolve `header_value` (the raw `X-API-Key` header, or None if
    absent) to an `ApiKeyContext`.

    Returns None for both "no header supplied" AND "header supplied but
    doesn't match any active key" -- callers that need to distinguish
    those two (i.e. anything that owes the caller a 401 for the latter)
    should use `authenticate` instead, which raises for the latter case.
    This split exists because the bulk endpoint's 403-for-everything
    semantics (see module docstring) needs the non-raising form.
    """
    if not header_value:
        return None
    key_hash = hash_api_key_secret(header_value)
    row = fetch_api_key_by_hash(conn, key_hash)
    if row is None or not row.get("active", False):
        return None
    return ApiKeyContext(key_id=row["key_id"], plan_tier=row["plan_tier"], rate_limit=row["rate_limit"])


def authenticate(conn, header_value: str | None) -> ApiKeyContext | None:
    """Resolve `header_value` to an `ApiKeyContext`, or None if no key
    was supplied at all (the free-tier path). Raises a 401
    `HTTPException` if a key WAS supplied but doesn't match any active
    row -- see module docstring for why this differs from the bulk
    endpoint's 403 treatment.
    """
    if not header_value:
        return None
    context = lookup_api_key_context(conn, header_value)
    if context is None:
        raise _invalid_api_key_error()
    return context


__all__ = ["ApiKeyContext", "authenticate", "lookup_api_key_context"]
