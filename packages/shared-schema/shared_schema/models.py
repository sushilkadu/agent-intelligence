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
    """An issued API key for the paid/free lookup API."""

    key_id: str = Field(..., description="Primary key")
    owner_email: str
    plan_tier: PlanTier
    rate_limit: int = Field(..., description="Requests allowed per window, e.g. per minute")
    created_at: datetime


class Monitor(BaseModel):
    """A subscription to be notified (via webhook) of changes to a domain."""

    monitor_id: UUID = Field(..., description="Primary key")
    domain: str = Field(..., description="FK reference to domains.domain")
    webhook_url: str
    created_at: datetime
