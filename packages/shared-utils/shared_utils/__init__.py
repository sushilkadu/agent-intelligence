"""Shared utility helpers for Agent Intelligence Python services.

Phase 0: placeholder only, to prove this package is importable from
every service. Real logging/config/retry helpers land in a later phase.

Phase 4: adds `api_keys` -- the API key generation/hashing scheme
shared between api-service (verifies) and billing-service (issues).
See `shared_utils/api_keys.py`'s docstring for why this one lives here
instead of being duplicated per-service.
"""

from .api_keys import (
    API_KEY_HEADER,
    API_KEY_PREFIX,
    generate_api_key_secret,
    hash_api_key_secret,
)
from .logging import get_logger

__all__ = [
    "API_KEY_HEADER",
    "API_KEY_PREFIX",
    "generate_api_key_secret",
    "get_logger",
    "hash_api_key_secret",
]
