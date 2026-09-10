#!/usr/bin/env python3
"""Local, long-running SQS worker adapter for notifier-service's Lambda
handler.

See crawler-service's `scripts/run_sqs_worker.py` for the full
rationale -- this is the same pattern, applied to `notify-queue` ->
`notifier.handler.lambda_handler` instead of `crawl-queue` ->
`crawler.handler.lambda_handler`. Purely infrastructure glue: no
business logic lives here, `notifier/handler.py` is unmodified, and a
failed batch is left on the queue (not deleted) for SQS's own
redelivery/DLQ handling rather than being swallowed.

Run via: `python scripts/run_sqs_worker.py` (see the service's
Dockerfile -- this is its container CMD).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boto3
from shared_utils import get_logger

from notifier.config import NOTIFY_QUEUE_URL, boto3_client_kwargs
from notifier.handler import lambda_handler

logger = get_logger("notifier-service.worker")

WAIT_TIME_SECONDS = 10
MAX_MESSAGES = 10


def main() -> int:
    if not NOTIFY_QUEUE_URL:
        print("NOTIFY_QUEUE_URL is not set -- nothing to poll.", file=sys.stderr)
        return 1

    sqs_client = boto3.client("sqs", **boto3_client_kwargs())
    logger.info("notifier-service SQS worker starting; polling %s", NOTIFY_QUEUE_URL)

    while True:
        try:
            received = sqs_client.receive_message(
                QueueUrl=NOTIFY_QUEUE_URL,
                MaxNumberOfMessages=MAX_MESSAGES,
                WaitTimeSeconds=WAIT_TIME_SECONDS,
            )
        except Exception:
            logger.exception("receive_message failed against %s; retrying shortly", NOTIFY_QUEUE_URL)
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
            logger.exception(
                "lambda_handler raised for a batch of %d message(s); leaving them for redelivery",
                len(messages),
            )
            continue

        for m in messages:
            try:
                sqs_client.delete_message(QueueUrl=NOTIFY_QUEUE_URL, ReceiptHandle=m["ReceiptHandle"])
            except Exception:
                logger.exception("failed to delete message %s after successful processing", m.get("MessageId"))


if __name__ == "__main__":
    raise SystemExit(main())
