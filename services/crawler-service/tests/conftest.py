"""Shared test fixtures.

Unit tests must never touch real AWS or a real LocalStack instance --
S3/SQS calls are mocked via `moto`. This autouse fixture makes sure no
inherited `AWS_ENDPOINT_URL` from the developer's shell (e.g. left over
from pointing at LocalStack) leaks into the mocked tests, and supplies
dummy credentials so boto3 never tries to resolve real ones.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_aws_env(monkeypatch):
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    yield
