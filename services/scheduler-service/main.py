"""scheduler-service: decides which domains are due for a re-crawl and
enqueues them for crawler-service.

Phase 0 shipped this as a health-check-only stub. Phase 5 adds the real
logic in the `scheduler/` package -- see `scheduler/handler.py` for the
actual Lambda entrypoint (`scheduler.handler.lambda_handler`,
EventBridge-triggered), which Terraform points at (see
infra/terraform/envs/dev/main.tf's `scheduler_lambda`/`scheduler_rule`).
This module is kept as-is for the health_check() proof-of-wiring
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
