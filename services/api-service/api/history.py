"""`GET /v1/domains/{domain}/history` support: list a domain's past
crawl snapshots straight out of the crawler's raw S3 bucket.

--- Why this reads S3 instead of a "history" table (deliberate) -------

There is no normalized crawl-history table in Postgres --
parser-service's `domains` table only ever holds *current* state (see
`parser/db.py`: a plain `INSERT ... ON CONFLICT DO UPDATE`, no history
rows kept). That's not an oversight; it's a scope decision for Phase 3
specifically to avoid touching Phase 2's already-reviewed schema
(`packages/shared-schema`) and upsert logic for a feature api-service
can satisfy another way.

crawler-service already retains every historical crawl of a domain in
S3, keyed by `{domain}/{iso-timestamp}/{artifact}` (see
`crawler/storage.py`). Each artifact object is a JSON "envelope" --
`{"url", "fetched_at", "status_code", "present", "error", "headers",
"body_base64"}` (`fetch_result_to_envelope`). So "list past crawl
snapshots" can be answered directly from that bucket: list the
per-timestamp "folders" under the domain's prefix, and for each one
pull just the lightweight envelope metadata (`fetched_at`,
`status_code`, `present`) for `agents.json` and the Web Bot Auth
directory -- WITHOUT re-parsing/re-validating `body_base64` as
agents.json / a JWKS document. Doing that full validation is
parser-service's job (see `parser/manifest.py` / `parser/webbotauth.py`);
duplicating it here would be a second, divergent implementation of the
same parsing logic for a lookup endpoint that only needs to say
"was this artifact present, and when."

--- Bounding an unboundedly-crawled domain -----------------------------

A domain crawled on a recurring schedule for a long time can have
thousands of timestamp "folders" in S3. Two bounds keep this endpoint
safe:

  1. `limit` (validated in `api/routes.py`, default
     `HISTORY_DEFAULT_LIMIT`, max `HISTORY_MAX_LIMIT`) caps how many
     snapshots are ever returned to the caller.
  2. `HISTORY_S3_LIST_CAP` caps how many timestamp prefixes a single
     request will ever list out of S3 via one `list_objects_v2` call
     (`MaxKeys`), before `limit` is even applied.

Because crawl timestamps are ISO-8601 strings (`fetch_result_to_envelope`
via `datetime.now(timezone.utc).isoformat()`), they sort
lexicographically in chronological order, so plain ascending
`list_objects_v2` + `Delimiter="/"` CommonPrefixes gives us the
prefixes in crawl order for free -- no need to parse/sort them as
real datetimes just to order them. We take the single most recent
`HISTORY_S3_LIST_CAP` of those (a single un-paginated request), then
return only the most recent `limit` of *that* window.

Known limitation, documented rather than silently swallowed: if a
domain has been crawled more than `HISTORY_S3_LIST_CAP` times, this
endpoint's notion of "most recent" is bounded by that single listing
call rather than a true global sort of every crawl ever done. A fully
correct unbounded-history endpoint would need either a real history
table or a paginated/indexed listing strategy -- both are bigger than
Phase 3's read-only lookup scope needs. `HISTORY_S3_LIST_CAP` defaults
generously (1000) so this only bites truly long-lived, frequently
recrawled domains.
"""

from __future__ import annotations

import json
from typing import Any

import boto3

from .config import HISTORY_S3_LIST_CAP, boto3_client_kwargs

AGENTS_JSON_ARTIFACT = "agents.json"
WEB_BOT_AUTH_ARTIFACT = "web-bot-auth-directory"


def get_s3_client():
    return boto3.client("s3", **boto3_client_kwargs())


def _list_crawl_timestamps(s3_client, bucket: str, domain: str) -> list[str]:
    """Return this domain's crawl-timestamp "folder" names (the ISO
    timestamp segment of `{domain}/{timestamp}/{artifact}`), in
    ascending (oldest-first) order, capped at `HISTORY_S3_LIST_CAP`
    entries from a single `list_objects_v2` call.
    """
    prefix = f"{domain}/"
    response = s3_client.list_objects_v2(
        Bucket=bucket,
        Prefix=prefix,
        Delimiter="/",
        MaxKeys=HISTORY_S3_LIST_CAP,
    )
    timestamps = []
    for entry in response.get("CommonPrefixes", []):
        common_prefix = entry.get("Prefix", "")
        # common_prefix looks like "{domain}/{timestamp}/" -- strip the
        # domain prefix and trailing slash to get just the timestamp.
        timestamp = common_prefix[len(prefix) :].rstrip("/")
        if timestamp:
            timestamps.append(timestamp)
    return timestamps


def _read_artifact_summary(s3_client, bucket: str, domain: str, timestamp: str, artifact_name: str) -> dict[str, Any]:
    """Fetch one artifact's envelope and return only the lightweight
    fields this endpoint exposes -- never the raw `body_base64`
    content (that's the full artifact body; this endpoint is metadata
    only, see module docstring).
    """
    key = f"{domain}/{timestamp}/{artifact_name}"
    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        envelope = json.loads(response["Body"].read())
        return {
            "present": bool(envelope.get("present")),
            "status_code": envelope.get("status_code"),
            "fetched_at": envelope.get("fetched_at"),
        }
    except s3_client.exceptions.NoSuchKey:
        # Shouldn't normally happen -- crawler-service writes both
        # artifacts' envelopes unconditionally for every crawl (see
        # crawler/handler.py's `process_domain`) -- but a lookup
        # endpoint should degrade gracefully rather than 500 on one
        # missing/corrupted historical object.
        return {"present": False, "status_code": None, "fetched_at": None}


def list_domain_history(s3_client, bucket: str, domain: str, *, limit: int) -> list[dict[str, Any]]:
    """Return up to `limit` of `domain`'s most recent crawl snapshots,
    most-recent-first. Each snapshot is
    `{"crawled_at": <iso timestamp>, "agents_json": {...}, "web_bot_auth": {...}}`.
    """
    timestamps = _list_crawl_timestamps(s3_client, bucket, domain)
    most_recent_first = list(reversed(timestamps))[:limit]

    snapshots = []
    for timestamp in most_recent_first:
        snapshots.append(
            {
                "crawled_at": timestamp,
                "agents_json": _read_artifact_summary(s3_client, bucket, domain, timestamp, AGENTS_JSON_ARTIFACT),
                "web_bot_auth": _read_artifact_summary(
                    s3_client, bucket, domain, timestamp, WEB_BOT_AUTH_ARTIFACT
                ),
            }
        )
    return snapshots


__all__ = ["get_s3_client", "list_domain_history"]
