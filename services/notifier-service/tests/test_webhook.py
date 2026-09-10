"""Unit tests for notifier.webhook: payload shape, retry/backoff on
failure, and the delivery-time SSRF re-check. All HTTP mocked via
respx -- no real network.
"""

from __future__ import annotations

import httpx
import respx

from notifier.webhook import build_notification_payload, deliver_webhook, validate_before_delivery

URL = "https://hooks.example.com/callback"


def _message(**overrides) -> dict:
    message = {
        "event": "record-changed",
        "domain": "example.com",
        "crawled_at": "2026-09-10T12:00:00+00:00",
        "changed_fields": {"web_bot_auth_valid": {"old": True, "new": False}},
    }
    message.update(overrides)
    return message


def _monitor(**overrides) -> dict:
    monitor = {"monitor_id": "11111111-1111-1111-1111-111111111111", "domain": "example.com", "webhook_url": URL}
    monitor.update(overrides)
    return monitor


# --- payload shape -----------------------------------------------------------


def test_build_notification_payload_includes_domain_change_and_monitor_id():
    payload = build_notification_payload(_message(), _monitor())

    assert payload["domain"] == "example.com"
    assert payload["event"] == "record-changed"
    assert payload["changed_fields"] == {"web_bot_auth_valid": {"old": True, "new": False}}
    assert payload["monitor_id"] == "11111111-1111-1111-1111-111111111111"


# --- delivery: success, retries, exhaustion ----------------------------------


@respx.mock
def test_deliver_webhook_succeeds_on_first_attempt():
    respx.post(URL).mock(return_value=httpx.Response(200))

    with httpx.Client() as client:
        delivered, attempts = deliver_webhook(client, URL, {"x": 1}, sleep=lambda _s: None)

    assert delivered is True
    assert attempts == 1


@respx.mock
def test_deliver_webhook_retries_on_non_2xx_then_succeeds():
    respx.post(URL).mock(side_effect=[httpx.Response(500), httpx.Response(200)])

    slept = []
    with httpx.Client() as client:
        delivered, attempts = deliver_webhook(client, URL, {"x": 1}, sleep=slept.append)

    assert delivered is True
    assert attempts == 2
    assert slept == [1.0]  # the configured first backoff delay


@respx.mock
def test_deliver_webhook_retries_on_connection_error_then_succeeds():
    respx.post(URL).mock(side_effect=[httpx.ConnectError("refused"), httpx.Response(200)])

    with httpx.Client() as client:
        delivered, attempts = deliver_webhook(client, URL, {"x": 1}, sleep=lambda _s: None)

    assert delivered is True
    assert attempts == 2


@respx.mock
def test_deliver_webhook_gives_up_after_max_attempts():
    respx.post(URL).mock(return_value=httpx.Response(503))

    slept = []
    with httpx.Client() as client:
        delivered, attempts = deliver_webhook(client, URL, {"x": 1}, max_attempts=3, sleep=slept.append)

    assert delivered is False
    assert attempts == 3
    # Bounded retries: sleeps between attempts only, never after the last.
    assert len(slept) == 2


@respx.mock
def test_deliver_webhook_uses_a_bounded_number_of_short_backoff_delays():
    respx.post(URL).mock(return_value=httpx.Response(500))

    slept = []
    with httpx.Client() as client:
        deliver_webhook(client, URL, {"x": 1}, max_attempts=3, backoff_seconds=[1.0, 2.0], sleep=slept.append)

    assert slept == [1.0, 2.0]


# --- delivery-time SSRF re-check ----------------------------------------------


def test_validate_before_delivery_allows_a_safe_url(monkeypatch):
    import notifier.webhook as webhook_module

    monkeypatch.setattr(
        webhook_module,
        "validate_webhook_url",
        lambda _url: None,
    )

    assert validate_before_delivery("https://hooks.example.com/callback") is None


def test_validate_before_delivery_rejects_non_https_url():
    assert validate_before_delivery("http://hooks.example.com/callback") is not None


def test_validate_before_delivery_rejects_private_ip_url():
    reason = validate_before_delivery("https://10.0.0.5/callback")
    assert reason is not None
    assert "disallowed address" in reason


def test_validate_before_delivery_rejects_cloud_metadata_url():
    reason = validate_before_delivery("https://169.254.169.254/latest/meta-data/")
    assert reason is not None
