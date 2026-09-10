#!/usr/bin/env python3
"""One-shot, idempotent LocalStack resource provisioning for the local
docker-compose stack (`docker-compose.yml`'s `localstack-setup`
service).

Mirrors (but does not exactly reproduce) `infra/terraform/envs/dev/main.tf`'s
resource shapes -- see that file for the authoritative real-deployment
names. This repo already has minor, pre-existing drift between
Terraform's resource names and the Python services' own `config.py`
env-var defaults (e.g. the raw-crawl bucket is
`agent-intelligence-raw-crawl-dev` in Terraform vs.
`agent-intel-raw-crawl-dev` as every service's own default). This
script does not attempt to reconcile that drift -- it just picks ONE
consistent set of names (matching the Python services' own `config.py`
defaults, since docker-compose.yml sets these same names explicitly via
`environment:` on every service that needs them) and provisions
exactly those, so nothing in the compose stack relies on a default
that might not match another service's default.

Safe to re-run: every create call tolerates "already exists".
"""

from __future__ import annotations

import os
import sys

import boto3
from botocore.exceptions import ClientError

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
ENDPOINT_URL = os.environ.get("AWS_ENDPOINT_URL", "http://localstack:4566")

RAW_DATA_BUCKET_NAME = os.environ.get("RAW_DATA_BUCKET_NAME", "agent-intel-raw-crawl-dev")
EXPORT_BUCKET_NAME = os.environ.get("EXPORT_BUCKET_NAME", "agent-intel-exports-dev")

CRAWL_QUEUE_NAME = os.environ.get("CRAWL_QUEUE_NAME", "crawl-queue")
CRAWL_QUEUE_DLQ_NAME = os.environ.get("CRAWL_QUEUE_DLQ_NAME", "crawl-queue-dlq")
RAW_FETCHED_QUEUE_NAME = os.environ.get("RAW_FETCHED_QUEUE_NAME", "raw-fetched")
NOTIFY_QUEUE_NAME = os.environ.get("NOTIFY_QUEUE_NAME", "notify-queue")
NOTIFY_QUEUE_DLQ_NAME = os.environ.get("NOTIFY_QUEUE_DLQ_NAME", "notify-queue-dlq")

RATE_LIMIT_TABLE_NAME = os.environ.get("RATE_LIMIT_TABLE_NAME", "agent-intel-dev-rate-limit")


def _client(service: str):
    return boto3.client(
        service,
        region_name=AWS_REGION,
        endpoint_url=ENDPOINT_URL,
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
    )


def ensure_bucket(s3_client, name: str) -> None:
    try:
        s3_client.create_bucket(Bucket=name)
        print(f"  created S3 bucket {name}")
    except s3_client.exceptions.BucketAlreadyOwnedByYou:
        print(f"  S3 bucket {name} already exists, skipping")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("BucketAlreadyExists", "BucketAlreadyOwnedByYou"):
            print(f"  S3 bucket {name} already exists, skipping")
        else:
            raise


def ensure_queue(sqs_client, name: str, *, dlq_arn: str | None = None, visibility_timeout: int = 90) -> str:
    attributes = {"VisibilityTimeout": str(visibility_timeout)}
    if dlq_arn:
        attributes["RedrivePolicy"] = (
            '{"deadLetterTargetArn": "%s", "maxReceiveCount": "5"}' % dlq_arn
        )
    queue_url = sqs_client.create_queue(QueueName=name, Attributes=attributes)["QueueUrl"]
    print(f"  queue {name} -> {queue_url}")
    return queue_url


def queue_arn(sqs_client, queue_url: str) -> str:
    return sqs_client.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]


def ensure_rate_limit_table(dynamodb_client, name: str) -> None:
    try:
        dynamodb_client.create_table(
            TableName=name,
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            BillingMode="PAY_PER_REQUEST",
        )
        print(f"  created DynamoDB table {name}")
        dynamodb_client.get_waiter("table_exists").wait(TableName=name)
    except dynamodb_client.exceptions.ResourceInUseException:
        print(f"  DynamoDB table {name} already exists, skipping")

    try:
        dynamodb_client.update_time_to_live(
            TableName=name,
            TimeToLiveSpecification={"Enabled": True, "AttributeName": "expires_at"},
        )
        print(f"  enabled TTL on {name}.expires_at")
    except ClientError as exc:
        # LocalStack returns ValidationException if TTL is already
        # enabled on this attribute -- idempotent no-op either way.
        print(f"  TTL on {name}.expires_at: {exc.response.get('Error', {}).get('Message', exc)}")


def main() -> int:
    print(f"provisioning LocalStack resources at {ENDPOINT_URL} (region={AWS_REGION})")

    s3_client = _client("s3")
    sqs_client = _client("sqs")
    dynamodb_client = _client("dynamodb")

    print("\n-- S3 buckets --")
    ensure_bucket(s3_client, RAW_DATA_BUCKET_NAME)
    ensure_bucket(s3_client, EXPORT_BUCKET_NAME)

    print("\n-- SQS queues --")
    crawl_dlq_url = ensure_queue(sqs_client, CRAWL_QUEUE_DLQ_NAME)
    crawl_dlq_arn = queue_arn(sqs_client, crawl_dlq_url)
    ensure_queue(sqs_client, CRAWL_QUEUE_NAME, dlq_arn=crawl_dlq_arn, visibility_timeout=90)

    ensure_queue(sqs_client, RAW_FETCHED_QUEUE_NAME, visibility_timeout=60)

    notify_dlq_url = ensure_queue(sqs_client, NOTIFY_QUEUE_DLQ_NAME)
    notify_dlq_arn = queue_arn(sqs_client, notify_dlq_url)
    ensure_queue(sqs_client, NOTIFY_QUEUE_NAME, dlq_arn=notify_dlq_arn, visibility_timeout=90)

    print("\n-- DynamoDB rate-limit table --")
    ensure_rate_limit_table(dynamodb_client, RATE_LIMIT_TABLE_NAME)

    print("\nOK: LocalStack resources provisioned.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
