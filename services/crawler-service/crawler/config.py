"""Configuration constants for crawler-service.

Centralizes anything that's likely to change later (the Web Bot Auth
well-known path is still in an active IETF draft, timeouts, env var
names) so there's exactly one place to update it.
"""

from __future__ import annotations

import os

# --- Well-known signal paths -------------------------------------------------

# Web Bot Auth "signature-agent-card" discovery location. Per the
# current IETF drafts (draft-meunier-web-bot-auth-architecture,
# draft-meunier-http-message-signatures-directory,
# draft-meunier-webbotauth-registry) this is served as a JWKS document
# containing the bot/agent's public signing keys:
#   https://datatracker.ietf.org/doc/html/draft-meunier-web-bot-auth-architecture
#   https://datatracker.ietf.org/doc/html/draft-meunier-http-message-signatures-directory
#   https://datatracker.ietf.org/doc/draft-meunier-webbotauth-registry/
#
# The draft is still moving -- this is deliberately kept as the single
# named constant used everywhere the path is needed. Do not hardcode
# the literal string anywhere else; update it here if the draft's
# well-known path changes.
WEB_BOT_AUTH_WELL_KNOWN_PATH = "/.well-known/http-message-signatures-directory"

AGENTS_JSON_PATH = "/agents.json"

# --- HTTP fetch behavior ------------------------------------------------------

DEFAULT_TIMEOUT_SECONDS = float(os.environ.get("CRAWLER_TIMEOUT_SECONDS", "10"))

# Overrides the URL scheme used to build fetch URLs. Defaults to
# "https" (real crawling). Local dev / test tooling can set this to
# "http" to point the crawl logic at a local mock HTTP server instead
# of doing a real TLS handshake -- see scripts/verify_localstack_e2e.py.
URL_SCHEME = os.environ.get("CRAWLER_URL_SCHEME", "https")

# --- AWS resource configuration ------------------------------------------------
#
# `AWS_ENDPOINT_URL` is the standard boto3/botocore env var: when set
# (locally, to LocalStack's endpoint), every boto3 client built by this
# service targets it; when unset (real Lambda / real AWS), boto3
# resolves the real regional endpoints. This module only *reads* that
# env var -- the literal LocalStack URL (http://localhost:4566) lives
# in local dev config/scripts (docker-compose, .env files, the
# verification script), never here.

AWS_REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))

RAW_DATA_BUCKET_NAME = os.environ.get("RAW_DATA_BUCKET_NAME", "agent-intel-raw-crawl-dev")
CRAWL_QUEUE_URL = os.environ.get("CRAWL_QUEUE_URL", "")
RAW_FETCHED_QUEUE_URL = os.environ.get("RAW_FETCHED_QUEUE_URL", "")


def boto3_client_kwargs() -> dict:
    """Common kwargs for every boto3 client this service creates.

    Centralized so every call site (storage.py, messaging.py, the seed
    loader script) picks up `AWS_ENDPOINT_URL` / `AWS_REGION` the same
    way instead of duplicating the same three lines.
    """
    kwargs: dict = {"region_name": AWS_REGION}
    endpoint_url = os.environ.get("AWS_ENDPOINT_URL")
    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url
    return kwargs
