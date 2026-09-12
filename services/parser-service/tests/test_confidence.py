from __future__ import annotations

from parser.confidence import (
    EXPIRED_KEY,
    MALFORMED_MANIFEST,
    MALFORMED_WEB_BOT_AUTH,
    NO_SIGNALS,
    compute_confidence_flags,
)


def test_both_signals_valid_no_flags():
    flags = compute_confidence_flags(
        agents_json_present=True,
        manifest_malformed=False,
        web_bot_auth_present=True,
        web_bot_auth_malformed=False,
        web_bot_auth_valid=True,
    )
    assert flags == []


def test_neither_signal_present_is_no_signals_only():
    flags = compute_confidence_flags(
        agents_json_present=False,
        manifest_malformed=False,
        web_bot_auth_present=False,
        web_bot_auth_malformed=False,
        web_bot_auth_valid=False,
        llms_txt_present=False,
    )
    assert flags == [NO_SIGNALS]


def test_llms_txt_present_alone_is_not_no_signals():
    """Regression test: llms.txt was added to the crawler/schema in
    Phase 5 but never threaded through this function, so a domain with
    ONLY llms.txt present (e.g. github.com in real local testing) was
    incorrectly flagged `no_signals`.
    """
    flags = compute_confidence_flags(
        agents_json_present=False,
        manifest_malformed=False,
        web_bot_auth_present=False,
        web_bot_auth_malformed=False,
        web_bot_auth_valid=False,
        llms_txt_present=True,
    )
    assert flags == []


def test_malformed_manifest_present():
    flags = compute_confidence_flags(
        agents_json_present=True,
        manifest_malformed=True,
        web_bot_auth_present=False,
        web_bot_auth_malformed=False,
        web_bot_auth_valid=False,
    )
    # agents.json was present (malformed), web_bot_auth was never
    # fetched -- no_signals must NOT also appear alongside a signal
    # that *was* present.
    assert flags == [MALFORMED_MANIFEST]


def test_malformed_web_bot_auth_present():
    flags = compute_confidence_flags(
        agents_json_present=False,
        manifest_malformed=False,
        web_bot_auth_present=True,
        web_bot_auth_malformed=True,
        web_bot_auth_valid=False,
    )
    assert flags == [MALFORMED_WEB_BOT_AUTH]


def test_expired_key_flag():
    flags = compute_confidence_flags(
        agents_json_present=True,
        manifest_malformed=False,
        web_bot_auth_present=True,
        web_bot_auth_malformed=False,
        web_bot_auth_valid=False,
    )
    assert flags == [EXPIRED_KEY]


def test_malformed_manifest_and_expired_key_both_reported():
    flags = compute_confidence_flags(
        agents_json_present=True,
        manifest_malformed=True,
        web_bot_auth_present=True,
        web_bot_auth_malformed=False,
        web_bot_auth_valid=False,
    )
    assert set(flags) == {MALFORMED_MANIFEST, EXPIRED_KEY}
