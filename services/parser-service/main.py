"""parser-service: normalizes raw crawled artifacts (agents.json, the Web
Bot Auth JWKS directory) into the canonical Domain schema.

Phase 2: real parsing/normalization/upsert logic lives in `parser/`
(mirroring crawler-service's `crawler/` package layout). The actual
Lambda entrypoint Terraform points at is `parser.handler.lambda_handler`
directly -- same convention as crawler-service, whose Lambda handler is
`crawler.handler.lambda_handler` rather than routed through this file.
This module stays a thin health-check/import-sanity stub, as it was in
Phase 0.
"""

from shared_schema import Domain  # noqa: F401  (proves shared-schema wiring works)
from shared_utils import get_logger

logger = get_logger("parser-service")


def health_check() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    result = health_check()
    logger.info("parser-service health check: %s", result)
    print(result)
