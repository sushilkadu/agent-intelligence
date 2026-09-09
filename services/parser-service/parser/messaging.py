"""Change detection + publishing the `record-changed` event to
`notify-queue`.

`notify-queue` is a new queue introduced in this phase (Terraform:
infra/terraform/modules/sqs via envs/dev/main.tf's
`module.notify_queue`) -- notifier-service (Phase 5) will be its
consumer; this module only ever publishes to it, never built here.

Message shape (documented here since notifier-service needs to agree
on it later):

    {
        "event": "record-changed",
        "domain": "example.com",
        "crawled_at": "2026-09-09T12:00:00+00:00",
        "changed_fields": {
            "web_bot_auth_valid": {"old": true, "new": false},
            "confidence_flags": {"old": [], "new": ["expired_key"]}
        }
    }

`changed_fields` carries only the fields that actually differ (not the
full before/after record) so a downstream consumer building a
human-readable notification ("web_bot_auth_valid changed from true to
false") doesn't have to diff the record itself. Kept intentionally
small/obvious rather than mirroring some richer event-envelope
convention (event id, schema version, etc.) -- there's exactly one
consumer type planned and no other event on this queue yet; add that
structure when a second event type or consumer needs it, not
speculatively now.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import boto3

from .config import boto3_client_kwargs
from .normalize import DIFF_FIELDS


def get_sqs_client():
    return boto3.client("sqs", **boto3_client_kwargs())


def diff_records(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Compare `previous` (the prior DB row, or None for a first-ever
    crawl) against `current` (this crawl's freshly built record) over
    `DIFF_FIELDS` only -- bookkeeping timestamps
    (first_seen_at/last_crawled_at/created_at/updated_at) are excluded
    on purpose, see normalize.py.

    A first-ever crawl (`previous is None`) is never reported as a
    change -- there's nothing to notify a subscriber about "this domain
    changed" when it's the first time it's ever been seen; that's a new
    record, not a change to an existing one.
    """
    if previous is None:
        return {}

    changed: dict[str, dict[str, Any]] = {}
    for field_name in DIFF_FIELDS:
        old_value = previous.get(field_name)
        new_value = current.get(field_name)
        if old_value != new_value:
            changed[field_name] = {"old": old_value, "new": new_value}
    return changed


def build_record_changed_message(
    *, domain: str, crawled_at: datetime, changed_fields: dict[str, dict[str, Any]]
) -> dict:
    return {
        "event": "record-changed",
        "domain": domain,
        "crawled_at": crawled_at.isoformat() if isinstance(crawled_at, datetime) else crawled_at,
        "changed_fields": changed_fields,
    }


def publish_record_changed(sqs_client, queue_url: str, message: dict) -> str:
    response = sqs_client.send_message(QueueUrl=queue_url, MessageBody=json.dumps(message, default=str))
    return response["MessageId"]


__all__ = [
    "build_record_changed_message",
    "diff_records",
    "get_sqs_client",
    "publish_record_changed",
]
