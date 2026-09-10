"""Publish domains due for recrawl onto `crawl-queue`.

Reuses `crawl-queue` and its EXACT existing message shape
(`{"domain": "<domain>"}`, one message per domain) -- the same queue
crawler-service already consumes from and
`scripts/load_seed_list.py`/`scripts/verify_localstack_e2e.py` already
publish to (see crawler-service's `crawler/handler.py`'s
`extract_domain`, which accepts exactly this shape). Deliberately NOT a
second/different message shape or a second entry point into the crawl
pipeline -- crawler-service's consumer side needs no changes at all for
scheduler-service to become a second producer onto the same queue.
"""

from __future__ import annotations

import json

import boto3

from .config import boto3_client_kwargs


def get_sqs_client():
    return boto3.client("sqs", **boto3_client_kwargs())


def enqueue_domain_for_recrawl(sqs_client, queue_url: str, domain: str) -> str:
    """Publish one domain onto `crawl-queue`, returning the SQS MessageId."""
    response = sqs_client.send_message(QueueUrl=queue_url, MessageBody=json.dumps({"domain": domain}))
    return response["MessageId"]


__all__ = ["enqueue_domain_for_recrawl", "get_sqs_client"]
