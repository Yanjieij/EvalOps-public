"""EvalOps CLI — run benchmarks, inspect results, smoke-test adapters."""

from __future__ import annotations

from pathlib import Path

import anyio
import typer
from rich.console import Console
from rich.table import Table

from evalops.config import get_settings
from evalops.datasets import load_benchmark
from evalops.logging import configure_logging
from evalops.models import JudgeConfig, JudgeKind, Sut, SutKind
from evalops.observability import configure_tracing, start_metrics_server
from evalops.runner import RunnerEngine
from evalops.runner.io import read_run, write_run

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="EvalOps evaluation CLI.",
)
console = Console()


# ---------- run -------------------------------------------------------------

@app.command()
def run(
    benchmark: Path = typer.Option(..., "--benchmark", "-b", help="Path to benchmark dir"),
    sut: str = typer.Option("mock", "--sut", help="SUT kind: mock | reference"),
    sut_endpoint: str = typer.Option("", "--sut-endpoint", help="Override endpoint URL"),
    judge: str = typer.Option(
        "rule",
        "--judge",
        help="Judge kind: rule | llm_single | llm_pairwise | llm_dual | agent_trace | hybrid",
    ),
    judge_model: str = typer.Option(
        "",
        "--judge-model",
        help="LiteLLM model name used by llm/agent/hybrid judges (e.g. gpt-4o).",
    ),
    judge_baseline_model: str = typer.Option(
        "",
        "--judge-baseline-model",
        help="Secondary provider for llm_dual; also used as the agent-judge model in hybrid.",
    ),
    judge_name: str = typer.Option("default", "--judge-name"),
    concurrency: int = typer.Option(4, "--concurrency", "-c"),
    max_cases: int = typer.Option(0, "--max-cases", help="0 = all"),
    out: Path = typer.Option(Path("runs/latest.json"), "--out", "-o"),
    resume: Path = typer.Option(
        None,
        "--resume",
        help="Path to a previous Run JSON; completed cases are carried over and skipped.",
    ),
    metrics_port: int = typer.Option(
        0,
        "--metrics-port",
        help="Start a Prometheus exporter on this port for the duration of the run (0=off).",
    ),
    log_level: str = typer.Option("INFO", "--log-level"),
) -> None:
    """Run a benchmark against a SUT and write a Run JSON report."""
    settings = get_settings()
    configure_logging(level=log_level)

    # Observability — both toggles are no-ops when unconfigured.
    configure_tracing(
        service_name=settings.otel_service_name,
        endpoint=settings.otel_exporter_endpoint,
    )
    port = metrics_port or settings.prometheus_port
    if port:
        info = start_metrics_server(port=port)
        console.print(f"[cyan]prometheus[/] serving on :{info.get('port')}")

    bench, cases = load_benchmark(benchmark)
    if max_cases > 0:
        cases = cases[:max_cases]

    sut_kind = SutKind(sut)
    sut_obj = _build_sut(sut_kind, sut_endpoint, settings)
    judge_config = JudgeConfig(
        name=judge_name,
        kind=JudgeKind(judge),
        model=judge_model,
        baseline_model=judge_baseline_model,
    )

    resume_run = None
    if resume is not None:
        resume_run = read_run(resume)
        console.print(
            f"[cyan]resume[/] from {resume} "
            f"(previously completed {len([r for r in resume_run.results if not r.error])} cases)"
        )

    engine = RunnerEngine(
        benchmark=bench,
        cases=cases,
        sut=sut_obj,
        judge_config=judge_config,
        concurrency=concurrency,
        resume_from=resume_run,
    )

    run = anyio.run(engine.run)
    path = write_run(run, out)
    console.print(f"[bold green]Run written:[/] {path}")
    _print_summary(run)


def _build_sut(kind: SutKind, endpoint_override: str, settings) -> Sut:
    if kind == SutKind.MOCK:
        return Sut(name="mock", kind=kind)
    if kind == SutKind.REFERENCE:
        return Sut(
            name="reference",
            kind=kind,
            endpoint=endpoint_override or settings.reference_base_url,
            auth={
                "user": settings.reference_user,
                "password": settings.reference_password,
                "timeout_s": str(settings.reference_timeout_s),
            },
        )
    raise typer.BadParameter(f"unsupported --sut {kind!r}")


# ---------- report ----------------------------------------------------------

@app.command()
def report(path: Path = typer.Argument(..., help="Path to a Run JSON file")) -> None:
    """Print a human-friendly summary of a previously written run."""
    run = read_run(path)
    _print_summary(run)
    _print_cases_table(run)


def _print_summary(run) -> None:
    s = run.summary
    tbl = Table(title=f"Run {run.id[:8]}  ·  {run.benchmark.name} @ {run.benchmark.version}  ·  SUT={run.sut.name}")
    tbl.add_column("Field", style="cyan", no_wrap=True)
    tbl.add_column("Value", style="white")
    tbl.add_row("Status", f"[bold]{run.status}[/]")
    tbl.add_row("Cases", f"{len(run.results)}")
    tbl.add_row("Pass rate", f"{s.pass_rate * 100:.1f}%")
    tbl.add_row("Unstable", f"{s.unstable_cases}")
    tbl.add_row("Cost (µUSD)", f"{s.total_cost.micro_usd}")
    tbl.add_row("Prompt tokens", f"{s.total_cost.prompt_tokens}")
    tbl.add_row("Completion tokens", f"{s.total_cost.completion_tokens}")
    console.print(tbl)

    if s.metrics:
        m = Table(title="Metrics (averages)")
        m.add_column("Metric", style="cyan")
        m.add_column("Value", justify="right", style="magenta")
        for name in sorted(s.metrics.keys()):
            m.add_row(name, f"{s.metrics[name]:.3f}")
        console.print(m)


def _print_cases_table(run) -> None:
    t = Table(title=f"Per-case results ({len(run.results)})")
    t.add_column("Case", style="cyan", no_wrap=True)
    t.add_column("Pass", justify="center")
    t.add_column("Latency", justify="right")
    t.add_column("Top metric", style="magenta")
    t.add_column("Error", style="red")
    for r in run.results:
        top = ""
        if r.judge_result.metrics:
            m0 = r.judge_result.metrics[0]
            top = f"{m0.name}={m0.value:.2f}"
        t.add_row(
            r.case_id,
            "[green]✓[/]" if r.passed else "[red]✗[/]",
            f"{r.latency_ms}ms",
            top,
            r.error[:60],
        )
    console.print(t)


# ---------- datasets --------------------------------------------------------

@app.command("show-benchmark")
def show_benchmark(path: Path = typer.Argument(...)) -> None:
    """Dump a benchmark's metadata + case count."""
    bench, cases = load_benchmark(path)
    console.print(f"[bold]{bench.name}[/] @ {bench.version}  ({len(cases)} cases)")
    console.print(bench.model_dump())
    kinds: dict[str, int] = {}
    for c in cases:
        kinds[c.kind] = kinds.get(c.kind, 0) + 1
    console.print(f"Kinds: {kinds}")


# ---------- entry point ------------------------------------------------------

def main() -> None:
    app()


if __name__ == "__main__":
    main()
