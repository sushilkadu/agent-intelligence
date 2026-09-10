"""End-to-end (within-process) tests of notifier-service's Lambda
handler: a hand-built record-changed-shaped SQS event goes in, monitors
are looked up from a faked DB layer (mirrors parser-service's
tests/fakes.py-style DB faking), and webhook delivery is exercised
against a real local mock HTTP server / respx-mocked HTTP -- never a
real network call.
"""

from __future__ import annotations

import json

import httpx
import respx

from notifier import handler


class _FakeConnection:
    def close(self) -> None:
        pass


def _event(domain: str, *, changed_fields: dict | None = None) -> dict:
    message = {
        "event": "record-changed",
        "domain": domain,
        "crawled_at": "2026-09-10T12:00:00+00:00",
        "changed_fields": changed_fields or {"web_bot_auth_valid": {"old": True, "new": False}},
    }
    return {"Records": [{"body": json.dumps(message)}]}


def _monitor(monitor_id: str, domain: str, webhook_url: str) -> dict:
    return {"monitor_id": monitor_id, "domain": domain, "webhook_url": webhook_url, "owner_key_id": "key-1"}


def test_extract_message_from_valid_body():
    record = {"body": json.dumps({"domain": "example.com", "event": "record-changed"})}
    assert handler.extract_message(record) == {"domain": "example.com", "event": "record-changed"}


def test_extract_message_returns_none_for_invalid_body():
    assert handler.extract_message({"body": "not json"}) is None
    assert handler.extract_message({"body": ""}) is None
    assert handler.extract_message({}) is None
    assert handler.extract_message({"body": json.dumps({"no_domain": True})}) is None


@respx.mock
def test_lambda_handler_delivers_to_every_monitor_watching_the_domain(monkeypatch):
    hook_a = "https://hooks-a.example.com/callback"
    hook_b = "https://hooks-b.example.com/callback"
    respx.post(hook_a).mock(return_value=httpx.Response(200))
    respx.post(hook_b).mock(return_value=httpx.Response(200))

    monitors = [
        _monitor("11111111-1111-1111-1111-111111111111", "example.com", hook_a),
        _monitor("22222222-2222-2222-2222-222222222222", "example.com", hook_b),
    ]
    monkeypatch.setattr(handler, "get_connection", lambda: _FakeConnection())
    monkeypatch.setattr(handler, "fetch_monitors_for_domain", lambda _conn, _domain: monitors)

    result = handler.lambda_handler(_event("example.com"), None)

    assert result == {"processed": 1, "domains": ["example.com"]}
    assert respx.calls.call_count == 2
    for call in respx.calls:
        body = json.loads(call.request.content)
        assert body["domain"] == "example.com"
        assert body["changed_fields"] == {"web_bot_auth_valid": {"old": True, "new": False}}


@respx.mock
def test_lambda_handler_with_no_monitors_for_the_domain_is_a_noop_delivery(monkeypatch):
    monkeypatch.setattr(handler, "get_connection", lambda: _FakeConnection())
    monkeypatch.setattr(handler, "fetch_monitors_for_domain", lambda _conn, _domain: [])

    result = handler.lambda_handler(_event("nobody-watching.example"), None)

    assert result == {"processed": 1, "domains": ["nobody-watching.example"]}
    assert respx.calls.call_count == 0


@respx.mock
def test_lambda_handler_skips_delivery_for_an_unsafe_webhook_url_but_still_notifies_others(monkeypatch):
    safe_hook = "https://hooks-safe.example.com/callback"
    respx.post(safe_hook).mock(return_value=httpx.Response(200))

    monitors = [
        _monitor("11111111-1111-1111-1111-111111111111", "example.com", "https://169.254.169.254/latest/meta-data/"),
        _monitor("22222222-2222-2222-2222-222222222222", "example.com", safe_hook),
    ]
    monkeypatch.setattr(handler, "get_connection", lambda: _FakeConnection())
    monkeypatch.setattr(handler, "fetch_monitors_for_domain", lambda _conn, _domain: monitors)

    result = handler.lambda_handler(_event("example.com"), None)

    assert result == {"processed": 1, "domains": ["example.com"]}
    # Only the safe monitor's webhook was actually called -- the unsafe
    # one (a metadata-endpoint URL that should never have been
    # registered, or a rebound hostname since) is never connected to.
    assert respx.calls.call_count == 1
    assert str(respx.calls[0].request.url) == safe_hook


@respx.mock
def test_lambda_handler_one_domains_failure_does_not_block_the_rest_of_the_batch(monkeypatch):
    good_hook = "https://hooks-good.example.com/callback"
    respx.post(good_hook).mock(return_value=httpx.Response(200))

    def _fetch_monitors(_conn, domain):
        if domain == "boom.example":
            raise RuntimeError("simulated DB failure")
        return [_monitor("11111111-1111-1111-1111-111111111111", domain, good_hook)]

    monkeypatch.setattr(handler, "get_connection", lambda: _FakeConnection())
    monkeypatch.setattr(handler, "fetch_monitors_for_domain", _fetch_monitors)

    event = {
        "Records": [
            {"body": json.dumps({"event": "record-changed", "domain": "boom.example", "changed_fields": {}})},
            {"body": json.dumps({"event": "record-changed", "domain": "fine.example", "changed_fields": {}})},
        ]
    }

    result = handler.lambda_handler(event, None)

    assert result == {"processed": 1, "domains": ["fine.example"]}
    assert respx.calls.call_count == 1


def test_lambda_handler_with_no_records_is_a_noop():
    result = handler.lambda_handler({"Records": []}, None)
    assert result == {"processed": 0, "domains": []}
