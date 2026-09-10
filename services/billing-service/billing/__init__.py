"""billing-service: API key issuance + Stripe billing lifecycle.

See `billing/routes.py` for the endpoints, `billing/db.py` for its
Postgres access (read+write, unlike api-service's read-only relationship
with the same `api_keys` table), and `billing/stripe_client.py` for the
one seam where real Stripe API calls happen (and where tests mock them
-- see that module's docstring for exactly what is/isn't mocked).
"""
