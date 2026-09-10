"""Lambda entrypoint: EventBridge scheduled rule -> decide which domains
are due for recrawl (tiered cadence, see cadence.py) -> enqueue each
onto `crawl-queue`.

--- How often does THIS Lambda run? -----------------------------------

Terraform wires this up to an EventBridge scheduled rule running
HOURLY (see infra/terraform/envs/dev/main.tf's `scheduler_rule`) --
separate from, and much more frequent than, any individual domain's own
recrawl cadence (daily/every-3-days/weekly, see cadence.py). Hourly is
a judgment call: frequent enough that a domain becoming due is never
more than an hour late, without invoking this Lambda so often that most
invocations do near-zero work (querying `domains` and finding nothing
due yet is cheap) or produce excessive CloudWatch/Lambda invocation
volume for what is fundamentally a low-frequency decision (the fastest
any domain is ever due is once a day).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from shared_utils import get_logger

from .cadence import is_domain_due
from .config import CRAWL_QUEUE_URL
from .db import fetch_domains_with_watcher_tiers, get_connection
from .messaging import enqueue_domain_for_recrawl, get_sqs_client

logger = get_logger("scheduler-service")


def lambda_handler(event: dict[str, Any] | None = None, context: Any = None, *, now: datetime | None = None) -> dict:
    """EventBridge-triggered Lambda entrypoint. `event` is whatever
    EventBridge hands a scheduled-rule target (unused -- this Lambda's
    behavior doesn't depend on the event payload, only on the current
    state of `domains`/`monitors`/`api_keys`).

    `now` is keyword-only and optional -- real invocations never pass
    it (defaults to the actual current time); tests pass it explicitly
    for deterministic due/not-due decisions.
    """
    now = now or datetime.now(timezone.utc)

    conn = get_connection()
    try:
        domain_rows = fetch_domains_with_watcher_tiers(conn)
    finally:
        conn.close()

    sqs_client = get_sqs_client()
    enqueued: list[str] = []
    for row in domain_rows:
        domain = row["domain"]
        if is_domain_due(row["tiers"], row["last_crawled_at"], now=now):
            try:
                message_id = enqueue_domain_for_recrawl(sqs_client, CRAWL_QUEUE_URL, domain)
                logger.info("domain %s is due for recrawl (tiers=%s) -> crawl-queue message %s", domain, sorted(row["tiers"]), message_id)
                enqueued.append(domain)
            except Exception:
                # One domain's enqueue failure must not stop the rest
                # of the batch from being scheduled -- same
                # per-item-isolation principle every other handler in
                # this codebase follows.
                logger.exception("failed to enqueue domain=%s for recrawl", domain)

    return {"checked": len(domain_rows), "enqueued": len(enqueued), "domains": enqueued}


__all__ = ["lambda_handler"]
