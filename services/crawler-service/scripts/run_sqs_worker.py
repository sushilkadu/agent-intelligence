#!/usr/bin/env python3
"""Local, long-running SQS worker adapter for crawler-service's Lambda
handler.

This is docker-compose-only infrastructure glue, NOT part of the real
Lambda deployment and NOT new business logic. In production,
`crawl-queue` triggers `crawler.handler.lambda_handler` via a real SQS
event source mapping (see infra/terraform/envs/dev/main.tf's
`crawler_lambda`). There is no Lambda runtime in local docker-compose,
so this script long-polls the queue itself and hands each batch of
messages to the exact same unchanged `lambda_handler`, in the exact
`{"Records": [{"body": ...}]}` shape an SQS event source mapping would
deliver -- the same shape `scripts/verify_localstack_e2e.py` already
builds by hand for its own one-off verification run.

Messages are deleted from the queue ONLY if `lambda_handler` returns
without raising. An exception is deliberately NOT swallowed here (it's
logged and the loop continues) -- the message is left on the queue for
SQS's own visibility-timeout-based redelivery / DLQ behavior, matching
real Lambda's at-least-once, redrive-on-failure semantics. Per-item
failures within one batch are already handled inside `lambda_handler`
itself (it catches per-domain exceptions and never lets one bad domain
fail the whole batch); an exception escaping `lambda_handler` here
means something more fundamental broke.

Run via: `python scripts/run_sqs_worker.py` (see the service's
Dockerfile -- this is its container CMD).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Allow running as `python scripts/run_sqs_worker.py` without installing
# the crawler package -- mirrors scripts/load_seed_list.py.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boto3
from shared_utils import get_logger

from crawler.config import CRAWL_QUEUE_URL, boto3_client_kwargs
from crawler.handler import lambda_handler

logger = get_logger("crawler-service.worker")

WAIT_TIME_SECONDS = 10
MAX_MESSAGES = 10


def main() -> int:
    if not CRAWL_QUEUE_URL:
        print("CRAWL_QUEUE_URL is not set -- nothing to poll.", file=sys.stderr)
        return 1

    sqs_client = boto3.client("sqs", **boto3_client_kwargs())
    logger.info("crawler-service SQS worker starting; polling %s", CRAWL_QUEUE_URL)

    while True:
        try:
            received = sqs_client.receive_message(
                QueueUrl=CRAWL_QUEUE_URL,
                MaxNumberOfMessages=MAX_MESSAGES,
                WaitTimeSeconds=WAIT_TIME_SECONDS,
            )
        except Exception:
            logger.exception("receive_message failed against %s; retrying shortly", CRAWL_QUEUE_URL)
            time.sleep(5)
            continue

        messages = received.get("Messages", [])
        if not messages:
            continue

        event = {"Records": [{"body": m["Body"]} for m in messages]}
        try:
            result = lambda_handler(event, None)
            logger.info("processed batch of %d message(s): %s", len(messages), result)
        except Exception:
            # Do NOT delete on failure: leave the whole batch on the
            # queue so SQS's own redelivery/DLQ handles it, exactly as
            # a raised exception would in real Lambda.
            logger.exception(
                "lambda_handler raised for a batch of %d message(s); leaving them for redelivery",
                len(messages),
            )
            continue

        for m in messages:
            try:
                sqs_client.delete_message(QueueUrl=CRAWL_QUEUE_URL, ReceiptHandle=m["ReceiptHandle"])
            except Exception:
                logger.exception("failed to delete message %s after successful processing", m.get("MessageId"))


if __name__ == "__main__":
    raise SystemExit(main())
