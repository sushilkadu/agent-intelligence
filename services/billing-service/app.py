"""billing-service: API key issuance + plan tier / rate limit management.

Phase 0: health check only.

Phase 4: real Stripe Checkout + webhook-driven key issuance/lifecycle,
plus the customer portal. See `billing/routes.py` for the endpoints,
`billing/db.py` for its (read+write) Postgres access, and
`billing/stripe_client.py` for exactly which Stripe SDK calls are real
vs. mocked in this environment's tests.

Same "one FastAPI app, run two ways" shape as api-service's `app.py`:
`uvicorn app:app` / `TestClient` locally, `handler` (Mangum) in Lambda.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mangum import Mangum

from billing.config import CORS_ALLOWED_ORIGINS
from billing.routes import router

app = FastAPI(title="Agent Intelligence Billing Service")

# --- CORS ----------------------------------------------------------------
#
# Scoped to known frontend origins, NOT "*" -- see billing/config.py's
# `CORS_ALLOWED_ORIGINS` docstring for why this service's calculus
# differs from api-service's permissive-by-design public GET routes.
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(router)


@app.exception_handler(HTTPException)
async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    """Same flat `{"error": ..., "message": ...}` error shape as
    api-service (see its `app.py`) instead of FastAPI's default
    `{"detail": ...}` wrapping.
    """
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# Lambda entrypoint (Terraform: `handler = "app.handler"`).
handler = Mangum(app)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
