#!/usr/bin/env python3
"""Local-only periodic trigger for scheduler-service's Lambda handler.

In production, `scheduler.handler.lambda_handler` is invoked by an
EventBridge scheduled rule running HOURLY (see
infra/terraform/envs/dev/main.tf's `scheduler_rule`/`scheduler_lambda`
and `scheduler/handler.py`'s own module docstring on why hourly).
There is no EventBridge locally, so this script calls the exact same
unchanged `lambda_handler` on a loop, sleeping
`SCHEDULER_INTERVAL_SECONDS` between runs.

*** SCHEDULER_INTERVAL_SECONDS defaults to 90 seconds. This is a
LOCAL-TESTING-ONLY cadence, chosen so a schedule run is actually
observable within a short manual testing session -- it is NOT a
stand-in for, or approximation of, the real hourly production
EventBridge schedule. Do not change this default to "match production"
-- if anything, production stays hourly regardless of what this local
script does. ***

This is docker-compose-only infrastructure glue, not new business
logic: `scheduler/handler.py` is unmodified.

Run via: `python scripts/run_periodic.py` (see the service's
Dockerfile -- this is its container CMD).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared_utils import get_logger

from scheduler.handler import lambda_handler

logger = get_logger("scheduler-service.worker")

INTERVAL_SECONDS = float(os.environ.get("SCHEDULER_INTERVAL_SECONDS", "90"))


def main() -> int:
    logger.info(
        "scheduler-service periodic worker starting -- running lambda_handler every %.0fs "
        "(LOCAL TESTING CADENCE ONLY; real production deployment runs hourly via EventBridge, "
        "see infra/terraform/envs/dev/main.tf's scheduler_rule)",
        INTERVAL_SECONDS,
    )
    while True:
        try:
            result = lambda_handler(None, None)
            logger.info("scheduler run result: %s", result)
        except Exception:
            logger.exception("scheduler lambda_handler raised; will retry next interval")
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
