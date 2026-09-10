# Build context MUST be the repo root -- see
# services/crawler-service/Dockerfile's comment for the full
# `-e ../../packages/...` relative-path rationale, identical here.
FROM python:3.11-slim

WORKDIR /app
COPY packages /app/packages
COPY services/scheduler-service /app/services/scheduler-service

WORKDIR /app/services/scheduler-service
RUN pip install --no-cache-dir -r requirements.txt

# scheduler.handler.lambda_handler is EventBridge-cron-triggered
# (hourly) in production; this loop script is local-only infra glue on
# a much shorter, clearly-documented local-testing cadence -- see
# scripts/run_periodic.py's docstring.
CMD ["python", "scripts/run_periodic.py"]
