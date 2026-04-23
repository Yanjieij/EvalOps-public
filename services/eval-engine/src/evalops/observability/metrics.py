"""Prometheus metrics for the eval-engine.

Mirrors the naming convention of the Go control-plane's
``evalops_cp_*`` metrics: this package publishes ``evalops_ee_*`` so
Grafana can show both halves of the system on the same dashboard
without label collision.

We use a **process-local ``CollectorRegistry``** instead of the
default global one. Short-lived CLI runs otherwise emit process
collector noise on every invocation, and our tests would have to
paper over metric duplication when the module is re-imported. A
dedicated registry also lets pytest assert on exact metric values
without racing with background exporters.

The server exposed by ``start_metrics_server`` is optional. CLI
smokes don't touch it — they just call ``record_*`` and exit. A
long-lived runner (Week 4's batch scheduler) will call
``start_metrics_server(port)`` once at boot and let Prometheus scrape
the HTTP endpoint for the lifetime of the process.
"""

from __future__ import annotations

import threading
from typing import Any

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    start_http_server,
)

# Process-local registry. Tests read counter values directly off this
# object and verify `labels(...).get()` results.
METRICS_REGISTRY = CollectorRegistry(auto_describe=True)


# --- Counters ---------------------------------------------------------------

RUNS_TOTAL: Counter = Counter(
    "evalops_ee_runs_total",
    "Count of evaluation runs started by the eval-engine, labelled by terminal status.",
    ["benchmark", "sut", "status"],
    registry=METRICS_REGISTRY,
)

JUDGE_CALLS_TOTAL: Counter = Counter(
    "evalops_ee_judge_calls_total",
    "Count of judge.score() invocations, labelled by judge kind and model.",
    ["kind", "model"],
    registry=METRICS_REGISTRY,
)

JUDGE_COST_MICRO_USD_TOTAL: Counter = Counter(
    "evalops_ee_judge_cost_micro_usd_total",
    "Cumulative judge cost in micro-USD, labelled by judge kind and model.",
    ["kind", "model"],
    registry=METRICS_REGISTRY,
)


# --- Histograms -------------------------------------------------------------

# Wall-clock time of a single run — matches the Go side's bucket choice
# so the cp and ee histograms plot identically on the same axes.
RUN_DURATION_SECONDS: Histogram = Histogram(
    "evalops_ee_run_duration_seconds",
    "Wall-clock duration of an evaluation run (seconds).",
    ["benchmark", "sut"],
    buckets=(1, 5, 10, 30, 60, 120, 300, 600, 1800, 3600),
    registry=METRICS_REGISTRY,
)

CASE_DURATION_SECONDS: Histogram = Histogram(
    "evalops_ee_case_duration_seconds",
    "Wall-clock duration of a single case (SUT + judge) in seconds.",
    ["benchmark", "sut", "kind"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
    registry=METRICS_REGISTRY,
)


# --- Gauges -----------------------------------------------------------------

RUN_PASS_RATE: Gauge = Gauge(
    "evalops_ee_run_pass_rate",
    "Pass rate of the most recent run for each (benchmark, sut) pair.",
    ["benchmark", "sut"],
    registry=METRICS_REGISTRY,
)

RUN_JUDGE_AGREEMENT: Gauge = Gauge(
    "evalops_ee_run_judge_agreement",
    "Run-level Cohen's kappa between dual-judge providers (−1 == n/a).",
    ["benchmark", "sut"],
    registry=METRICS_REGISTRY,
)


# --- Convenience recorders --------------------------------------------------


def record_run_start(benchmark: str, sut: str) -> None:
    """Bump ``runs_total{status="started"}``.

    We emit an explicit ``started`` record on top of the eventual
    terminal status so Grafana can plot *in-flight runs* as
    ``started - (succeeded + failed + partial + cancelled)``.
    """
    RUNS_TOTAL.labels(benchmark=benchmark, sut=sut, status="started").inc()


def record_run_finish(
    *,
    benchmark: str,
    sut: str,
    status: str,
    duration_seconds: float,
    pass_rate: float,
    judge_agreement: float,
) -> None:
    RUNS_TOTAL.labels(benchmark=benchmark, sut=sut, status=status).inc()
    RUN_DURATION_SECONDS.labels(benchmark=benchmark, sut=sut).observe(
        max(0.0, float(duration_seconds))
    )
    RUN_PASS_RATE.labels(benchmark=benchmark, sut=sut).set(float(pass_rate))
    RUN_JUDGE_AGREEMENT.labels(benchmark=benchmark, sut=sut).set(float(judge_agreement))


def record_case_done(
    *,
    benchmark: str,
    sut: str,
    kind: str,
    duration_seconds: float,
) -> None:
    CASE_DURATION_SECONDS.labels(
        benchmark=benchmark, sut=sut, kind=kind
    ).observe(max(0.0, float(duration_seconds)))


def record_judge_call(*, kind: str, model: str, cost_micro_usd: int) -> None:
    JUDGE_CALLS_TOTAL.labels(kind=kind, model=model or "unspecified").inc()
    if cost_micro_usd:
        JUDGE_COST_MICRO_USD_TOTAL.labels(
            kind=kind, model=model or "unspecified"
        ).inc(float(cost_micro_usd))


# --- Prometheus HTTP server -------------------------------------------------

_server_started_lock = threading.Lock()
_server_started: bool = False
_server_info: dict[str, Any] = {}


def start_metrics_server(port: int, addr: str = "0.0.0.0") -> dict[str, Any]:
    """Start an HTTP exporter bound to the process-local registry.

    Idempotent: only the first call starts a server; subsequent calls
    return the same ``{"port", "addr"}`` dict. If the supplied port is
    ``0`` or negative we skip the call entirely, which lets CLI smokes
    pass a disabled port without branching at the call site.
    """
    global _server_started
    if port <= 0:
        return {"port": 0, "addr": "", "started": False}
    with _server_started_lock:
        if not _server_started:
            start_http_server(port, addr=addr, registry=METRICS_REGISTRY)
            _server_info.update({"port": port, "addr": addr, "started": True})
            _server_started = True
    return dict(_server_info)
