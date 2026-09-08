"""api-service: public + paid lookup API.

Phase 0: health check only. Real lookup/query endpoints land in a later
phase once crawler/parser have data to serve.
"""

from fastapi import FastAPI

from shared_schema import Domain  # noqa: F401  (proves shared-schema wiring works)

app = FastAPI(title="Agent Intelligence API Service")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
