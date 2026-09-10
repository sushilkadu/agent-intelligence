"""Unit tests for `api/auth.py`'s pure key-resolution logic -- no
FastAPI, no real DB, no real AWS. `fetch_api_key_by_hash` (the one
thing that would touch Postgres) is monkeypatched directly, mirroring
how `tests/test_domains.py` fakes `fetch_domain`.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from shared_utils import hash_api_key_secret

from api import auth


def _row(key_id: str = "key-1", plan_tier: str = "self_serve", rate_limit: int = 1000, active: bool = True) -> dict:
    return {"key_id": key_id, "plan_tier": plan_tier, "rate_limit": rate_limit, "active": active}


def test_authenticate_returns_none_when_no_header_at_all():
    assert auth.authenticate(conn=None, header_value=None) is None
    assert auth.authenticate(conn=None, header_value="") is None


def test_authenticate_returns_context_for_a_valid_active_key(monkeypatch):
    monkeypatch.setattr(auth, "fetch_api_key_by_hash", lambda _conn, _key_hash: _row())

    context = auth.authenticate(conn=None, header_value="ai_live_whatever")

    assert context is not None
    assert context.key_id == "key-1"
    assert context.plan_tier == "self_serve"
    assert context.rate_limit == 1000


def test_authenticate_raises_clean_401_for_an_unrecognized_key(monkeypatch):
    monkeypatch.setattr(auth, "fetch_api_key_by_hash", lambda _conn, _key_hash: None)

    with pytest.raises(HTTPException) as exc_info:
        auth.authenticate(conn=None, header_value="ai_live_not-a-real-key")

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"] == "invalid_api_key"


def test_authenticate_raises_clean_401_for_an_inactive_key(monkeypatch):
    """A key that matches by hash but has been deactivated (e.g. a
    canceled Stripe subscription -- see billing-service's webhook
    handler) must fail auth exactly like an unrecognized key, not be
    silently treated as valid.
    """
    monkeypatch.setattr(auth, "fetch_api_key_by_hash", lambda _conn, _key_hash: _row(active=False))

    with pytest.raises(HTTPException) as exc_info:
        auth.authenticate(conn=None, header_value="ai_live_deactivated")

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"] == "invalid_api_key"


def test_lookup_api_key_context_never_raises_for_a_missing_or_invalid_key(monkeypatch):
    """The non-raising form used by the bulk endpoint's 403-for-everything
    dependency (see api/routes.py's `require_bulk_api_key`) -- it must
    return None, not raise, for both "no header" and "header present
    but no match."
    """
    assert auth.lookup_api_key_context(conn=None, header_value=None) is None

    monkeypatch.setattr(auth, "fetch_api_key_by_hash", lambda _conn, _key_hash: None)
    assert auth.lookup_api_key_context(conn=None, header_value="ai_live_bad") is None


def test_lookup_is_keyed_by_the_sha256_hash_of_the_header_value_not_the_raw_value(monkeypatch):
    seen = {}

    def _fake_fetch(_conn, key_hash):
        seen["key_hash"] = key_hash

    monkeypatch.setattr(auth, "fetch_api_key_by_hash", _fake_fetch)

    auth.lookup_api_key_context(conn=None, header_value="ai_live_abc123")

    assert seen["key_hash"] == hash_api_key_secret("ai_live_abc123")
    assert seen["key_hash"] != "ai_live_abc123"
