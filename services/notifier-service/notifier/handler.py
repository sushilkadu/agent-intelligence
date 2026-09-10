"""Lambda entrypoint: notify-queue (SQS) -> look up monitors for the
changed domain -> POST a webhook notification to each, with retry/
backoff and a delivery-time SSRF re-check.

Mirrors crawler-service's/parser-service's `handler.py` in shape: a
thin sync `lambda_handler` wired up as the target of an SQS event
source mapping on `notify-queue`, one message per `record-changed`
event (see parser-service's `parser/messaging.py` for that message's
exact shape).

Invoked directly (not through real Lambda) for local verification --
see the pattern established by crawler-service's/parser-service's
`scripts/verify_localstack_e2e.py`.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from shared_utils import get_logger

from .db import fetch_monitors_for_domain, get_connection
from .webhook import build_notification_payload, deliver_webhook, validate_before_delivery

logger = get_logger("notifier-service")


def get_http_client() -> httpx.Client:
    """Factory for the outbound webhook-delivery HTTP client, mirroring
    every other service's `get_s3_client`/`get_sqs_client`
    factory-function convention (see crawler/parser-service's
    `storage.py`/`messaging.py`) so it's a single, easily
    monkeypatchable seam for tests and local verification scripts --
    e.g. a verification run against a local mock HTTPS server with a
    self-signed certificate needs a client constructed with
    `verify=False`, which this factory is the one place to swap in.
    """
    return httpx.Client()


def extract_message(record: dict[str, Any]) -> dict[str, Any] | None:
    """Pull the record-changed payload out of one SQS record. Returns
    None (rather than raising) for a malformed record -- one bad
    message must not take down the whole batch, same principle as
    crawler/parser-service's own `extract_*` functions.
    """
    body = record.get("body", record.get("Body", ""))
    if not isinstance(body, str) or not body.strip():
        return None

    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return None

    if isinstance(parsed, dict) and parsed.get("domain"):
        return parsed

    return None


def process_message(message: dict[str, Any], *, conn, http_client: httpx.Client) -> dict[str, Any]:
    """Process one record-changed message: look up every monitor
    watching this domain, and attempt delivery to each independently --
    one monitor's bad/unsafe webhook_url or delivery failure must never
    stop delivery to the domain's other monitors.
    """
    domain = str(message["domain"])
    monitors = fetch_monitors_for_domain(conn, domain)

    results = []
    for monitor in monitors:
        monitor_id = str(monitor["monitor_id"])
        webhook_url = monitor["webhook_url"]

        unsafe_reason = validate_before_delivery(webhook_url)
        if unsafe_reason is not None:
            logger.warning(
                "monitor %s for domain %s has an unsafe webhook_url, skipping delivery: %s",
                monitor_id,
                domain,
                unsafe_reason,
            )
            results.append({"monitor_id": monitor_id, "delivered": False, "reason": "unsafe_webhook_url"})
            continue

        payload = build_notification_payload(message, monitor)
        delivered, attempts = deliver_webhook(http_client, webhook_url, payload)
        if delivered:
            logger.info("delivered record-changed for %s to monitor %s in %d attempt(s)", domain, monitor_id, attempts)
        else:
            logger.warning(
                "failed to deliver record-changed for %s to monitor %s after %d attempt(s) -- "
                "leaving for notify-queue's own redrive/DLQ",
                domain,
                monitor_id,
                attempts,
            )
        results.append({"monitor_id": monitor_id, "delivered": delivered, "attempts": attempts})

    return {"domain": domain, "monitors_notified": len(monitors), "results": results}


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict:
    """SQS-triggered Lambda entrypoint. `event["Records"]` is the
    standard SQS event source mapping shape; each record's `body` is
    one record-changed message (see `extract_message`).
    """
    records = event.get("Records", [])
    messages = [m for m in (extract_message(r) for r in records) if m]

    if not messages:
        logger.info("lambda_handler invoked with no usable messages (records=%d)", len(records))
        return {"processed": 0, "domains": []}

    conn = get_connection()
    http_client = get_http_client()
    processed_domains: list[str] = []
    try:
        for message in messages:
            domain = message.get("domain", "<unknown>")
            try:
                process_message(message, conn=conn, http_client=http_client)
                processed_domains.append(domain)
            except Exception:
                # One domain's bad data (DB hiccup, malformed monitor
                # row, etc.) must not take down the rest of the batch --
                # same principle as crawler/parser-service's per-item
                # error handling. The SQS message itself is still
                # considered "processed" from this Lambda's point of
                # view only if it doesn't raise past this point; an
                # unhandled exception here would let the message become
                # visible again for a future redelivery via
                # notify-queue's normal visibility-timeout mechanics.
                logger.exception("failed to process record-changed message for domain=%s", domain)
    finally:
        conn.close()
        http_client.close()

    return {"processed": len(processed_domains), "domains": processed_domains}


__all__ = ["extract_message", "get_http_client", "lambda_handler", "process_message"]
