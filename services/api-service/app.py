"""api-service: public + paid lookup API.

Phase 3: real public, unauthenticated, read-only lookup endpoints
(`GET /v1/domains/{domain}`, `GET /v1/domains/{domain}/history`) plus a
free-tier per-IP rate limiter. See `api/routes.py` for the endpoints,
`api/db.py`/`api/history.py` for how they read Postgres/S3, and
`api/ratelimit.py` for the rate limiter.

This one FastAPI app is the single implementation of the route logic,
run two ways:
  * locally / in tests: `uvicorn app:app` (or FastAPI's `TestClient`).
  * in Lambda: `handler` below wraps the same `app` with Mangum, the
    standard ASGI-on-Lambda adapter, so API Gateway (HTTP API) events
    drive the exact same code path as a local `uvicorn` request --
    nothing about the route logic is forked between the two.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mangum import Mangum
from shared_schema import Domain  # noqa: F401  (proves shared-schema wiring works)

from api.routes import router

app = FastAPI(title="Agent Intelligence API Service")

# --- CORS ----------------------------------------------------------------
#
# Permissive by design for these two routes specifically: this is a
# public, unauthenticated, read-only GET lookup API with no cookies,
# sessions, or credentials involved anywhere in the request -- there is
# no cross-site state for a permissive CORS policy to leak or let
# anyone else act on behalf of a user. Any site should be able to embed
# a "check this domain" widget against it directly from the browser.
# `allow_credentials` is left at its default (False) since nothing here
# uses cookies/auth headers.
#
# This is NOT the policy Phase 4's authenticated/paid endpoints should
# reuse -- once API keys/billing are involved, CORS needs to be scoped
# to known frontend origins, not "*". Flagged again in the Phase 3
# report for whoever picks up Phase 4.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(router)


@app.exception_handler(HTTPException)
async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    """Return `HTTPException.detail` as the response body directly when
    it's already a structured dict (every error raised in `api/routes.py`
    passes a `{"error": ..., "message": ...}` dict), instead of FastAPI's
    default `{"detail": ...}` wrapping -- gives API consumers one flat,
    predictable error shape for both 404s and 429s.
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

    uvicorn.run(app, host="0.0.0.0", port=8000)
