"""Process-wide logging setup."""

import logging

from ai_personal_knowledge_assistant.core.settings import LogLevel

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def configure_logging(level: LogLevel) -> None:
    """Send log records at `level` and above to stderr.

    Safe to call more than once: it replaces the root handlers it set before.
    """
    logging.basicConfig(level=level, format=LOG_FORMAT, force=True)
