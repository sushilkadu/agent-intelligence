"""api-service: public + paid lookup API.

Phase 3 scope: read-only public endpoints over the `domains` table
(populated by parser-service) and the crawler's raw S3 history, plus a
Lambda-level per-IP rate limiter for the free tier. See `api/routes.py`
for the endpoints and `../app.py` for how they're wired into the
FastAPI app (and, via Mangum, into Lambda).
"""
