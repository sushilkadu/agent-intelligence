"""On-demand crawl triggering for `GET /v1/domains/{domain}`'s cache-miss
path (see `api/routes.py`'s `_trigger_on_demand_crawl`).

Two small pieces of AWS-facing logic live here, kept separate from
`api/routes.py` so both are unit-testable on their own:

  1. `enqueue_crawl` -- publish `domain` onto `crawl-queue`, in the
     EXACT same message shape (`{"domain": "<domain>"}`) crawler-service's
     `extract_domain` already accepts and scheduler-service's
     `enqueue_domain_for_recrawl` already produces (see
     services/scheduler-service/scheduler/messaging.py). api-service
     becomes a THIRD producer onto this one queue -- crawler-service's
     consumer side needs no changes at all.

  2. `try_acquire_crawl_lock` -- duplicate-crawl suppression. Two
     lookups for the same not-yet-crawled domain arriving close
     together (two browser tabs, a retry, concurrent visitors) must
     not each enqueue a separate crawl. Rather than a second table,
     this reuses the SAME DynamoDB table `api/ratelimit.py` already
     uses for the free-tier per-IP counter (one item per
     `{ip}#{window_start}` there) -- a distinct `pending-crawl#{domain}`
     key prefix keeps this marker in its own item, never colliding
     with a rate-limit window's item id (see `_pending_crawl_item_id`).
     A short TTL (`PENDING_CRAWL_TTL_SECONDS`, see api/config.py) means
     a crawl that never completes (crashed worker, permanently
     unreachable domain) doesn't block that domain from ever being
     retried.

Both AWS calls are real boto3 (mocked via moto in tests, exactly like
the rest of this service's DynamoDB/S3 access -- see
tests/test_ratelimit.py, tests/test_bulk.py).
"""

from __future__ import annotations

import json
import time

import boto3

from .config import boto3_client_kwargs


def get_crawl_sqs_client():
    return boto3.client("sqs", **boto3_client_kwargs())


def enqueue_crawl(sqs_client, queue_url: str, domain: str) -> str:
    """Publish `domain` onto `crawl-queue`, returning the SQS MessageId.

    Deliberately the SAME message shape scheduler-service's
    `enqueue_domain_for_recrawl` already produces
    (`{"domain": "<domain>"}`) -- not re-derived here to avoid two
    definitions of "what a crawl-queue message looks like" silently
    drifting apart.
    """
    response = sqs_client.send_message(QueueUrl=queue_url, MessageBody=json.dumps({"domain": domain}))
    return response["MessageId"]


def _pending_crawl_item_id(domain: str) -> str:
    """The DynamoDB item id used to mark `domain` as "a crawl is
    already in flight."

    The `pending-crawl#` prefix is deliberately distinct from every
    other item-id shape already written to this same table by
    `api/ratelimit.py` (`{ip}#{window_start}` for the free tier,
    `key:{key_id}#{window_start}` for an authenticated key) -- a
    domain name can never collide with either of those and silently
    share (or corrupt) a rate-limit counter.
    """
    return f"pending-crawl#{domain}"


def try_acquire_crawl_lock(
    dynamodb_client,
    table_name: str,
    domain: str,
    *,
    ttl_seconds: int,
    now: float | None = None,
) -> bool:
    """Atomically claim the right to enqueue an on-demand crawl for
    `domain`.

    Returns True if THIS call acquired the marker (the caller should go
    ahead and enqueue a crawl); False if another request already holds
    an unexpired marker for this same domain (the caller must NOT
    enqueue again -- a crawl is already in flight, so it should just
    report "still pending" the same way it would if it had triggered
    one itself).

    Implemented as a single conditional `PutItem`: the condition
    expression accepts either "no item exists yet" or "an item exists
    but its own `expires_at` has already passed" -- so this is
    correct even if DynamoDB's TTL sweep (background, best-effort, can
    lag real time by minutes -- see api/ratelimit.py's docstring on the
    identical caveat for its own TTL use) hasn't actually deleted a
    stale marker yet. DynamoDB evaluates the condition and performs the
    write atomically server-side, so two concurrent requests for the
    same domain can never both "win" this check.
    """
    now = now if now is not None else time.time()
    item_id = _pending_crawl_item_id(domain)
    expires_at = int(now) + ttl_seconds
    try:
        dynamodb_client.put_item(
            TableName=table_name,
            Item={"id": {"S": item_id}, "expires_at": {"N": str(expires_at)}},
            ConditionExpression="attribute_not_exists(id) OR expires_at < :now",
            ExpressionAttributeValues={":now": {"N": str(now)}},
        )
        return True
    except dynamodb_client.exceptions.ConditionalCheckFailedException:
        return False


def release_crawl_lock(dynamodb_client, table_name: str, domain: str) -> None:
    """Release a lock this same request just acquired via
    `try_acquire_crawl_lock`, WITHOUT enqueuing a crawl.

    The only caller is `api/routes.py`'s `_trigger_on_demand_crawl`,
    for exactly one case: this request won the race to trigger a new
    crawl, but then failed the separate, tighter crawl-triggering rate
    limit (see `api/ratelimit.py`'s `check_and_increment_for_crawl_trigger`)
    -- so no crawl was actually enqueued. Leaving the marker in place
    would falsely tell every OTHER caller (any IP, not just this
    rate-limited one) "a crawl for this domain is already in flight"
    for the rest of `PENDING_CRAWL_TTL_SECONDS`, blocking a legitimate
    request from a different, well-behaved visitor for no real reason.
    A plain `DeleteItem` (not conditional) is correct here: this
    request is the one that just wrote the marker, so deleting it
    unconditionally simply undoes that write.
    """
    dynamodb_client.delete_item(
        TableName=table_name,
        Key={"id": {"S": _pending_crawl_item_id(domain)}},
    )


__all__ = ["enqueue_crawl", "get_crawl_sqs_client", "release_crawl_lock", "try_acquire_crawl_lock"]
