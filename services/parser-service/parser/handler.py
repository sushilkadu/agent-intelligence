"""Lambda entrypoint: raw-fetched (SQS) -> S3 read -> parse/validate ->
upsert `domains` -> notify-queue (SQS) on change.

Mirrors crawler-service's crawler/handler.py in shape: a thin sync
`lambda_handler` wired up as the target of an SQS event source mapping
on `raw-fetched`, one message per crawled domain (see
crawler/messaging.py's `build_raw_fetched_message` for that message's
exact shape).

Invoked directly (not through real Lambda) for local verification --
see scripts/verify_localstack_e2e.py.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from shared_utils import get_logger

from .confidence import compute_confidence_flags
from .config import NOTIFY_QUEUE_URL, RAW_DATA_BUCKET_NAME
from .db import fetch_domain, get_connection, upsert_domain
from .manifest import ParsedManifest, parse_agents_json
from .messaging import (
    build_record_changed_message,
    diff_records,
    get_sqs_client,
    publish_record_changed,
)
from .normalize import build_domain_record
from .storage import decode_envelope_body, get_s3_client, read_envelope
from .webbotauth import ParsedWebBotAuth, parse_web_bot_auth

logger = get_logger("parser-service")


def extract_message(record: dict[str, Any]) -> dict[str, Any] | None:
    """Pull the raw-fetched payload out of one SQS record.

    Returns None (rather than raising) for a record whose body isn't
    valid JSON, isn't an object, or has no `domain` -- one malformed
    message must not take down the whole batch, same principle as
    crawler-service's `extract_domain`.
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


def _parse_timestamp(value: Any, *, fallback: datetime) -> datetime:
    """Parse an ISO-8601 timestamp string (as produced by
    `datetime.isoformat()` in crawler-service). Falls back to
    `fallback` for a missing/unparseable value rather than raising --
    the crawl still happened and shouldn't be dropped over a timestamp
    format quirk.
    """
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return fallback


def process_message(
    message: dict[str, Any],
    *,
    s3_client,
    sqs_client,
    conn,
    bucket: str,
    notify_queue_url: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Process one raw-fetched message end to end: read whichever
    artifacts were present from S3, validate/normalize them, upsert the
    domain's row, and publish `record-changed` if anything meaningful
    changed. Returns the normalized record that was written.
    """
    now = now or datetime.now(timezone.utc)
    domain = str(message["domain"])
    crawled_at = _parse_timestamp(message.get("crawled_at"), fallback=now)

    agents_info = message.get("agents_json") or {}
    web_bot_auth_info = message.get("web_bot_auth") or {}

    agents_json_present = bool(agents_info.get("present"))
    agents_json_s3_key = agents_info.get("s3_key")
    web_bot_auth_present = bool(web_bot_auth_info.get("present"))
    web_bot_auth_s3_key = web_bot_auth_info.get("s3_key")

    manifest = ParsedManifest()
    if agents_json_present and agents_json_s3_key:
        envelope = read_envelope(s3_client, bucket, agents_json_s3_key)
        manifest = parse_agents_json(decode_envelope_body(envelope))

    webbotauth = ParsedWebBotAuth()
    if web_bot_auth_present and web_bot_auth_s3_key:
        envelope = read_envelope(s3_client, bucket, web_bot_auth_s3_key)
        webbotauth = parse_web_bot_auth(decode_envelope_body(envelope), now=now)

    flags = compute_confidence_flags(
        agents_json_present=agents_json_present,
        manifest_malformed=manifest.malformed,
        web_bot_auth_present=web_bot_auth_present,
        web_bot_auth_malformed=webbotauth.malformed,
        web_bot_auth_valid=webbotauth.valid,
    )

    previous = fetch_domain(conn, domain)

    record = build_domain_record(
        domain=domain,
        crawled_at=crawled_at,
        agents_json_present=agents_json_present,
        agents_json_s3_key=agents_json_s3_key,
        manifest=manifest,
        web_bot_auth_present=web_bot_auth_present,
        web_bot_auth_s3_key=web_bot_auth_s3_key,
        webbotauth=webbotauth,
        confidence_flags=flags,
        previous=previous,
        now=now,
    )

    changed_fields = diff_records(previous, record)

    upsert_domain(conn, record)

    if changed_fields:
        notify_message = build_record_changed_message(
            domain=domain, crawled_at=crawled_at, changed_fields=changed_fields
        )
        message_id = publish_record_changed(sqs_client, notify_queue_url, notify_message)
        logger.info(
            "domain %s changed (%s) -> record-changed message %s",
            domain,
            sorted(changed_fields),
            message_id,
        )
    else:
        logger.info("domain %s: no change from previous crawl, nothing published", domain)

    return record


def lambda_handler(event: dict[str, Any], context: Any = None, *, now: datetime | None = None) -> dict:
    """SQS-triggered Lambda entrypoint. `event["Records"]` is the
    standard SQS event source mapping shape; each record's `body` is
    one raw-fetched message (see `extract_message`).

    `now` is keyword-only and optional -- real Lambda invocations
    (called positionally with exactly `(event, context)`, the standard
    AWS calling convention) never pass it, so it defaults to the actual
    current time. Tests pass it explicitly for deterministic
    "is this key expired" checks instead of depending on wall-clock
    time at whatever moment the test happens to run.
    """
    records = event.get("Records", [])
    messages = [m for m in (extract_message(r) for r in records) if m]

    if not messages:
        logger.info("lambda_handler invoked with no usable messages (records=%d)", len(records))
        return {"processed": 0, "domains": []}

    s3_client = get_s3_client()
    sqs_client = get_sqs_client()
    conn = get_connection()
    processed_domains: list[str] = []
    try:
        for message in messages:
            domain = message.get("domain", "<unknown>")
            try:
                process_message(
                    message,
                    s3_client=s3_client,
                    sqs_client=sqs_client,
                    conn=conn,
                    bucket=RAW_DATA_BUCKET_NAME,
                    notify_queue_url=NOTIFY_QUEUE_URL,
                    now=now,
                )
                processed_domains.append(domain)
            except Exception:
                # One domain's bad data (unreadable S3 object, DB
                # hiccup, etc.) must not take down the rest of the
                # batch -- same principle as crawler-service's
                # per-domain fetch error handling.
                logger.exception("failed to process raw-fetched message for domain=%s", domain)
    finally:
        conn.close()

    return {"processed": len(processed_domains), "domains": processed_domains}
