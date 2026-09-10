"""Request/response models for billing-service's endpoints.

None of these reuse `shared_schema.ApiKey` directly as a response shape
(unlike api-service's deliberate choice to return `Domain` as-is -- see
api-service's `api/schemas.py`) -- `ApiKey` carries `key_hash` and the
transient `pending_secret`, and a response model built straight from it
risks accidentally serializing one of those into an HTTP response the
moment a field gets added to `ApiKey` without a matching audit here.
Explicit, narrow response models are the safer default for anything
billing-related.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class CheckoutRequest(BaseModel):
    email: str = Field(..., min_length=3, description="Customer email Stripe Checkout will bill/notify.")
    plan_tier: str = Field(..., description="Only 'self_serve' is purchasable here; see routes.py's docstring.")


class CheckoutResponse(BaseModel):
    checkout_url: str = Field(..., description="Redirect the customer's browser here to complete Stripe Checkout.")


class SessionKeyResponse(BaseModel):
    """`GET /v1/billing/session/{id}` -- see that route's docstring for
    the "shown once" caveat this response represents.
    """

    key_id: str
    plan_tier: str
    rate_limit: int
    api_key: str | None = Field(
        None, description="The plaintext bearer secret -- present only on its first successful retrieval."
    )
    already_retrieved: bool = Field(
        ..., description="True if this key's secret was already retrieved once before and can no longer be shown."
    )


class PortalRequest(BaseModel):
    """Exactly one of `email`/`api_key` must be supplied -- whichever
    identifies the Stripe customer to open a portal session for.
    """

    email: str | None = None
    api_key: str | None = None

    @model_validator(mode="after")
    def _exactly_one_identifier(self) -> PortalRequest:
        if bool(self.email) == bool(self.api_key):
            raise ValueError("Provide exactly one of 'email' or 'api_key'.")
        return self


class PortalResponse(BaseModel):
    portal_url: str = Field(..., description="Redirect the customer's browser here to manage their subscription.")


__all__ = [
    "CheckoutRequest",
    "CheckoutResponse",
    "PortalRequest",
    "PortalResponse",
    "SessionKeyResponse",
]
