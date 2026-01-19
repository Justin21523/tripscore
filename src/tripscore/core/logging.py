"""
Logging configuration.

We use a YAML logging config (`src/tripscore/config/logging.yaml`) and then apply
runtime overrides from settings (e.g., `TRIPSCORE_LOG_LEVEL`).
"""

from __future__ import annotations

import logging.config

from tripscore.config.settings import get_logging_config, get_settings


def configure_logging() -> None:
    """Configure the Python logging system based on packaged YAML config + settings."""
    settings = get_settings()
    config = get_logging_config()

    level = settings.app.log_level.upper()
    config.setdefault("root", {})["level"] = level
    for handler in config.get("handlers", {}).values():
        if isinstance(handler, dict) and "level" in handler:
            handler["level"] = level

    logging.config.dictConfig(config)
