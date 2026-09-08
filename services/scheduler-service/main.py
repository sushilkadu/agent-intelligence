"""scheduler-service: decides which domains are due for a re-crawl and
enqueues them for crawler-service.

Phase 0: no scheduling logic yet -- this will eventually be Lambda-triggered
(e.g. on a periodic EventBridge rule). For now it just proves the service
is wired up: importable shared-schema/shared-utils, and a health_check()
entrypoint.
"""

from shared_schema import Domain  # noqa: F401  (proves shared-schema wiring works)
from shared_utils import get_logger

logger = get_logger("scheduler-service")


def health_check() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    result = health_check()
    logger.info("scheduler-service health check: %s", result)
    print(result)
