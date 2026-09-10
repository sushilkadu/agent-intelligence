"""SQLAlchemy Core table metadata for the canonical schema.

Phase 2 note: this is intentionally *not* a declarative ORM layer.
`models.py`'s Pydantic classes are already the canonical shape/validation
of the data; adding a parallel set of ORM model classes on top would
just be a second place to keep those fields in sync. SQLAlchemy Core
`Table` objects are the minimum needed to give Alembic something to
diff against (`alembic revision --autogenerate`) and to build SQL with
`sqlalchemy.sql` if a caller wants that -- they carry no behavior of
their own.

Runtime reads/writes (parser-service's upsert) do NOT have to route
through this module -- see `services/parser-service/parser/db.py`,
which uses plain psycopg2 + hand-written SQL instead, because the
upsert is a single `INSERT ... ON CONFLICT DO UPDATE` with a couple of
column exclusions (first_seen_at/created_at) that's simpler to read as
raw SQL than to express through SQLAlchemy Core's `on_conflict_do_update`
constructs. This module exists for migrations only.

Column definitions mirror `models.py`'s `Domain`, `ApiKey`, and
`Monitor` field-for-field (types, nullability, defaults) -- if you add
or change a field on those Pydantic models, update the matching column
here and generate a new Alembic migration.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

from .models import PlanTier

metadata = MetaData()

domains = Table(
    "domains",
    metadata,
    Column("domain", String, primary_key=True),
    Column("first_seen_at", DateTime(timezone=True), nullable=False),
    Column("last_crawled_at", DateTime(timezone=True), nullable=False),
    Column("agent_json_present", Boolean, nullable=False, server_default="false"),
    Column("agent_json_s3_key", String, nullable=True),
    Column("llms_txt_present", Boolean, nullable=False, server_default="false"),
    Column("llms_txt_s3_key", String, nullable=True),
    Column("web_bot_auth_present", Boolean, nullable=False, server_default="false"),
    Column("web_bot_auth_key_id", String, nullable=True),
    Column("web_bot_auth_valid", Boolean, nullable=False, server_default="false"),
    Column("web_bot_auth_expiry", DateTime(timezone=True), nullable=True),
    # JSONB (not a stricter type) matches the Pydantic model's
    # `dict[str, Any]` -- declared_capabilities is deliberately
    # schemaless, see parser-service's manifest.py docstring on why
    # agents.json isn't validated against any one fixed schema.
    Column("declared_capabilities", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    # A simple list of short flag strings -- a native Postgres text
    # array is more idiomatic here than JSONB for a flat list.
    Column("confidence_flags", ARRAY(String), nullable=False, server_default=text("'{}'")),
    Column("on_chain_ref", String, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

api_keys = Table(
    "api_keys",
    metadata,
    Column("key_id", String, primary_key=True),
    Column("owner_email", String, nullable=False),
    Column(
        "plan_tier",
        Enum(PlanTier, name="plan_tier", values_callable=lambda enum_cls: [e.value for e in enum_cls]),
        nullable=False,
    ),
    Column("rate_limit", Integer, nullable=False),
    # --- Phase 4 additions -- see alembic/versions/<rev>_api_key_auth_and_billing.py
    # for the full rationale; kept in sync here per this module's
    # docstring (mirror models.py's `ApiKey` field-for-field).
    #
    # SHA-256 hex digest of the real bearer secret. Unique + not null:
    # every key row is created with a real, distinct secret already
    # hashed -- this is what auth looks up by, never `key_id`.
    Column("key_hash", String, nullable=False, unique=True),
    # False once a Stripe subscription is canceled -- see models.py.
    Column("active", Boolean, nullable=False, server_default=text("true")),
    # Nullable + unique: null for out-of-band (licensing) keys that
    # never touch Stripe; unique so a customer maps to at most one live
    # api_keys row per Stripe customer.
    Column("stripe_customer_id", String, nullable=True, unique=True),
    Column("stripe_subscription_id", String, nullable=True),
    # Nullable + unique: the Checkout Session that created this row --
    # the idempotency key for webhook processing (see billing-service's
    # webhook handler) and the lookup key for the one-time key-retrieval
    # endpoint.
    Column("stripe_checkout_session_id", String, nullable=True, unique=True),
    # KNOWN GAP, see models.py's docstring on this field: transient
    # plaintext-secret holding cell for the no-email MVP retrieval flow.
    Column("pending_secret", String, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

monitors = Table(
    "monitors",
    metadata,
    Column("monitor_id", UUID(as_uuid=True), primary_key=True),
    Column("domain", String, ForeignKey("domains.domain"), nullable=False),
    Column("webhook_url", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

__all__ = ["api_keys", "domains", "metadata", "monitors"]
