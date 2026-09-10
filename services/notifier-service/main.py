"""notifier-service: fires webhook notifications to monitors when a
watched domain's agent-identity signals change.

Phase 0 shipped this as a health-check-only stub. Phase 5 adds the real
logic in the `notifier/` package -- see `notifier/handler.py` for the
actual Lambda entrypoint (`notifier.handler.lambda_handler`, SQS-triggered
off `notify-queue`), which Terraform points at (see
infra/terraform/envs/dev/main.tf's `notifier_lambda`). This module is
kept as-is for the health_check() proof-of-wiring entrypoint.
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
