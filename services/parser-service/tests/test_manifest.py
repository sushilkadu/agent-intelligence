from __future__ import annotations

import json

from parser.manifest import parse_agents_json


def test_valid_json_object_is_parsed():
    payload = {"agents": [{"name": "demo-bot", "capabilities": ["chat"]}]}
    result = parse_agents_json(json.dumps(payload).encode("utf-8"))

    assert result.malformed is False
    assert result.declared_capabilities == payload
    assert result.malformed_reason is None


def test_invalid_json_is_malformed():
    result = parse_agents_json(b"{not valid json")

    assert result.malformed is True
    assert result.declared_capabilities == {}
    assert result.malformed_reason is not None
    assert "not valid JSON" in result.malformed_reason


def test_non_utf8_bytes_are_malformed():
    result = parse_agents_json(b"\xff\xfe\x00\x01")

    assert result.malformed is True
    assert result.declared_capabilities == {}
    assert result.malformed_reason == "the response body isn't valid UTF-8 text"


def test_valid_json_that_is_not_an_object_is_malformed():
    # A bare JSON list is syntactically valid JSON but every agents.json
    # proposal describes the manifest as a top-level object -- a list
    # (or string/number) can't be treated as one.
    result = parse_agents_json(json.dumps(["not", "an", "object"]).encode("utf-8"))

    assert result.malformed is True
    assert result.declared_capabilities == {}
    assert result.malformed_reason == "the top-level value is a JSON array, not a JSON object"


def test_valid_json_string_is_malformed_with_a_specific_reason():
    result = parse_agents_json(json.dumps("just a string").encode("utf-8"))

    assert result.malformed is True
    assert result.malformed_reason == "the top-level value is a JSON string, not a JSON object"


def test_absent_signal_is_not_malformed():
    result = parse_agents_json(None)

    assert result.malformed is False
    assert result.declared_capabilities == {}
    assert result.malformed_reason is None
