# Build context MUST be the repo root -- see
# services/crawler-service/Dockerfile's comment for the full
# `-e ../../packages/...` relative-path rationale, identical here.
FROM python:3.11-slim

WORKDIR /app
COPY packages /app/packages
COPY services/api-service /app/services/api-service

WORKDIR /app/services/api-service
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8000

# Same FastAPI app object api-service's real Lambda deployment wraps
# with Mangum (see app.py) -- run directly with uvicorn for local
# testing, exactly like the "Running locally" README instructions.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
