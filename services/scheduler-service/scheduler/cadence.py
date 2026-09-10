"""Pure cadence-decision logic: given the set of active plan tiers
watching a domain (via its monitors) and when it was last crawled, is
it due for a recrawl right now?

Kept dependency-free (no DB, no boto3, no datetime.now() called
internally) specifically so this is trivial to unit test directly --
see tests/test_cadence.py -- rather than only provable by running the
whole Lambda handler end to end.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .config import DEFAULT_CADENCE_DAYS, LICENSING_CADENCE_DAYS, SELF_SERVE_CADENCE_DAYS

LICENSING_TIER = "licensing"
SELF_SERVE_TIER = "self_serve"


def cadence_days_for_tiers(tiers: set[str]) -> int:
    """Return the recrawl cadence, in days, for a domain watched by
    monitors owned by keys on the given set of plan tiers (`tiers` may
    be empty -- a domain with no monitors at all).

    "Highest tier wins" -- see config.py's docstring for the full
    rationale: a `licensing`-tier watcher gets the fastest cadence
    regardless of who else (if anyone) is also watching at a lower
    tier; `self_serve` gets the middle cadence; no paid-tier watcher at
    all gets the slowest, default cadence.
    """
    if LICENSING_TIER in tiers:
        return LICENSING_CADENCE_DAYS
    if SELF_SERVE_TIER in tiers:
        return SELF_SERVE_CADENCE_DAYS
    return DEFAULT_CADENCE_DAYS


def is_due(last_crawled_at: datetime | None, cadence_days: int, *, now: datetime) -> bool:
    """A domain is due for recrawl if it has never been crawled at all,
    or if at least `cadence_days` have elapsed since its last crawl.
    """
    if last_crawled_at is None:
        return True
    return now - last_crawled_at >= timedelta(days=cadence_days)


def is_domain_due(tiers: set[str], last_crawled_at: datetime | None, *, now: datetime) -> bool:
    """Convenience wrapper combining `cadence_days_for_tiers` +
    `is_due` -- what `scheduler/handler.py` calls per domain.
    """
    return is_due(last_crawled_at, cadence_days_for_tiers(tiers), now=now)


__all__ = ["cadence_days_for_tiers", "is_domain_due", "is_due"]
