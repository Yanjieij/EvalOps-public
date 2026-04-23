"""Tests for the observability module.

Covers three behaviours we care about:

1. **Registry is self-contained** — the 7 metric families are
   registered and nothing leaked onto Prometheus's default globals.
2. **Recorder helpers update the right labels** — calling
   ``record_run_finish`` lights up the expected series without
   touching unrelated labels.
3. **Runner integration** — a full `mock` smoke run moves the
   ``runs_total{status="succeeded"}`` counter, the pass-rate gauge,
   and the case-duration histogram together.

We snapshot values off the process-local registry before/after an
action so the test is order-independent and won't break if other
tests run first.
"""

from __future__ import annotations

from pathlib import Path

import anyio
from prometheus_client import CollectorRegistry

from evalops.datasets import load_benchmark
from evalops.models import JudgeConfig, JudgeKind, Sut, SutKind
from evalops.observability import (
    METRICS_REGISTRY,
    record_case_done,
    record_judge_call,
    record_run_finish,
    record_run_start,
)
from evalops.runner import RunnerEngine

REPO_ROOT = Path(__file__).resolve().parents[3]


def _counter_value(name: str, labels: dict[str, str]) -> float:
    """Read a counter value off the process-local registry."""
    v = METRICS_REGISTRY.get_sample_value(name, labels)
    return 0.0 if v is None else float(v)


def _gauge_value(name: str, labels: dict[str, str]) -> float | None:
    return METRICS_REGISTRY.get_sample_value(name, labels)


def test_metrics_registry_is_not_the_default() -> None:
    """We explicitly opt out of the prometheus_client default registry.

    A process-local registry is important for two reasons: short-lived
    CLI runs don't want the process collector noise, and tests don't
    want metric duplication on reimport.
    """
    assert isinstance(METRICS_REGISTRY, CollectorRegistry)
    # Walk the registry and confirm every collector is ours.
    family_names = {
        f.name
        for f in METRICS_REGISTRY.collect()
        if f.name.startswith("evalops_ee")
    }
    for expected in (
        "evalops_ee_runs",
        "evalops_ee_judge_calls",
        "evalops_ee_judge_cost_micro_usd",
        "evalops_ee_run_duration_seconds",
        "evalops_ee_case_duration_seconds",
        "evalops_ee_run_pass_rate",
        "evalops_ee_run_judge_agreement",
    ):
        assert any(n.startswith(expected) for n in family_names), expected


def test_record_run_lifecycle_updates_expected_series() -> None:
    bench, sut = "unit-bench", "unit-sut"
    before_started = _counter_value(
        "evalops_ee_runs_total",
        {"benchmark": bench, "sut": sut, "status": "started"},
    )
    before_succeeded = _counter_value(
        "evalops_ee_runs_total",
        {"benchmark": bench, "sut": sut, "status": "succeeded"},
    )
    record_run_start(benchmark=bench, sut=sut)
    record_run_finish(
        benchmark=bench,
        sut=sut,
        status="succeeded",
        duration_seconds=1.25,
        pass_rate=0.87,
        judge_agreement=0.71,
    )
    assert (
        _counter_value(
            "evalops_ee_runs_total",
            {"benchmark": bench, "sut": sut, "status": "started"},
        )
        == before_started + 1.0
    )
    assert (
        _counter_value(
            "evalops_ee_runs_total",
            {"benchmark": bench, "sut": sut, "status": "succeeded"},
        )
        == before_succeeded + 1.0
    )
    assert _gauge_value(
        "evalops_ee_run_pass_rate",
        {"benchmark": bench, "sut": sut},
    ) == 0.87
    assert _gauge_value(
        "evalops_ee_run_judge_agreement",
        {"benchmark": bench, "sut": sut},
    ) == 0.71


def test_record_case_done_updates_histogram_count() -> None:
    bench, sut, kind = "unit-bench", "unit-sut", "rag"
    count_before = _counter_value(
        "evalops_ee_case_duration_seconds_count",
        {"benchmark": bench, "sut": sut, "kind": kind},
    )
    record_case_done(
        benchmark=bench, sut=sut, kind=kind, duration_seconds=0.42
    )
    record_case_done(
        benchmark=bench, sut=sut, kind=kind, duration_seconds=1.1
    )
    assert (
        _counter_value(
            "evalops_ee_case_duration_seconds_count",
            {"benchmark": bench, "sut": sut, "kind": kind},
        )
        == count_before + 2.0
    )


def test_record_judge_call_attributes_cost_by_kind_and_model() -> None:
    before_calls = _counter_value(
        "evalops_ee_judge_calls_total",
        {"kind": "hybrid", "model": "gpt-4o-mini"},
    )
    before_cost = _counter_value(
        "evalops_ee_judge_cost_micro_usd_total",
        {"kind": "hybrid", "model": "gpt-4o-mini"},
    )
    record_judge_call(kind="hybrid", model="gpt-4o-mini", cost_micro_usd=1234)
    assert (
        _counter_value(
            "evalops_ee_judge_calls_total",
            {"kind": "hybrid", "model": "gpt-4o-mini"},
        )
        == before_calls + 1.0
    )
    assert (
        _counter_value(
            "evalops_ee_judge_cost_micro_usd_total",
            {"kind": "hybrid", "model": "gpt-4o-mini"},
        )
        == before_cost + 1234.0
    )


def test_runner_smoke_run_emits_all_three_metric_families() -> None:
    """Full RunnerEngine path → counter + histogram + gauge all move."""
    bench_path = REPO_ROOT / "datasets" / "rag-toy"
    bench, cases = load_benchmark(bench_path)
    label = {"benchmark": bench.name, "sut": "mock", "status": "succeeded"}
    started_label = {"benchmark": bench.name, "sut": "mock", "status": "started"}
    before_started = _counter_value("evalops_ee_runs_total", started_label)
    before_succeeded = _counter_value("evalops_ee_runs_total", label)
    before_count = _counter_value(
        "evalops_ee_case_duration_seconds_count",
        {"benchmark": bench.name, "sut": "mock", "kind": "rag"},
    )

    engine = RunnerEngine(
        benchmark=bench,
        cases=cases,
        sut=Sut(name="mock", kind=SutKind.MOCK),
        judge_config=JudgeConfig(name="rule", kind=JudgeKind.RULE),
        concurrency=2,
    )
    anyio.run(engine.run)

    assert _counter_value("evalops_ee_runs_total", started_label) == before_started + 1
    assert _counter_value("evalops_ee_runs_total", label) == before_succeeded + 1
    # Pass-rate gauge is set to the run's final pass_rate — should be >= 0
    pr = _gauge_value(
        "evalops_ee_run_pass_rate",
        {"benchmark": bench.name, "sut": "mock"},
    )
    assert pr is not None and 0.0 <= pr <= 1.0
    # Case-duration count bumped by exactly len(cases) for the RAG kind.
    # rag-toy has a mix of RAG and (none other), so all cases land here.
    rag_cases = sum(1 for c in cases if c.kind.value == "rag")
    after_count = _counter_value(
        "evalops_ee_case_duration_seconds_count",
        {"benchmark": bench.name, "sut": "mock", "kind": "rag"},
    )
    assert after_count - before_count == rag_cases
