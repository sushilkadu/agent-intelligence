"""Lambda entrypoint: crawl-queue (SQS) -> crawl -> S3 -> raw-fetched (SQS).

In production this module's `lambda_handler` is wired up as the target
of an SQS event source mapping on `crawl-queue`, one message per
domain. Lambda handlers are invoked synchronously, so `lambda_handler`
itself is a thin sync wrapper that drives the async crawl logic via
`asyncio.run`.

This is invoked directly (not through real Lambda) for local
verification -- see scripts/verify_localstack_e2e.py, which builds a
hand-crafted SQS-event-shaped dict and calls `lambda_handler(event,
None)` exactly as the Lambda runtime would.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import httpx
from shared_utils import get_logger

from .config import RAW_DATA_BUCKET_NAME, RAW_FETCHED_QUEUE_URL
from .fetch import crawl_domain
from .messaging import build_raw_fetched_message, get_sqs_client, publish_raw_fetched
from .storage import get_s3_client, store_fetch_result

logger = get_logger("crawler-service")


def extract_domain(record: dict[str, Any]) -> str | None:
    """Pull a domain out of one SQS record.

    Accepts either a bare-string message body (`"example.com"`) or a
    JSON object body (`{"domain": "example.com"}`, the shape produced
    by `scripts/load_seed_list.py`). Returns None (rather than
    raising) for a record that has no usable domain, so one malformed
    message can't take down the whole batch.
    """
    body = record.get("body", record.get("Body", ""))
    if not isinstance(body, str) or not body.strip():
        return None

    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        parsed = None

    if isinstance(parsed, dict) and parsed.get("domain"):
        return str(parsed["domain"]).strip()

    stripped = body.strip()
    return stripped or None


async def process_domain(
    domain: str,
    *,
    s3_client,
    sqs_client,
    bucket: str,
    raw_fetched_queue_url: str,
    http_client: httpx.AsyncClient | None = None,
) -> dict:
    """Crawl one domain end-to-end: fetch both signals, store each in
    S3, publish one `raw-fetched` message. Returns the published
    message for convenience (e.g. tests, the verification script).
    """
    result = await crawl_domain(domain, client=http_client)
    crawled_at = datetime.now(timezone.utc).isoformat()

    agents_json_key = store_fetch_result(
        s3_client, bucket, domain, crawled_at, "agents.json", result.agents_json
    )
    web_bot_auth_key = store_fetch_result(
        s3_client, bucket, domain, crawled_at, "web-bot-auth-directory", result.web_bot_auth
    )

    message = build_raw_fetched_message(
        domain=domain,
        crawled_at=crawled_at,
        agents_json_present=result.agents_json.present,
        agents_json_status_code=result.agents_json.status_code,
        agents_json_s3_key=agents_json_key,
        web_bot_auth_present=result.web_bot_auth.present,
        web_bot_auth_status_code=result.web_bot_auth.status_code,
        web_bot_auth_s3_key=web_bot_auth_key,
    )
    message_id = publish_raw_fetched(sqs_client, raw_fetched_queue_url, message)
    logger.info(
        "crawled %s: agents.json present=%s web_bot_auth present=%s -> raw-fetched message %s",
        domain,
        result.agents_json.present,
        result.web_bot_auth.present,
        message_id,
    )
    return message


async def _process_domains(domains: list[str], *, bucket: str, raw_fetched_queue_url: str) -> list[dict]:
    s3_client = get_s3_client()
    sqs_client = get_sqs_client()
    async with httpx.AsyncClient(follow_redirects=True) as http_client:
        # The HTTP fetches run concurrently; the S3/SQS calls per
        # domain are synchronous boto3 calls (fine at crawl-queue's
        # batch scale -- Phase 1 doesn't need a fully async AWS
        # client for this).
        tasks = [
            process_domain(
                domain,
                s3_client=s3_client,
                sqs_client=sqs_client,
                bucket=bucket,
                raw_fetched_queue_url=raw_fetched_queue_url,
                http_client=http_client,
            )
            for domain in domains
        ]
        return await asyncio.gather(*tasks)


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict:
    """SQS-triggered Lambda entrypoint. `event["Records"]` is the
    standard SQS event source mapping shape; each record's `body` is
    expected to name one domain (see `extract_domain`).
    """
    records = event.get("Records", [])
    domains = [d for d in (extract_domain(r) for r in records) if d]

    if not domains:
        logger.info("lambda_handler invoked with no usable domains (records=%d)", len(records))
        return {"processed": 0, "domains": []}

    asyncio.run(
        _process_domains(
            domains,
            bucket=RAW_DATA_BUCKET_NAME,
            raw_fetched_queue_url=RAW_FETCHED_QUEUE_URL,
        )
    )
    return {"processed": len(domains), "domains": domains}
