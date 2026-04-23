"""Evaluation-engine observability primitives.

Two halves:

- ``metrics`` exposes a process-local Prometheus registry and the
  counters / histograms / gauges the runner updates. Using a local
  registry keeps the EvalOps metrics free of the Python process
  collector's default globals, which is important for shortlived CLI
  runs that would otherwise emit noise on every shutdown.
- ``tracing`` lazily configures an OpenTelemetry tracer whose parent
  span ids flow through into the reference sidecar via the same
  ``X-EvalOps-*`` headers the HTTP adapter already injects, so a Jaeger
  query on ``run_id`` returns the full scheduler → adapter → SUT chain.

Both modules are import-safe even when OTel / Prometheus is not
configured — tracing returns a no-op tracer, metrics always work but
serve nothing until ``start_metrics_server()`` is called.
"""

from __future__ import annotations

from .metrics import (
    CASE_DURATION_SECONDS,
    JUDGE_CALLS_TOTAL,
    JUDGE_COST_MICRO_USD_TOTAL,
    METRICS_REGISTRY,
    RUN_DURATION_SECONDS,
    RUN_JUDGE_AGREEMENT,
    RUN_PASS_RATE,
    RUNS_TOTAL,
    record_case_done,
    record_judge_call,
    record_run_finish,
    record_run_start,
    start_metrics_server,
)
from .tracing import (
    case_span,
    configure_tracing,
    get_tracer,
    run_span,
)

__all__ = [
    "CASE_DURATION_SECONDS",
    "JUDGE_CALLS_TOTAL",
    "JUDGE_COST_MICRO_USD_TOTAL",
    "METRICS_REGISTRY",
    "RUNS_TOTAL",
    "RUN_DURATION_SECONDS",
    "RUN_JUDGE_AGREEMENT",
    "RUN_PASS_RATE",
    "case_span",
    "configure_tracing",
    "get_tracer",
    "record_case_done",
    "record_judge_call",
    "record_run_finish",
    "record_run_start",
    "run_span",
    "start_metrics_server",
]
