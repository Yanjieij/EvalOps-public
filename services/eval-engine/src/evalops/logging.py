"""Structured logging setup built on structlog.

Every log line carries `run_id`, `case_id`, and `request_id` when they are
available in the current contextvar scope. That gives us grep-friendly
correlation across async tasks without having to thread loggers manually.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar

import structlog

_run_id_var: ContextVar[str | None] = ContextVar("run_id", default=None)
_case_id_var: ContextVar[str | None] = ContextVar("case_id", default=None)
_request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def bind_run(run_id: str) -> None:
    _run_id_var.set(run_id)


def bind_case(case_id: str) -> None:
    _case_id_var.set(case_id)


def bind_request(request_id: str) -> None:
    _request_id_var.set(request_id)


def _inject_context(
    _logger: object, _name: str, event_dict: dict[str, object]
) -> dict[str, object]:
    if (rid := _run_id_var.get()) is not None:
        event_dict.setdefault("run_id", rid)
    if (cid := _case_id_var.get()) is not None:
        event_dict.setdefault("case_id", cid)
    if (req := _request_id_var.get()) is not None:
        event_dict.setdefault("request_id", req)
    return event_dict


def configure_logging(level: str = "INFO", json_output: bool = False) -> None:
    """Set up stdlib + structlog so `structlog.get_logger()` Just Works."""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=getattr(logging, level.upper(), logging.INFO),
    )
    processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _inject_context,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if json_output:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=True))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[return-value]
