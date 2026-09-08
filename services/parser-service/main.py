"""parser-service: normalizes raw crawled artifacts (agents.json, llms.txt,
Web Bot Auth signature cards) into the canonical Domain schema.

Phase 0: no parsing logic yet -- this will eventually be Lambda-triggered
(e.g. off an SQS queue of raw crawl results). For now it just proves the
service is wired up: importable shared-schema/shared-utils, and a
health_check() entrypoint.
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
