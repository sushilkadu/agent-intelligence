# Build context MUST be the repo root -- see
# services/crawler-service/Dockerfile's comment for the full
# `-e ../../packages/...` relative-path rationale, identical here.
FROM python:3.11-slim

WORKDIR /app
COPY packages /app/packages
COPY services/billing-service /app/services/billing-service

WORKDIR /app/services/billing-service
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8001

# Same FastAPI app object billing-service's real Lambda deployment
# wraps with Mangum (see app.py) -- run directly with uvicorn for
# local testing.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
