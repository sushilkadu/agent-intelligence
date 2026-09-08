"""Shared utility helpers for Agent Intelligence Python services.

Phase 0: placeholder only, to prove this package is importable from
every service. Real logging/config/retry helpers land in a later phase.
"""

from .logging import get_logger

__all__ = ["get_logger"]
