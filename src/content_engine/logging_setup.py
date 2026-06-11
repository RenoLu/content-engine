"""Minimal, dependency-free logging configuration."""

from __future__ import annotations

import logging
import os
import sys

_CONFIGURED = False


def setup_logging(level: str | None = None) -> None:
    """Configure root logging.

    Handlers are installed once, but an explicitly-passed ``level`` is ALWAYS
    applied — even on later calls. This matters because modules call
    ``get_logger`` at import time (configuring at the default level first), and
    the CLI then calls ``setup_logging(args.log_level)`` to honor ``--log-level``;
    without re-applying, that flag would be silently ignored.
    """
    global _CONFIGURED
    explicit = level is not None
    lvl_name = (level or os.environ.get("LOG_LEVEL") or "INFO").upper()
    lvl = getattr(logging, lvl_name, logging.INFO)
    root = logging.getLogger()

    if not _CONFIGURED:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        root.handlers.clear()
        root.addHandler(handler)
        root.setLevel(lvl)
        _CONFIGURED = True
    elif explicit:
        root.setLevel(lvl)


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger, ensuring logging is configured."""
    setup_logging()
    return logging.getLogger(name)
