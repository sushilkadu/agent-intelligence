"""Webhook payload construction + delivery with bounded retry/backoff,
and the delivery-time SSRF re-check.

--- Why re-validate the URL here, not just trust api-service's check ---

`shared_utils.webhook_safety.validate_webhook_url` already runs once at
`POST /v1/monitors` registration time (api-service). Running it again
here, immediately before every delivery attempt, is defense-in-depth
against two things:

  1. The registration-time check being bypassed somehow (a bug, a
     future code path that writes `monitors` some other way, a
     manually-inserted row) -- notifier-service should never blindly
     trust that every row in the table is safe just because ONE known
     code path validates on the way in.
  2. DNS rebinding: a hostname that resolved safely at registration
     time can be repointed at a private/metadata address by its owner
     any time after that -- registration-time validation cannot see
     into the future. Re-resolving and re-checking immediately before
     every connection attempt is the strongest mitigation available
     without a much larger rework (see
     `shared_utils/webhook_safety.py`'s module docstring for the full
     discussion of what this does and doesn't close).
"""

from __future__ import annotations

import time
from typing import Any, Callable

import httpx
from shared_utils import UnsafeWebhookURLError, validate_webhook_url

from .config import WEBHOOK_BACKOFF_SECONDS, WEBHOOK_MAX_ATTEMPTS, WEBHOOK_TIMEOUT_SECONDS


def build_notification_payload(message: dict[str, Any], monitor: dict[str, Any]) -> dict[str, Any]:
    """Build the JSON body POSTed to a monitor's webhook_url.

    Includes at minimum what the build plan asks for -- the domain,
    what changed, and when -- plus which monitor this delivery is for,
    so a customer with multiple monitors can tell them apart without
    re-deriving it from the URL they registered.
    """
    return {
        "event": message.get("event", "record-changed"),
        "domain": message["domain"],
        "crawled_at": message.get("crawled_at"),
        "changed_fields": message.get("changed_fields", {}),
        "monitor_id": str(monitor["monitor_id"]),
    }


def deliver_webhook(
    http_client: httpx.Client,
    url: str,
    payload: dict[str, Any],
    *,
    max_attempts: int = WEBHOOK_MAX_ATTEMPTS,
    backoff_seconds: list[float] = WEBHOOK_BACKOFF_SECONDS,
    timeout: float = WEBHOOK_TIMEOUT_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[bool, int]:
    """POST `payload` to `url`, retrying on connection error, timeout,
    or a non-2xx response, with a short backoff between attempts.
    Returns `(delivered, attempts_made)`.

    Bounded on purpose (see config.py's docstring on `WEBHOOK_MAX_ATTEMPTS`/
    `WEBHOOK_BACKOFF_SECONDS`): this all runs inside one Lambda
    invocation's timeout, so retrying "a few times with short backoff"
    is the right shape here, not anything open-ended -- a webhook that
    exhausts every attempt here is left for notify-queue's own
    redrive/DLQ to eventually give up on (see config.py's docstring),
    not retried indefinitely in-process.
    """
    last_status: int | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = http_client.post(url, json=payload, timeout=timeout)
            last_status = response.status_code
            if 200 <= response.status_code < 300:
                return True, attempt
        except httpx.RequestError:
            pass

        if attempt < max_attempts:
            delay_index = min(attempt - 1, len(backoff_seconds) - 1)
            if delay_index >= 0:
                sleep(backoff_seconds[delay_index])

    del last_status  # kept for potential future logging; not returned today
    return False, max_attempts


def validate_before_delivery(url: str) -> str | None:
    """Re-run the SSRF safety check immediately before connecting (see
    module docstring). Returns None if `url` is safe, or a human-readable
    reason string if it is not -- never raises, so a caller can log and
    skip this one monitor without the exception propagating and
    aborting the rest of the batch.
    """
    try:
        validate_webhook_url(url)
    except UnsafeWebhookURLError as exc:
        return str(exc)
    return None


__all__ = ["build_notification_payload", "deliver_webhook", "validate_before_delivery"]
