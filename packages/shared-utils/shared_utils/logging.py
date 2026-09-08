"""Placeholder logging helper, shared across services.

Just enough to prove shared-utils is installable/importable from every
service. Structured logging / log shipping config is future work.
"""

import logging


def get_logger(name: str) -> logging.Logger:
    """Return a standard library logger configured with a sane default format."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
