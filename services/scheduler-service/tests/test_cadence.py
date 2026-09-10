"""Unit tests for scheduler.cadence -- the pure tiered cadence-decision
logic, tested directly (no DB, no boto3, no Lambda event) per the
build's own verification requirement: a domain with a licensing-tier
monitor is due daily, one with only a self-serve monitor is due on a
different (middle) cadence, and one with no paid-tier monitor at all
falls back to the weekly default -- plus the boundary itself (exactly
at the cadence threshold, and just under it).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scheduler.cadence import cadence_days_for_tiers, is_domain_due, is_due

NOW = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)


# --- cadence_days_for_tiers: tier -> cadence mapping -------------------------


def test_licensing_tier_gets_daily_cadence():
    assert cadence_days_for_tiers({"licensing"}) == 1


def test_self_serve_tier_gets_the_middle_cadence():
    assert cadence_days_for_tiers({"self_serve"}) == 3


def test_no_paid_tier_watcher_gets_the_weekly_default():
    assert cadence_days_for_tiers(set()) == 7


def test_licensing_wins_when_a_domain_has_monitors_on_multiple_tiers():
    """A domain watched by both a self_serve AND a licensing monitor
    gets the faster (licensing) cadence -- see config.py's "highest
    tier wins" rationale.
    """
    assert cadence_days_for_tiers({"self_serve", "licensing"}) == 1


def test_free_tier_present_alongside_others_does_not_affect_the_outcome():
    # free-tier keys can't own monitors in practice (see api-service's
    # MONITOR_ALLOWED_PLAN_TIERS), but this must still degrade sensibly
    # rather than crash if one ever showed up in the tiers set.
    assert cadence_days_for_tiers({"free", "self_serve"}) == 3
    assert cadence_days_for_tiers({"free"}) == 7


# --- is_due: last_crawled_at + cadence -> due/not-due ------------------------


def test_never_crawled_is_always_due():
    assert is_due(None, cadence_days=7, now=NOW) is True


def test_due_exactly_at_the_cadence_boundary():
    last_crawled = NOW - timedelta(days=7)
    assert is_due(last_crawled, cadence_days=7, now=NOW) is True


def test_not_due_just_under_the_cadence_boundary():
    last_crawled = NOW - timedelta(days=7) + timedelta(minutes=1)
    assert is_due(last_crawled, cadence_days=7, now=NOW) is False


def test_due_well_past_the_cadence_boundary():
    last_crawled = NOW - timedelta(days=30)
    assert is_due(last_crawled, cadence_days=1, now=NOW) is True


# --- is_domain_due: the combined per-domain decision -------------------------


def test_domain_with_licensing_monitor_is_due_daily():
    last_crawled = NOW - timedelta(days=1)
    assert is_domain_due({"licensing"}, last_crawled, now=NOW) is True

    just_short = NOW - timedelta(hours=23)
    assert is_domain_due({"licensing"}, just_short, now=NOW) is False


def test_domain_with_only_self_serve_monitor_is_due_on_the_middle_cadence():
    exactly_due = NOW - timedelta(days=3)
    assert is_domain_due({"self_serve"}, exactly_due, now=NOW) is True

    not_yet_due = NOW - timedelta(days=2)
    assert is_domain_due({"self_serve"}, not_yet_due, now=NOW) is False


def test_domain_with_no_monitors_falls_back_to_the_weekly_default():
    exactly_due = NOW - timedelta(days=7)
    assert is_domain_due(set(), exactly_due, now=NOW) is True

    not_yet_due = NOW - timedelta(days=6)
    assert is_domain_due(set(), not_yet_due, now=NOW) is False


def test_domain_with_no_monitors_and_never_crawled_is_due():
    assert is_domain_due(set(), None, now=NOW) is True
