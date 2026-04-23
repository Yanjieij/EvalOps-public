"""OpenTelemetry tracing for the eval-engine.

We expose a thin wrapper around `opentelemetry.trace`:

- ``configure_tracing()`` installs the TracerProvider + OTLP HTTP
  exporter iff ``EVALOPS_OTEL_EXPORTER_ENDPOINT`` is set. When the env
  var is empty (dev, CI, offline smoke), we fall back to the no-op
  tracer that OTel ships with by default — calls to ``run_span`` /
  ``case_span`` still work, they just don't export.
- ``run_span`` and ``case_span`` are context managers used by the
  runner. Keeping the span creation here (rather than scattering
  ``get_tracer(__name__).start_as_current_span(...)`` in the runner)
  means there's one place to add attributes when Week 4's Release
  Gate starts caring about span semantics.

We deliberately don't import the OTel SDK at module import time. That
keeps the test suite fast (OTel has a non-trivial init cost) and lets
the CLI continue to run in OTel-less environments without even a
warning log line.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator
from typing import Any

# `opentelemetry.trace` is always safe to import — it's tiny and sets
# up a no-op tracer by default.
from opentelemetry import trace
from opentelemetry.trace import Tracer

_log = logging.getLogger(__name__)

_configured: bool = False
_service_name: str = "evalops-eval-engine"


def configure_tracing(
    *,
    service_name: str = "evalops-eval-engine",
    endpoint: str = "",
    headers: dict[str, str] | None = None,
) -> bool:
    """Install a global TracerProvider + OTLP exporter.

    Returns ``True`` when an exporter was wired up, ``False`` when we
    left the OTel no-op defaults in place.
    """
    global _configured, _service_name
    _service_name = service_name

    if not endpoint:
        return False

    if _configured:
        return True

    # SDK imports are deferred so no-op mode doesn't pay for them.
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:  # pragma: no cover — optional dep
        _log.warning("otel.sdk_import_failed: %s", exc)
        return False

    provider = TracerProvider(
        resource=Resource.create({"service.name": service_name})
    )
    exporter = OTLPSpanExporter(endpoint=endpoint, headers=headers or {})
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _configured = True
    _log.info("otel.configured: endpoint=%s", endpoint)
    return True


def get_tracer(name: str | None = None) -> Tracer:
    return trace.get_tracer(name or _service_name)


@contextlib.contextmanager
def run_span(
    *,
    run_id: str,
    benchmark: str,
    sut: str,
    judge: str,
    concurrency: int,
) -> Iterator[Any]:
    """Span that wraps a full run.

    Attributes match the Prometheus label set so Tempo / Jaeger
    traces can be joined against Prometheus time series on the
    dashboard.
    """
    tracer = get_tracer()
    with tracer.start_as_current_span("evalops.run") as span:
        if span.is_recording():
            span.set_attribute("evalops.run_id", run_id)
            span.set_attribute("evalops.benchmark", benchmark)
            span.set_attribute("evalops.sut", sut)
            span.set_attribute("evalops.judge", judge)
            span.set_attribute("evalops.concurrency", concurrency)
        yield span


@contextlib.contextmanager
def case_span(
    *,
    run_id: str,
    case_id: str,
    kind: str,
    benchmark: str,
    sut: str,
) -> Iterator[Any]:
    """Span around a single case execution.

    Sits as a child of the current run span by virtue of
    ``start_as_current_span`` reading the active context.
    """
    tracer = get_tracer()
    with tracer.start_as_current_span("evalops.case") as span:
        if span.is_recording():
            span.set_attribute("evalops.run_id", run_id)
            span.set_attribute("evalops.case_id", case_id)
            span.set_attribute("evalops.case_kind", kind)
            span.set_attribute("evalops.benchmark", benchmark)
            span.set_attribute("evalops.sut", sut)
        yield span
