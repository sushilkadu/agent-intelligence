"""crawler-service: fetches agent-identity signals (agents.json, llms.txt,
Web Bot Auth signature cards, on-chain registry refs) for a given domain.

Phase 0: no crawling logic yet -- this will eventually be Lambda-triggered
(e.g. off an SQS queue of domains to crawl). For now it just proves the
service is wired up: importable shared-schema/shared-utils, and a
health_check() entrypoint.
"""

from shared_schema import Domain  # noqa: F401  (proves shared-schema wiring works)
from shared_utils import get_logger

logger = get_logger("crawler-service")


def health_check() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    result = health_check()
    logger.info("crawler-service health check: %s", result)
    print(result)
