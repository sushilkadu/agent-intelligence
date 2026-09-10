"""Canonical Pydantic models for the Agent Intelligence schema.

Phase 0 scope: these define the shape of the data (domains, api_keys,
monitors) that crawler/parser/api services will eventually read and
write. There is no persistence layer here yet -- no SQLAlchemy models,
no Alembic migrations. That comes in a later phase once the schema
has been proven out by the service wiring in Phase 0.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class PlanTier(str, Enum):
    """Billing plan tiers for API key holders."""

    FREE = "free"
    SELF_SERVE = "self_serve"
    LICENSING = "licensing"


class Domain(BaseModel):
    """A single crawled domain and the agent-identity signals found on it.

    `domain` is the primary key (the bare hostname, e.g. "example.com").
    """

    domain: str = Field(..., description="Primary key: bare hostname, e.g. 'example.com'")
    first_seen_at: datetime
    last_crawled_at: datetime

    agent_json_present: bool = False
    agent_json_s3_key: Optional[str] = None

    llms_txt_present: bool = False
    llms_txt_s3_key: Optional[str] = None

    web_bot_auth_present: bool = False
    web_bot_auth_key_id: Optional[str] = None
    web_bot_auth_valid: bool = False
    web_bot_auth_expiry: Optional[datetime] = None

    declared_capabilities: dict[str, Any] = Field(default_factory=dict)
    confidence_flags: list[str] = Field(default_factory=list)

    on_chain_ref: Optional[str] = None

    created_at: datetime
    updated_at: datetime


class ApiKey(BaseModel):
    """An issued API key for the paid/free lookup API.

    Phase 4 note: `key_id` is a non-secret identifier only -- it is
    NOT the bearer credential a caller authenticates with. The actual
    high-entropy secret (`ai_live_...`, see
    `shared_utils.api_keys.generate_api_key_secret`) is generated once
    at issuance, shown to the customer exactly once, and never stored.
    `key_hash` (its SHA-256 hex digest) is what auth actually looks up
    by -- this is the same "never store the usable credential in
    plaintext" principle as password hashing, applied to API keys.
    """

    key_id: str = Field(..., description="Primary key -- a non-secret identifier, not the bearer credential itself")
    owner_email: str
    plan_tier: PlanTier
    rate_limit: int = Field(..., description="Requests allowed per window, e.g. per minute")

    key_hash: str = Field(
        ..., description="SHA-256 hex digest of the actual bearer secret -- what auth looks up by. Never the plaintext."
    )

    active: bool = Field(
        True,
        description="False once a subscription is canceled/deactivated -- key rows are kept (not deleted) so "
        "history/audit trails survive cancellation; an inactive key fails auth even if key_hash matches.",
    )

    stripe_customer_id: Optional[str] = Field(
        None, description="Stripe Customer id, for self-serve keys issued via Checkout. Null for out-of-band (licensing) keys."
    )
    stripe_subscription_id: Optional[str] = Field(
        None, description="Stripe Subscription id backing this key's plan, kept in sync via webhook events."
    )
    stripe_checkout_session_id: Optional[str] = Field(
        None,
        description="The Checkout Session id that created this key. Unique -- lets webhook processing be idempotent "
        "(a redelivered `checkout.session.completed` for the same session is a no-op, not a second row) and lets "
        "GET /v1/billing/session/{id} find this row without a second Stripe round-trip.",
    )

    pending_secret: Optional[str] = Field(
        None,
        description="KNOWN GAP (see billing-service's Phase 4 report): the plaintext bearer secret, held here only "
        "transiently between issuance and its first retrieval via GET /v1/billing/session/{id}, then cleared to "
        "NULL. This is a pragmatic MVP substitute for emailing the customer their key (no email-sending service "
        "exists anywhere in this architecture yet) -- it is a real plaintext-secret-at-rest window, not a "
        "'shown exactly once and never persisted' guarantee. Must never be included in any response model besides "
        "the one-time retrieval endpoint's.",
    )

    created_at: datetime


class Monitor(BaseModel):
    """A subscription to be notified (via webhook) of changes to a domain.

    Phase 5 note: `owner_key_id` was added to close a real gap in this
    model as originally committed (Phase 0) -- without an owner, ANY
    caller (or any API key at all) could register a webhook monitor for
    ANY domain, and later delete ANYONE's monitor by guessing/enumerating
    `monitor_id`. See `api/routes.py`'s monitor endpoints and the new
    Alembic migration's docstring for the full rationale. Every monitor
    now belongs to exactly one `api_keys` row (the key that registered
    it); `POST /v1/monitors` only accepts paid-tier keys (monitoring is
    a paid feature) and `DELETE /v1/monitors/{id}` only succeeds for the
    owning key.
    """

    monitor_id: UUID = Field(..., description="Primary key")
    domain: str = Field(..., description="FK reference to domains.domain")
    webhook_url: str
    owner_key_id: str = Field(
        ..., description="FK reference to api_keys.key_id -- the paid-tier key that registered this monitor."
    )
    created_at: datetime
