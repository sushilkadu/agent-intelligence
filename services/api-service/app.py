"""api-service: public + paid lookup API.

Phase 3: real public, unauthenticated, read-only lookup endpoints
(`GET /v1/domains/{domain}`, `GET /v1/domains/{domain}/history`) plus a
free-tier per-IP rate limiter.

Phase 4: optional `X-API-Key` auth on those same two routes (still
free-tier/IP-limited with no key, key-tier-limited with one -- see
`api/auth.py`), a paid-tier-only bulk lookup route
(`POST /v1/domains/bulk`), and a key-usage route (`GET /v1/keys/me`).
API keys themselves are issued by billing-service, not here -- this
service only ever reads `api_keys` (see `api/db.py`'s
`fetch_api_key_by_hash`).

See `api/routes.py` for the endpoints, `api/db.py`/`api/history.py` for
how they read Postgres/S3, and `api/ratelimit.py` for the rate limiter.

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
# Phase 3 shipped this permissive for the two unauthenticated GET
# routes and flagged "reconsider once API keys/billing are involved"
# for whoever picked up Phase 4. Having now added API-key auth and a
# paid POST route, the reconsideration is: keep it permissive, `*`
# included -- but for a reason specific to bearer-token auth, not
# because it stopped mattering.
#
# CORS exists to stop a malicious page from riding a VICTIM'S AMBIENT
# credentials (cookies, browser-managed HTTP auth) to a third-party API
# without the victim's knowledge -- that's what "credentialed
# cross-origin request" means, and it's exactly what CORS's
# `allow_credentials`/origin-allowlist machinery is built to gate.
# Nothing in this API is ambient: there are no cookies, no sessions, and
# the browser never attaches `X-API-Key` on its own. A caller's JS has
# to already possess the key and set the header itself for any request
# (same-origin or cross-origin) to succeed at all -- so a malicious
# third-party page embedding this API can only ever act with a key IT
# already has, never one belonging to some other site's visitor. That's
# the textbook case (bearer-token/API-key auth, unlike cookie auth) where
# permissive CORS carries none of the risk it exists to prevent -- see
# the Phase 4 build plan's own framing of this same point.
# `allow_credentials` stays at its default (False): this API is never
# meant to be called WITH cookies, so there's no reason to opt into
# the one CORS mode that would actually reintroduce ambient-credential
# risk.
#
# `allow_methods` adds POST for `POST /v1/domains/bulk`; `allow_headers`
# was already `"*"`, which covers the new `X-API-Key` header without a
# change.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
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
