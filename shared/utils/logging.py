# shared/utils/logging.py
"""
File Use:
    Provides structured logging setup for the entire platform.
    Uses structlog with JSON output in production, console rendering
    in development (TTY detection).

Implements:
    - setup_logging (configure structlog globally)
    - get_logger (get a component-bound logger)

Depends On:
    - structlog

Used By:
    - All application modules via get_logger()
"""

import logging
import sys
from typing import Any

import structlog


def setup_logging(level: str = "INFO") -> None:
    """Configure structured logging for the application.

    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR).
    """
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer()
            if sys.stderr.isatty()
            else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(component: str, **kwargs: Any) -> structlog.stdlib.BoundLogger:
    """Get a logger bound to a component name.

    Args:
        component: Name of the component (e.g. "ingest_router", "ppe_pipeline").
        **kwargs: Additional key-value pairs to bind to every log entry.

    Returns:
        A structlog bound logger instance.
    """
    return structlog.get_logger(component=component, **kwargs)
