"""monitor ownership and ssrf-safe webhook url

Revision ID: 05c793fc3af7
Revises: 8ab25d318716
Create Date: 2026-09-10 16:41:00.548593

Phase 5: fixes a real gap in `monitors` as originally committed
(Phase 0) -- the table had no owner column at all, so any caller (or
any API key) could register a webhook monitor for any domain, and
later delete anyone else's monitor by guessing/enumerating
`monitor_id`. See `shared_schema/models.py`'s `Monitor.owner_key_id`
docstring, and `services/api-service/api/routes.py`'s monitor
endpoints, for the full rationale.

  * `owner_key_id` (NOT NULL, FK -> api_keys.key_id) -- the API key
    that registered this monitor. Added NOT NULL directly (no backfill
    step), same reasoning as the prior migration's `key_hash`: this
    table has never had real rows in any deployed environment (no
    `terraform apply` has ever run against real AWS), so there is no
    existing-row data migration concern. Confirmed empty against local
    dev Postgres before writing this migration.

    `POST /v1/monitors` (api-service) is the only path that creates a
    `monitors` row, and it requires an authenticated, active,
    self_serve/licensing-tier API key -- so every row this migration
    will ever see created already has a real owner.

Note this migration only addresses the *ownership* gap at the
database-schema level (an unowned monitor becoming structurally
impossible). The companion *SSRF* gap (an attacker registering a
webhook_url pointing at a private/metadata address) is enforced at the
application layer instead -- see `shared_utils/webhook_safety.py` and
its use in both api-service (at registration) and notifier-service (at
delivery) -- there is no database constraint that can validate a URL
resolves somewhere safe, since DNS resolution isn't a schema-level
concern and can change between registration and delivery time (the
documented residual DNS-rebinding risk).
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '05c793fc3af7'
down_revision: Union[str, None] = '8ab25d318716'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('monitors', sa.Column('owner_key_id', sa.String(), nullable=False))
    # Explicit constraint name (autogenerate leaves this unnamed, which
    # makes `downgrade()` unable to reference it by name) -- same
    # convention the prior migration established for its unique
    # constraints.
    op.create_foreign_key(
        'monitors_owner_key_id_fkey', 'monitors', 'api_keys', ['owner_key_id'], ['key_id']
    )


def downgrade() -> None:
    op.drop_constraint('monitors_owner_key_id_fkey', 'monitors', type_='foreignkey')
    op.drop_column('monitors', 'owner_key_id')
