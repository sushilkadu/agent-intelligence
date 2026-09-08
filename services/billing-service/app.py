"""billing-service: API key issuance + plan tier / rate limit management.

Phase 0: health check only. Real billing/key-issuance logic lands in a
later phase.
"""

from fastapi import FastAPI

from shared_schema import ApiKey  # noqa: F401  (proves shared-schema wiring works)

app = FastAPI(title="Agent Intelligence Billing Service")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
