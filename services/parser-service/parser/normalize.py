"""Assembles the normalized `domains` row for one crawl.

Field-for-field, this mirrors `shared_schema.models.Domain`. Kept as a
plain dict (rather than constructing a `Domain` instance) because the
DB layer (db.py) writes/reads plain dicts via psycopg2 -- see db.py's
docstring for why this phase doesn't route runtime reads/writes through
an ORM. A caller who wants the strongly-typed Pydantic object back can
do `Domain(**record)`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .manifest import ParsedManifest
from .webbotauth import ParsedWebBotAuth

# Fields that participate in change detection (messaging.py's diff).
#
# Deliberately excludes pure bookkeeping columns (`first_seen_at`,
# `last_crawled_at`, `created_at`, `updated_at`) -- those always change
# on every crawl and would make every crawl look like a "change".
#
# Also deliberately excludes `agent_json_s3_key`/`llms_txt_s3_key`:
# crawler-service writes every crawl's artifact to a *new*,
# timestamp-keyed S3 object (see crawler/storage.py's
# `{domain}/{iso-timestamp}/{artifact}` key scheme), so these pointer
# fields change on literally every single crawl even when the
# underlying content is byte-for-byte identical. Diffing them would
# make `record-changed` fire on every crawl of every domain, which
# defeats the point of change detection -- these are storage pointers,
# not "signal" content. `declared_capabilities` (the actual parsed
# content) is what's compared instead.
DIFF_FIELDS = (
    "agent_json_present",
    "llms_txt_present",
    "web_bot_auth_present",
    "web_bot_auth_key_id",
    "web_bot_auth_valid",
    "web_bot_auth_expiry",
    "declared_capabilities",
    "confidence_flags",
    "on_chain_ref",
)


def build_domain_record(
    *,
    domain: str,
    crawled_at: datetime,
    agents_json_present: bool,
    agents_json_s3_key: str | None,
    manifest: ParsedManifest,
    web_bot_auth_present: bool,
    web_bot_auth_s3_key: str | None,
    webbotauth: ParsedWebBotAuth,
    confidence_flags: list[str],
    previous: dict[str, Any] | None,
    now: datetime,
) -> dict[str, Any]:
    """Build the full normalized record for one domain's crawl.

    `previous` is the domain's existing DB row (None if this is the
    first crawl ever seen for it) -- used only to preserve
    `first_seen_at`/`created_at` across crawls; every other field is
    always recomputed fresh from this crawl's inputs.

    Phase 1 (crawler-service) doesn't fetch llms.txt or an on-chain
    registry ref yet, so those fields are always their Pydantic-model
    defaults here (False/None) -- not this module inventing data for
    signals nothing upstream produces yet.
    """
    first_seen_at = previous["first_seen_at"] if previous else crawled_at
    created_at = previous["created_at"] if previous else now

    return {
        "domain": domain,
        "first_seen_at": first_seen_at,
        "last_crawled_at": crawled_at,
        "agent_json_present": agents_json_present,
        "agent_json_s3_key": agents_json_s3_key,
        "llms_txt_present": False,
        "llms_txt_s3_key": None,
        "web_bot_auth_present": web_bot_auth_present,
        "web_bot_auth_key_id": webbotauth.key_id,
        "web_bot_auth_valid": webbotauth.valid,
        "web_bot_auth_expiry": webbotauth.expiry,
        "declared_capabilities": manifest.declared_capabilities,
        "confidence_flags": confidence_flags,
        "on_chain_ref": None,
        "created_at": created_at,
        "updated_at": now,
    }


__all__ = ["DIFF_FIELDS", "build_domain_record"]
