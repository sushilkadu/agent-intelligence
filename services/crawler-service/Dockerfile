# Build context MUST be the repo root (see docker-compose.yml), not
# this directory -- requirements.txt below declares
# `-e ../../packages/shared-schema` / `-e ../../packages/shared-utils`,
# which pip resolves relative to the CURRENT WORKING DIRECTORY at
# install time (already documented from earlier phases' local-dev
# instructions), not relative to requirements.txt's own location. That
# only resolves correctly if `packages/` is copied into the image at
# the same relative position to `services/crawler-service` as it has
# in the repo, and WORKDIR is set to this service's own directory
# before `pip install` runs -- exactly mirroring the local-dev
# convention (`cd services/crawler-service && pip install -r
# requirements.txt`).
#
# python:3.11-slim, not the 3.9 every local dev/testing venv in this
# repo has used so far -- this is deliberately the first environment
# that exercises the real python3.11 Lambda runtime target (see
# infra/terraform/envs/dev/main.tf's `runtime = "python3.11"`).
FROM python:3.11-slim

WORKDIR /app
COPY packages /app/packages
COPY services/crawler-service /app/services/crawler-service

WORKDIR /app/services/crawler-service
RUN pip install --no-cache-dir -r requirements.txt

# crawler-service has no long-running Lambda-triggered process
# locally -- crawl-queue triggers `crawler.handler.lambda_handler` via
# a real SQS event source mapping in production. This worker script is
# local-only infra glue that polls the queue itself and calls the same
# unchanged lambda_handler -- see scripts/run_sqs_worker.py's docstring.
CMD ["python", "scripts/run_sqs_worker.py"]
