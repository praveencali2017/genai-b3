"""Process-wide logging setup."""

import logging

from ai_travel_agent.config.settings import LogLevel

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def configure_logging(level: LogLevel) -> None:
    """Send log records at `level` and above to stderr; safe to call twice."""
    logging.basicConfig(level=level, format=LOG_FORMAT, force=True)
