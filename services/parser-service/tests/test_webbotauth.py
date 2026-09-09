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


def test_missing_keys_array_is_malformed():
    result = parse_web_bot_auth(json.dumps({"not_keys": []}).encode("utf-8"), now=NOW)

    assert result.malformed is True


def test_empty_keys_array_is_malformed():
    result = parse_web_bot_auth(json.dumps({"keys": []}).encode("utf-8"), now=NOW)

    assert result.malformed is True


def test_absent_signal_is_not_malformed():
    result = parse_web_bot_auth(None, now=NOW)

    assert result.malformed is False
    assert result.valid is False
    assert result.key_id is None
