"""api key auth and billing fields

Revision ID: 8ab25d318716
Revises: 164e2b863589
Create Date: 2026-09-10 14:32:51.039117

Phase 4: extends `api_keys` to support real key issuance/auth and
Stripe billing lifecycle. See `shared_schema/models.py`'s `ApiKey`
docstring for the per-field rationale; summarized here:

  * `key_hash` (NOT NULL, UNIQUE) -- SHA-256 hex digest of the actual
    high-entropy bearer secret (`ai_live_...`, see
    `shared_utils.api_keys`). This is what api-service's auth
    dependency looks up by. Added NOT NULL directly (no backfill step)
    because this table has never had real rows in any deployed
    environment -- Phase 3 only ever imported `ApiKey` to prove wiring
    (see billing-service's Phase 0 `app.py`) and no `terraform apply`
    has ever run against real AWS, so there is no existing-row data
    migration concern. Confirmed empty against local dev Postgres
    before writing this migration.

    `key_id` (the existing primary key) is deliberately NOT the secret
    itself -- storing a usable bearer credential in plaintext in the
    database would be the API-key equivalent of storing passwords in
    plaintext. `key_hash` is the only thing persisted that auth can act
    on; the plaintext secret exists only transiently at issuance time
    (see `pending_secret` below).

  * `active` (NOT NULL, default true) -- lets a canceled Stripe
    subscription deactivate its key (auth starts rejecting it) without
    deleting the row, so billing history/audit trail survives
    cancellation.

  * `stripe_customer_id` (nullable, UNIQUE) / `stripe_subscription_id`
    (nullable) -- link a self-serve key to its Stripe objects so
    `customer.subscription.updated`/`.deleted` webhooks can find and
    update the right row. Nullable because out-of-band `licensing`
    keys never touch Stripe.

  * `stripe_checkout_session_id` (nullable, UNIQUE) -- the specific
    Checkout Session that created this row. This is the idempotency
    key for `checkout.session.completed` webhook processing (a
    redelivered event for the same session hits a unique-constraint
    conflict and no-ops instead of minting a second key/row) and is
    also how `GET /v1/billing/session/{id}` finds the row without a
    second Stripe API round-trip.

  * `pending_secret` (nullable) -- KNOWN GAP, documented in depth on
    the model: a transient plaintext-secret holding cell for the
    no-email-service MVP retrieval flow (`GET
    /v1/billing/session/{id}`), cleared to NULL after first read.
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '8ab25d318716'
down_revision: Union[str, None] = '164e2b863589'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('api_keys', sa.Column('key_hash', sa.String(), nullable=False))
    op.add_column('api_keys', sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False))
    op.add_column('api_keys', sa.Column('stripe_customer_id', sa.String(), nullable=True))
    op.add_column('api_keys', sa.Column('stripe_subscription_id', sa.String(), nullable=True))
    op.add_column('api_keys', sa.Column('stripe_checkout_session_id', sa.String(), nullable=True))
    op.add_column('api_keys', sa.Column('pending_secret', sa.String(), nullable=True))

    # Explicit constraint names (autogenerate leaves these unnamed,
    # which makes `downgrade()` unable to reference them by name) --
    # named after Postgres's own default naming convention so they're
    # recognizable in `\d api_keys` output too.
    op.create_unique_constraint('api_keys_key_hash_key', 'api_keys', ['key_hash'])
    op.create_unique_constraint('api_keys_stripe_customer_id_key', 'api_keys', ['stripe_customer_id'])
    op.create_unique_constraint(
        'api_keys_stripe_checkout_session_id_key', 'api_keys', ['stripe_checkout_session_id']
    )


def downgrade() -> None:
    op.drop_constraint('api_keys_stripe_checkout_session_id_key', 'api_keys', type_='unique')
    op.drop_constraint('api_keys_stripe_customer_id_key', 'api_keys', type_='unique')
    op.drop_constraint('api_keys_key_hash_key', 'api_keys', type_='unique')
    op.drop_column('api_keys', 'pending_secret')
    op.drop_column('api_keys', 'stripe_checkout_session_id')
    op.drop_column('api_keys', 'stripe_subscription_id')
    op.drop_column('api_keys', 'stripe_customer_id')
    op.drop_column('api_keys', 'active')
    op.drop_column('api_keys', 'key_hash')
