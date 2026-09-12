from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from parser.webbotauth import parse_web_bot_auth

NOW = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)


def _ts(dt: datetime) -> int:
    return int(dt.timestamp())


def test_valid_non_expired_key():
    exp = NOW + timedelta(days=30)
    payload = {"keys": [{"kty": "OKP", "kid": "key-1", "exp": _ts(exp)}]}

    result = parse_web_bot_auth(json.dumps(payload).encode("utf-8"), now=NOW)

    assert result.malformed is False
    assert result.key_id == "key-1"
    assert result.valid is True
    assert result.expiry == exp


def test_expired_key():
    exp = NOW - timedelta(days=1)
    payload = {"keys": [{"kty": "OKP", "kid": "key-1", "exp": _ts(exp)}]}

    result = parse_web_bot_auth(json.dumps(payload).encode("utf-8"), now=NOW)

    assert result.malformed is False
    assert result.key_id == "key-1"
    assert result.valid is False
    assert result.expiry == exp


def test_multiple_keys_selects_latest_exp():
    older = NOW + timedelta(days=1)
    newer = NOW + timedelta(days=60)
    payload = {
        "keys": [
            {"kid": "key-old", "exp": _ts(older)},
            {"kid": "key-new", "exp": _ts(newer)},
        ]
    }

    result = parse_web_bot_auth(json.dumps(payload).encode("utf-8"), now=NOW)

    assert result.key_id == "key-new"
    assert result.expiry == newer
    assert result.valid is True


def test_key_missing_exp_is_not_valid_but_not_malformed():
    payload = {"keys": [{"kid": "key-1"}]}

    result = parse_web_bot_auth(json.dumps(payload).encode("utf-8"), now=NOW)

    assert result.malformed is False
    assert result.key_id == "key-1"
    assert result.expiry is None
    assert result.valid is False


def test_invalid_json_is_malformed():
    result = parse_web_bot_auth(b"{not valid json", now=NOW)

    assert result.malformed is True
    assert result.key_id is None
    assert result.valid is False
    assert "not valid JSON" in result.malformed_reason


def test_missing_keys_array_is_malformed():
    result = parse_web_bot_auth(json.dumps({"not_keys": []}).encode("utf-8"), now=NOW)

    assert result.malformed is True
    assert "keys" in result.malformed_reason


def test_bare_single_jwk_object_is_malformed_with_the_shopify_reason():
    # Real testing against Shopify's actual live Web Bot Auth directory
    # found exactly this: a bare single JWK, no top-level "keys" array
    # at all (unlike Cloudflare's own documented JWKS example format).
    # This is the single most important case in this file -- it's a
    # verified real-world finding, not a hypothetical edge case.
    bare_jwk = {"kty": "OKP", "crv": "Ed25519", "kid": "some-key", "exp": _ts(NOW + timedelta(days=30))}

    result = parse_web_bot_auth(json.dumps(bare_jwk).encode("utf-8"), now=NOW)

    assert result.malformed is True
    assert result.key_id is None
    assert result.valid is False
    assert "keys" in result.malformed_reason


def test_empty_keys_array_is_malformed():
    result = parse_web_bot_auth(json.dumps({"keys": []}).encode("utf-8"), now=NOW)

    assert result.malformed is True
    assert "keys" in result.malformed_reason


def test_keys_array_with_no_dict_entries_is_malformed():
    result = parse_web_bot_auth(json.dumps({"keys": ["not-a-key-object", 42]}).encode("utf-8"), now=NOW)

    assert result.malformed is True
    assert result.malformed_reason == 'the "keys" array contains no valid key objects'


def test_absent_signal_is_not_malformed():
    result = parse_web_bot_auth(None, now=NOW)

    assert result.malformed is False
    assert result.valid is False
    assert result.key_id is None
    assert result.malformed_reason is None
