"""SQS publishing of the `raw-fetched` event.

One message per crawled domain, published once both signals have been
fetched and stored. Consumed downstream by parser-service (Phase 2).
"""

from __future__ import annotations

import json

import boto3

from .config import boto3_client_kwargs


def get_sqs_client():
    return boto3.client("sqs", **boto3_client_kwargs())


def build_raw_fetched_message(
    *,
    domain: str,
    crawled_at: str,
    agents_json_present: bool,
    agents_json_status_code: int | None,
    agents_json_s3_key: str,
    web_bot_auth_present: bool,
    web_bot_auth_status_code: int | None,
    web_bot_auth_s3_key: str,
    llms_txt_present: bool = False,
    llms_txt_status_code: int | None = None,
    llms_txt_s3_key: str | None = None,
    on_chain_ref: str | None = None,
) -> dict:
    """Build the `raw-fetched` message payload.

    Shape: the domain, what was found (presence booleans + status
    codes for each signal), and the S3 keys written for each -- enough
    for parser-service to go fetch the raw artifacts and normalize
    them, without re-deriving anything crawler-service already knows.

    `llms_txt_*` (Phase 5) mirrors `agents_json`'s shape exactly --
    `llms_txt_s3_key` is None (not written to S3 at all) when
    `CRAWL_LLMS_TXT_ENABLED` is false, since no fetch was attempted.
    `on_chain_ref` (Phase 5) is a bare passthrough of whatever
    `onchain.lookup_on_chain_ref` returned (always None today -- see
    that module's docstring), not an artifact fetch, so it has no
    accompanying S3 key.
    """
    return {
        "domain": domain,
        "crawled_at": crawled_at,
        "agents_json": {
            "present": agents_json_present,
            "status_code": agents_json_status_code,
            "s3_key": agents_json_s3_key,
        },
        "web_bot_auth": {
            "present": web_bot_auth_present,
            "status_code": web_bot_auth_status_code,
            "s3_key": web_bot_auth_s3_key,
        },
        "llms_txt": {
            "present": llms_txt_present,
            "status_code": llms_txt_status_code,
            "s3_key": llms_txt_s3_key,
        },
        "on_chain_ref": on_chain_ref,
    }


def publish_raw_fetched(sqs_client, queue_url: str, message: dict) -> str:
    """Publish `message` to the `raw-fetched` queue, returning the SQS MessageId."""
    response = sqs_client.send_message(QueueUrl=queue_url, MessageBody=json.dumps(message))
    return response["MessageId"]


__all__ = ["build_raw_fetched_message", "get_sqs_client", "publish_raw_fetched"]
