"""notifier-service: fires webhook notifications to monitors when a
watched domain's agent-identity signals change.

Phase 0: no notification logic yet -- this will eventually be
Lambda-triggered (e.g. off an SQS queue of domain-change events). For now
it just proves the service is wired up: importable shared-schema/
shared-utils, and a health_check() entrypoint.
"""

from shared_schema import Monitor  # noqa: F401  (proves shared-schema wiring works)
from shared_utils import get_logger

logger = get_logger("notifier-service")


def health_check() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    result = health_check()
    logger.info("notifier-service health check: %s", result)
    print(result)
