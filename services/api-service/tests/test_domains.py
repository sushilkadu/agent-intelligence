"""GET /v1/domains/{domain}: found vs. not-yet-crawled (404), never a
500 for the "unknown domain" case. The DB layer is faked out (mirrors
parser-service's approach of faking `fetch_domain`/`upsert_domain` in
handler-level tests, see parser-service/tests/fakes.py) so this suite
never touches real Postgres -- that's proven separately by the
localstack/local-Postgres verification run.
"""

from __future__ import annotations

from datetime import datetime, timezone

from api import routes

NOW = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)


def _sample_row(domain: str) -> dict:
    return {
        "domain": domain,
        "first_seen_at": NOW,
        "last_crawled_at": NOW,
        "agent_json_present": True,
        "agent_json_s3_key": f"{domain}/2026-09-09T12:00:00+00:00/agents.json",
        "llms_txt_present": False,
        "llms_txt_s3_key": None,
        "web_bot_auth_present": True,
        "web_bot_auth_key_id": "key-1",
        "web_bot_auth_valid": True,
        "web_bot_auth_expiry": NOW,
        "declared_capabilities": {"agents": [{"name": "demo-bot"}]},
        "confidence_flags": [],
        "on_chain_ref": None,
        "created_at": NOW,
        "updated_at": NOW,
    }


def test_domain_found_returns_normalized_record(client_no_rate_limit, monkeypatch):
    row = _sample_row("example.com")
    monkeypatch.setattr(routes, "fetch_domain", lambda _conn, domain: row if domain == "example.com" else None)

    response = client_no_rate_limit.get("/v1/domains/example.com")

    assert response.status_code == 200
    body = response.json()
    assert body["domain"] == "example.com"
    assert body["agent_json_present"] is True
    assert body["web_bot_auth_valid"] is True
    assert body["confidence_flags"] == []
    assert body["declared_capabilities"] == {"agents": [{"name": "demo-bot"}]}


def test_domain_not_found_returns_clean_404_not_500(client_no_rate_limit, monkeypatch):
    monkeypatch.setattr(routes, "fetch_domain", lambda _conn, _domain: None)

    response = client_no_rate_limit.get("/v1/domains/never-crawled.example")

    assert response.status_code == 404
    body = response.json()
    assert body["error"] == "domain_not_found"
    assert "never-crawled.example" in body["message"]
