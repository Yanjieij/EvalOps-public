# EvalOps Architecture

> Snapshot at end of Week 1. Updates track the plan in
> `~/.claude/plans/eager-zooming-rabin.md`.

## Layered view

```
┌─────────────────────────────────────────────────────────────────────────┐
│ FRONTEND (Week 4)          React + TS + Ant Design Pro + ECharts        │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │ REST + SSE
┌──────────────────────────────────▼──────────────────────────────────────┐
│ CONTROL PLANE (Go / Gin)         • Auth (later)                          │
│ services/control-plane/          • RunScheduler (Week 2)                 │
│                                  • Request-ID / Logging / Metrics (W1 ✓) │
│                                  • Readiness probe (W1 ✓)                │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │ gRPC evalops.v1 (Week 2)
┌──────────────────────────────────▼──────────────────────────────────────┐
│ EVAL ENGINE (Python)                                                     │
│ services/eval-engine/                                                    │
│                                                                          │
│   Runner ── Judge (Rule ✓ / LLM stub ✓ / Agent W3 / Hybrid W3)          │
│     │        │                                                           │
│     │        └── Metrics (EM ✓ / F1 ✓ / citation ✓ / tool_sel ✓ / ...)  │
│     │                                                                    │
│     └── Adapters                                                         │
│           ├── MockAdapter ✓                                              │
│           └── ReferenceAdapter ✓ (chat / rag / agent paths)              │
│                                                                          │
│   Datasets: YAML on disk → Pydantic Case objects ✓                       │
│   CLI: `evalops run | report | show-benchmark` ✓                         │
└─────────────┬─────────────────────────┬──────────────────────┬──────────┘
              │                         │                      │
              ▼                         ▼                      ▼
┌─────────────────────────┐ ┌───────────────────────┐ ┌────────────────────┐
│ SUT — Reference App     │ │ Judge Models          │ │ Storage (W2)       │
│ ├── gateway (Gin)       │ │ (GPT-4o + Claude 3.5, │ │ PG + Redis +       │
│ ├── ai-engine (gRPC)    │ │  Week 2)              │ │ MinIO + ClickHouse │
│ └── agent-sidecar ✓     │ └───────────────────────┘ └────────────────────┘
│     (additive, :8081)   │
└─────────────────────────┘
```

✓ = shipped in Week 1. All other labels are tracked in the plan's phase table.

## Process model (local dev)

```
┌──────────────────┐    ┌──────────────────┐     ┌──────────────────┐
│ evalops CLI      │───▶│ eval-engine      │────▶│ Agent Sidecar    │
│ (Typer app)      │    │ (in-process)     │     │ (FastAPI :8081)  │
└──────────────────┘    └──────────────────┘     └──────────────────┘
                                   │
                                   ▼
                          runs/<name>.json
```

The control plane process is optional for the Week 1 smoke loop — the
CLI runs the Runner in-process. Week 2 adds a worker mode where the
control plane enqueues jobs that the eval-engine picks up over gRPC.

## Code generation status

Week 1 hand-writes DTOs in `evalops.models` (Python) and
`control-plane/internal/handler/runs.go` (Go). Week 2 wires up `buf
generate` with `protoc-gen-go`, `protoc-gen-go-grpc`, and
`grpc_tools.protoc` to replace the hand-written versions.

## Design choices worth calling out

**Why the agent layer lives outside the host app's existing services.**
The host app's ai-engine and gateway are stable and we don't want to touch
them until Week 2. The Week 1 sidecar is purely additive: a new
`reference-app/services/agent-sidecar/` directory with its own
`pyproject.toml`, runs as its own process. Fold-in path is documented in
`reference-sut-changeset.md`.

**Why the Runner is in-process for Week 1.** Network hops complicate the
smoke loop and hide bugs. Single-process evaluation gives us a tight
feedback cycle and also means the runner can be exercised from unit
tests via `anyio.run(engine.run)` without a transport layer. Week 2
splits out the worker for distributed execution.

**Why all I/O is async.** Evaluation is embarrassingly parallel on the
SUT side (each case is independent) and embarrassingly sequential on
the judge side (LLM judges are slow, cost-bound). `anyio` + structured
concurrency lets us use the same code for both regimes.

**Why the rule judge is the first-class citizen in Week 1.** It's
deterministic, free, and tests the end-to-end path without any external
dependencies. The LLM and Agent judges are stubbed so the runner's
plumbing is already correct by the time we plug them in.

**Why the toy datasets are hand-crafted YAML.** We need every metric to
fire on the smoke run so we can catch rule-judge regressions immediately.
A random benchmark subset wouldn't reliably trigger `unanswerable`,
`hallucinate`, or `inject_failure` rubrics. Real benchmarks (HotpotQA,
τ-bench, MS MARCO) arrive in Week 2.
