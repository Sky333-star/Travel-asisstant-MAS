"""Logging configuration.

Console format in development (readable), JSON in production (parseable).
Both carry the same fields, so a log line means the same thing either way.

Noisy third-party loggers are quietened deliberately: httpx logs every request
at INFO, which at one MCP call per second drowns out the agent's own reasoning
trail -- the thing you actually want to read when debugging a plan.
"""

from __future__ import annotations

import logging
import sys
from datetime import UTC
from typing import Any, ClassVar

from .config import settings

_NOISY_LOGGERS = (
    "httpx",
    "httpcore",
    "urllib3",
    "asyncio",
    "watchfiles",
    "opentelemetry.exporter.otlp.proto.http.trace_exporter",
    "langgraph.checkpoint",
    "mcp.server.lowlevel.server",
)


class _ConsoleFormatter(logging.Formatter):
    """Compact, aligned console output with the module path kept short."""

    COLOURS: ClassVar[dict[str, str]] = {
        "DEBUG": "\033[36m", "INFO": "\033[32m", "WARNING": "\033[33m",
        "ERROR": "\033[31m", "CRITICAL": "\033[35m",
    }
    RESET = "\033[0m"

    def __init__(self, use_colour: bool = True) -> None:
        super().__init__(datefmt="%H:%M:%S")
        self.use_colour = use_colour

    def format(self, record: logging.LogRecord) -> str:
        name = record.name
        if name.startswith("app."):
            name = name[4:]
        level = record.levelname
        if self.use_colour:
            colour = self.COLOURS.get(level, "")
            level = f"{colour}{level:<7}{self.RESET}"
        else:
            level = f"{level:<7}"

        message = record.getMessage()
        line = f"{self.formatTime(record, self.datefmt)} {level} {name:<28} {message}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


class _JsonFormatter(logging.Formatter):
    """One JSON object per line, for log shippers."""

    def format(self, record: logging.LogRecord) -> str:
        import json
        from datetime import datetime

        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=UTC
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        # Attach any extra=... fields the call site provided.
        standard = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)
        standard.update({"message", "asctime", "taskName"})
        for key, value in record.__dict__.items():
            if key not in standard and not key.startswith("_"):
                payload[key] = value

        return json.dumps(payload, default=str)


def setup_logging() -> None:
    """Install the configured formatter on the root logger."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    if settings.log_format == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(_ConsoleFormatter(use_colour=sys.stdout.isatty()))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(max(level, logging.WARNING))

    logging.getLogger("app").setLevel(level)
