# Build context MUST be the repo root -- see
# services/crawler-service/Dockerfile's comment for the full
# `-e ../../packages/...` relative-path rationale, identical here.
FROM python:3.11-slim

WORKDIR /app
COPY packages /app/packages
COPY services/parser-service /app/services/parser-service

WORKDIR /app/services/parser-service
RUN pip install --no-cache-dir -r requirements.txt

# raw-fetched triggers `parser.handler.lambda_handler` via a real SQS
# event source mapping in production; this worker script is local-only
# infra glue -- see scripts/run_sqs_worker.py's docstring.
CMD ["python", "scripts/run_sqs_worker.py"]
