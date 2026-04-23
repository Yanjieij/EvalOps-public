# EvalOps

> **English** | [简体中文](README.md)

> **Enterprise-grade LLM evaluation platform with SRE DNA.** Turn evaluation from a one-off script into an always-on production service that drives a real data flywheel.

EvalOps is a single-person interview-prep project that wraps a reference enterprise LLM application in a full **evaluation + observability + data-flywheel** loop.

**Scope is deliberately narrow: Agent + RAG on hard multi-hop tasks.**

---

## Table of contents

- [Why EvalOps](#why-evalops)
- [Four pillars](#four-pillars)
- [Architecture](#architecture)
- [Technology stack](#technology-stack)
- [Features in detail](#features-in-detail)
  - [Evaluation runner](#1-evaluation-runner)
  - [Judge engine](#2-judge-engine)
  - [SUT adapters & Agent sidecar](#3-sut-adapters--agent-sidecar)
  - [Datasets](#4-datasets)
  - [Observability & visualization](#5-observability--visualization)
  - [Proto contract & code generation](#6-proto-contract--code-generation)
  - [CI guardrails](#7-ci-guardrails)
- [Repository layout](#repository-layout)
- [Quick start](#quick-start)
- [CLI reference](#cli-reference)
- [Local port map](#local-port-map)
- [License](#license)

---

## Why EvalOps

Open-source evaluation frameworks (OpenCompass, lm-eval-harness, DeepEval, ragas, AgentBench) do a great job at "run a benchmark, get a number". They don't solve two realities of production LLM apps:

1. **Offline vs. online drift** — benchmark distributions go stale the moment your product ships.
2. **No closed loop** — bad cases rot in log files instead of becoming regression tests.

EvalOps treats evaluation as a **production service**: it has its own SLOs, metrics, tracing, CI guardrails, and a data-flywheel pipeline that feeds production failures back into the next regression run.

## Four pillars

| Pillar | What it is | Status |
|---|---|---|
| **Observable Evaluation** | Every run emits OpenTelemetry spans, Prometheus metrics on a process-local registry, and SLOs for judge agreement, cost, and p95 latency | ✅ |
| **Agent-as-a-Judge** | A GPT-4 class agent audits the full action trace of a SUT agent and scores 4 independent dimensions: plan quality, tool selection, reasoning coherence, error recovery | ✅ |

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Frontend (Vite + React + TS + Ant Design)  ✅ info page        │
│  Benchmark │ Run Dashboard │ Radar & Diff │ Case Inspector      │
└──────────────────────────┬──────────────────────────────────────┘
                           │ REST + SSE    (dev proxy /api → :8090)
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  Go Control Plane (Gin)  ✅                                     │
│  Request-ID │ Structured log │ Prometheus │ Health probes       │
└──────────────────────────┬──────────────────────────────────────┘
                           │ gRPC (evalops.v1, buf-generated ✅)
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  Python Evaluation Engine                                       │
│                                                                 │
│   Runner ✅  ── structured concurrency + resume + error isolate │
│     │                                                           │
│     ├── Judge ── Rule ✅ │ LLM ✅ │ Agent ✅ │ Hybrid ✅       │
│     │             │         │                                  │
│     │             │         └── LiteLLM (OpenAI/Claude/Zhipu…)  │
│     │             │                                             │
│     │             └── EM/F1/ragas-lite/tool-selection/recovery  │
│     │                                                           │
│     ├── Adapters ── Mock ✅ │ Reference HTTP ✅                 │
│     │                                                           │
│     └── Datasets ── loader ✅ │ toy ✅ │ HotpotQA-dev-100 ✅    │
└─────────┬────────────────────────┬─────────────────┬───────────┘
          │                        │                 │
          ▼                        ▼                 ▼
┌──────────────────────┐ ┌───────────────────┐ ┌────────────────┐
│  Reference App (SUT) │ │ Judge Models      │ │ Storage        │
│  chat ✅ rag ✅      │ │ via LiteLLM ✅    │ │ PG + MinIO     │
│  agent-sidecar ✅    │ │ (provider-agnos.) │ │ (local docker) │
└──────────────────────┘ └───────────────────┘ └────────────────┘
          ▲
          │  X-EvalOps-Run-Id / Case-Id propagated through headers
```

## Technology stack

| Layer | Tool | Why |
|---|---|---|
| Control plane | Go 1.24 · Gin · zerolog · prometheus/client_golang | Reuses familiar backend engineering patterns; high-concurrency I/O front door |
| Evaluation engine | Python 3.12 · Pydantic v2 · anyio · structlog · Typer · Rich | Evaluation is IO-bound; async + structured concurrency fits naturally |
| LLM access | **LiteLLM** | One unified `chat.completions`-style API for OpenAI / Anthropic / ZhipuAI / Gemini / Ollama / Bedrock. Adding a provider is a **string change**, not code |
| gRPC contract | Protocol Buffers · **buf 1.66** with remote plugins | No local `protoc` toolchain needed; CI drift-guards the committed stubs |
| Data loading | PyYAML + in-memory Pydantic · raw HotpotQA JSON via streaming slice | Small datasets ship as git-friendly YAML; large ones via a fetch script |
| Storage | PostgreSQL 16 (docker) · Redis 7 (docker) · MinIO (docker) | docker-compose keeps state persistent; Run JSONs can also be written directly to disk |
| Observability | Prometheus 2.52 · Grafana 10.4 · Jaeger 1.55 · structlog ctxvars | See [Observability](#5-observability--visualization) for what's actually exercised |
| Frontend | Vite 5 · React 18 · TypeScript 5 · Ant Design 5 | Provides the project info page and local dev proxy |
| CI | GitHub Actions · ruff · pytest · go vet/build/race · buf lint · npm ci | 6 independent lanes, cancel-in-progress on stale pushes |

---

## Features in detail

### 1. Evaluation runner

**File**: `services/eval-engine/src/evalops/runner/engine.py`

The `RunnerEngine` is the workhorse that turns `(benchmark, cases, SUT, judge_config)` into a fully-populated `Run` object. Design decisions:

- **Async-only, structured concurrency.** Uses `anyio.Semaphore(concurrency)` to bound parallel SUT calls, all inside a single `task_group`. Cancellation of one task cancels the rest cleanly.
- **Per-case error isolation.** A single failing SUT call or judge error never sinks the whole run. The failure is captured in `CaseResult.error` and the run still computes a summary. Distinction between `RunStatus.FAILED` (everything errored), `PARTIAL` (some errored), and `SUCCEEDED` is derived from the error count.
- **ContextVar propagation.** Each worker binds `run_id`, `case_id`, `request_id` into the structlog contextvars so every log line written anywhere downstream automatically carries them. No manual plumbing.
- **Run-level metric aggregation.** The `_summarize` method averages per-case metric values into `RunSummary.metrics`, sums costs, computes pass rate, and — for dual-judge runs — walks every case's `judge_trace["dual_raw_pairs"]` and computes **Cohen's κ once across the full corpus** (κ is a corpus-level statistic and is meaningless at n=1).
- **Idempotent resume.** `RunnerEngine(..., resume_from=prior_run)` carries completed non-errored cases forward verbatim, re-runs only the pending tail, and preserves the original `run_id` so Jaeger trace correlation survives a restart. Full resume (every case already done) is a pure summary rebuild with zero SUT calls.

**Test coverage**: `test_runner_mock.py` (baseline), `test_runner_resume.py` (4 resume scenarios), `test_runner_dual_judge.py` (3 run-level κ scenarios).

### 2. Judge engine

**Directory**: `services/eval-engine/src/evalops/judge/`

Five judge kinds — all shipped:

#### Rule judge (`rule.py` + `metrics.py`) — ✅ Week 1

Deterministic, free, the first line of any hybrid funnel. Dispatches on `CaseKind` and emits:

- **RAG cases** — `exact_match`, `substring_match`, `f1` (SQuAD-style token F1), `citation_recall`, `context_precision` (dual of recall, catches verbose retrievers), `faithfulness_lite` (token-overlap proxy that catches blatant hallucinations without an LLM), plus `unanswerable_handling` when `rubric.expected_refusal` is set.
- **Agent cases** — `final_em`, `final_f1`, `tool_selection` (correct tool at each expected step), `plan_efficiency` (expected_len / predicted_len, capped), and `error_recovery` when `rubric.inject_failure` is set.
- **Chat cases** — `exact_match`, `f1`, or a degenerate `non_empty` guard.

All primitives are in `metrics.py`; 23 unit tests pin their behaviour.

#### LLM-as-a-Judge (`llm.py` + `prompts.py`) — ✅ Week 2

Backed by **LiteLLM**, so swapping providers is a string change. Three sub-kinds:

| Kind | How it works | Mitigations |
|---|---|---|
| `LLM_SINGLE` | Single model, per-metric rubric prompt (currently `rag/faithfulness`, `rag/answer_relevancy`), strict JSON output with markdown-fence-tolerant parser | `repeats` + stddev threshold → `unstable=True` flag |
| `LLM_PAIRWISE` | Two calls per case (SUT=A baseline=B, then swap) with SUT-centric vote reconciliation | Position-bias collapse: if both votes pick position A, result is TIE + unstable |
| `LLM_DUAL` | Primary + secondary provider score each case; case-level result is the mean + an `llm/dual_bin_agreement` in {0, 0.5, 1.0} | Run-level **Cohen's κ** computed in `_summarize` across all cases' raw pairs — this is our human-annotator-free inter-agreement proxy |

The `LiteLLMClient` wrapper lazy-imports `litellm` so the slim default install never pulls the heavy dep tree. Tests replace the client with a `StubClient` that returns canned responses — **no test ever hits a real API**. Setting `EVALOPS_LLM_JUDGE=stub` also toggles a deterministic stub for offline CI smoke runs.

**Test coverage**: `test_judge_llm.py` — 13 tests covering parse, self-consistency, pairwise swap disagreement, dual-judge bin agreement, the κ primitive.

#### Agent-as-a-Judge (`agent.py`) — ✅ Week 3

EvalOps's core differentiator against the LLM-as-a-Judge crowd. Hands the full ReAct trace — every thought, action, observation — to a GPT-4 class model via LiteLLM and asks for a **four-dimensional verdict**:

| Dimension              | What it catches |
|------------------------|----------------|
| `plan_quality`         | sensible task decomposition, wasted steps |
| `tool_selection`       | right tool for each step, reasonable args |
| `reasoning_coherence`  | thoughts consistent with prior observations |
| `error_recovery`       | retry/adapt when a tool fails or returns empty |

- Emits per-dim `MetricScore` entries + an `agent_judge/overall` weighted mean. `rubric.dimension_weights` can tilt the aggregate.
- Prompt (`prompts.py:AGENT_TRACE_USER`) has explicit 1.0 / 0.7 / 0.4 / 0.0 anchors per dimension — anchoring measurably reduces inter-run variance.
- Trace renderer clips long observations at 280 chars and caps total steps at 40, so `file_read` cases can't blow the judge's token budget.
- Per-dim self-consistency: when `repeats > 1`, any dim with stddev above `rubric.unstable_stddev` (default 0.15) flags the whole result `unstable=True`.
- Parse errors never raise — they become 0.0 on every dim + `unstable=True`.
- **Test coverage**: `test_judge_agent.py` — 6 tests covering the happy path, trace clipping, self-consistency, weighted overalls, parse errors, and empty traces, all via `StubClient` (zero real API calls).

#### Hybrid judge (`hybrid.py`) — ✅ Week 3

Three-tier cost/quality cascade: `rule → LLM → Agent-as-a-Judge`. Rule metrics always run and are **never discarded**. LLM/Agent tiers run alongside for diagnostics.

- **Escalation policy** lives in `_needs_llm` / `_needs_agent`. Defaults: escalate RAG/CHAT cases to LLM when `rag/faithfulness_lite < 0.7` or `rag/citation_recall < 0.5`; fire Agent-as-a-Judge on every `CaseKind.AGENT` case. Rubric overrides: `always_llm`, `skip_llm`, `always_agent_judge`, `skip_agent_judge`, `escalate_faithfulness`, `escalate_citation_recall`, `llm_model`, `agent_judge_model`.
- `judge_trace["escalations"]` is an ordered list of tiers that actually fired — `["rule"]`, `["rule","llm"]`, `["rule","agent"]`, or `["rule","llm","agent"]`. The Grafana "judge cost burn per tier" panel reads it.
- **Lazy** judge construction: LLM/Agent judges are only instantiated on the first case that escalates, and cached by model name. Rule-only runs pay zero LiteLLM import cost.
- Honours `EVALOPS_LLM_JUDGE=stub` — `make smoke` / CI exercises the full funnel end-to-end without a real API key.
- **Test coverage**: `test_judge_hybrid.py` — 5 tests covering rule-only short-circuit, RAG-low-faithfulness LLM escalation, agent-case forced escalation, `skip_*` flags, `always_llm`.

### 3. SUT adapters & Agent sidecar

**Directory**: `services/eval-engine/src/evalops/adapters/`

Every SUT integration implements one async method: `call(case, metadata) -> SutOutput`. Three adapters today:

- **`MockAdapter`** — fully in-process, deterministic, honours `rubric.mock_mode` (`faithful` / `hallucinate` / `refuse`) so unit tests can reproduce specific failure modes without any external service.
- **`ReferenceAdapter`** — `httpx.AsyncClient`, auth-optional (pre-minted token / user+password login / anonymous), auto-injects `X-Request-ID` / `X-EvalOps-Run-Id` / `X-EvalOps-Case-Id` headers on every call. Routes `CaseKind.RAG` → `/api/v1/knowledge/query`, `CHAT` → `/api/v1/chat/sync`, `AGENT` → `/api/v1/agent/run`.
- **Agent sidecar** — `sut-extensions/reference-agent-sidecar/`. Because the reference app has no native Agent surface, we ship an **additive-only** FastAPI service: ReAct executor + 4 locked-in tools (`rag_query`, `calc`, `file_read`, `mock_web_search`), each with a deterministic failure-injection switch via env vars. Mirrored into a companion app tree by `scripts/deploy-sidecar.sh` so the canonical source stays git-tracked in this repo.

### 4. Datasets

**Directory**: `datasets/` + `services/eval-engine/src/evalops/datasets/`

Four datasets checked in:

| Name | Size | Purpose |
|---|---|---|
| `rag-toy` | 4 cases | Hand-crafted rubric-driven smoke cases that exercise **every** rule-judge metric in under a second. Happy path / hallucination / unanswerable / citation. |
| `agent-toy` | 3 cases | Tool-selection, plan efficiency, error-recovery rubrics. |
| `hotpotqa-dev-100` | 100 cases (~1.4 MB) | Real public benchmark. First 100 cases of HotpotQA dev-distractor split, deterministically sliced by `scripts/fetch-hotpotqa.sh`. 100% hard multi-hop, 79 bridge + 21 comparison. |
| `tau-bench-lite` | 20 cases | τ-bench / ToolBench-inspired agent benchmark with 6 subsets: single-step lookup, multi-hop, multi-tool, file_read, unanswerable, error_recovery. Every case ships both an `input.preset_plan` and an `expected.trace`, so sidecar execution and MockAdapter replay stay in sync by construction. |

**Loader** (`datasets/__init__.py`) reads `benchmark.yaml` + either `cases.yaml` or `cases/*.yaml`, parses into Pydantic `Case` objects with automatic `CaseKind` / `CapabilityTag` coercion.

**HotpotQA adapter** (`datasets/hotpotqa.py`) maps one HotpotQA record → one EvalOps `Case`: question → `input.query`, gold answer, supporting titles → `source_ids`, full distractor context flattened into `sources`, gold supporting sentences kept under `expected.supporting_sentences`, `level` → `difficulty` (1/3/5), capability tags `rag/multi_hop`, `rag/level/<lvl>`, `rag/<type>`.

### 5. Observability & visualization

**Everything below runs locally via `make infra-up`.** There is no remote or cloud deployment.

#### Structured logging — ✅

- `services/eval-engine/src/evalops/logging.py`: structlog with a custom `_inject_context` processor that reads `run_id` / `case_id` / `request_id` from `ContextVar`s and merges them into **every** log line automatically.
- `bind_run()` / `bind_case()` / `bind_request()` are called once per task; every subsequent `log.info(...)` in the same async task picks them up with no manual plumbing.
- Two output modes: colour console (dev) and JSON (prod) via `EVALOPS_LOG_JSON=true`.
- Go control plane uses `zerolog` with the same `request_id` convention.

#### Prometheus metrics — ✅ (control plane + eval-engine)

**Go control plane** — `services/control-plane/internal/observability/metrics.go`, exposed at `GET /metrics` on port `:8090`:

| Metric | Type | Labels | What it measures |
|---|---|---|---|
| `evalops_cp_http_requests_total` | counter | `method`, `path`, `status` | HTTP-level traffic (from `Metrics()` middleware) |
| `evalops_cp_http_request_duration_seconds` | histogram | `method`, `path` | Request latency (DefBuckets; `path` uses `c.FullPath()` to keep cardinality bounded) |
| `evalops_runs_submitted_total` | counter | `benchmark`, `sut` | Domain: runs submitted via `POST /api/v1/runs` |
| `evalops_run_duration_seconds` | histogram | `benchmark`, `sut` | Domain: wall-clock run duration |
| `evalops_run_cost_micro_usd_total` | counter | `benchmark`, `sut` | Domain: cumulative judge + SUT cost |
| `evalops_run_pass_rate` | gauge | `benchmark`, `sut` | Domain: pass rate of the last completed run |

The registry is **process-local** (not the default registry) so Go runtime noise is opt-in — we explicitly register `ProcessCollector` + `GoCollector` for parity with the default.

**Python eval-engine** — `services/eval-engine/src/evalops/observability/metrics.py` — also a process-local `CollectorRegistry`, prefixed `evalops_ee_*` so it coexists with the Go side on the same Grafana dashboard without label collision. HTTP exporter is **opt-in**: pass `--metrics-port 9100` (or set `EVALOPS_PROMETHEUS_PORT=9100`) to start the server for the lifetime of the run. CLI smokes skip it entirely. Prometheus is already configured to scrape `host.docker.internal:9100`.

| Metric | Type | Labels | What it measures |
|---|---|---|---|
| `evalops_ee_runs_total` | counter | `benchmark`, `sut`, `status` | Run lifecycle — emitted on `start` plus terminal status |
| `evalops_ee_judge_calls_total` | counter | `kind`, `model` | Judge invocations per tier / provider |
| `evalops_ee_judge_cost_micro_usd_total` | counter | `kind`, `model` | Cumulative judge cost, attributed per tier / provider |
| `evalops_ee_run_duration_seconds` | histogram | `benchmark`, `sut` | Wall-clock run duration; buckets matched with the Go side |
| `evalops_ee_case_duration_seconds` | histogram | `benchmark`, `sut`, `kind` | Per-case duration, split by `CaseKind` |
| `evalops_ee_run_pass_rate` | gauge | `benchmark`, `sut` | Last-run pass rate (overwritten on each run) |
| `evalops_ee_run_judge_agreement` | gauge | `benchmark`, `sut` | Dual-judge Cohen's κ, or `-1` when N/A |

The Grafana overview dashboard now includes 4 panels that read these series: eval-engine runs/sec by status, judge cost burn per tier, dual-judge κ gauge, and case-duration p95 by kind.

#### Distributed trace correlation — ✅ headers + exporter

- `ReferenceAdapter` injects three correlation headers on every call: `X-Request-ID` (unique per request), `X-EvalOps-Run-Id`, `X-EvalOps-Case-Id`.
- The reference-app agent sidecar echoes them back in the response body so tests can assert end-to-end propagation.
- OpenTelemetry spans are wired on the eval-engine side (`services/eval-engine/src/evalops/observability/tracing.py`). `RunnerEngine.run` wraps every run in a `run_span` and every case in a child `case_span` with attributes matching the Prometheus label set (`evalops.run_id`, `evalops.case_id`, `evalops.benchmark`, `evalops.sut`, `evalops.case_kind`). When `EVALOPS_OTEL_EXPORTER_ENDPOINT` is empty, OTel's default no-op tracer means the instrumentation is free. Set the env var to your OTLP HTTP endpoint (default Jaeger: `http://localhost:4328/v1/traces`) and the `TracerProvider` + `OTLPSpanExporter` initialize lazily via `configure_tracing()` inside the CLI.

#### Metric scraping — ✅

`infra/prometheus/prometheus.yml` — Prometheus running in docker, `scrape_interval: 15s`:

```yaml
  - job_name: evalops-control-plane
    metrics_path: /metrics
    static_configs:
      - targets: ["host.docker.internal:8090"]
        labels: {service: control-plane, env: local}

  - job_name: evalops-eval-engine
    metrics_path: /metrics
    static_configs:
      - targets: ["host.docker.internal:9100"]
        labels: {service: eval-engine, env: local}
```

`host.docker.internal` is the macOS/Windows Docker Desktop alias for the host — this is how the containerized Prometheus reaches the host-running Go and Python processes without futzing with bridge networks.

#### Grafana dashboard — ✅

Auto-provisioned (`infra/grafana/provisioning/`) with:

- **Datasources**: Prometheus (http://prometheus:9090) + Jaeger (http://jaeger:16686), both via docker-compose service names
- **Dashboards**: loaded from `infra/grafana/dashboards/*.json`, one per file

Ships with **"EvalOps Overview"** — 5 panels:

| Panel | PromQL |
|---|---|
| Runs submitted / sec | `sum(rate(evalops_runs_submitted_total[5m])) by (benchmark, sut)` |
| Latest pass rate | `evalops_run_pass_rate` |
| Run cost burn (µUSD/hr) | `sum(rate(evalops_run_cost_micro_usd_total[1h])) by (benchmark, sut)` |
| Control-plane HTTP RPS | `sum(rate(evalops_cp_http_requests_total[1m])) by (path, status)` |
| Control-plane HTTP p95 latency | `histogram_quantile(0.95, sum(rate(evalops_cp_http_request_duration_seconds_bucket[5m])) by (le, path))` |

Open at `http://localhost:3001` (admin / admin), the EvalOps folder is pre-created.

#### Jaeger — ✅

`jaegertracing/all-in-one` container with `COLLECTOR_OTLP_ENABLED=true`. Ports: UI `:16696`, OTLP gRPC `:4327`, OTLP HTTP `:4328`. Point `EVALOPS_OTEL_EXPORTER_ENDPOINT=http://localhost:4328/v1/traces` at it to export eval-engine spans.

#### CLI run report — ✅

`evalops report <run.json>` uses `rich.Table` to render:

1. **Summary** — run ID, benchmark + version, SUT, status, pass rate, unstable count, total cost (µUSD), prompt + completion tokens
2. **Metric averages** — every metric that appeared in any case, averaged
3. **Per-case table** — case id, pass/fail, latency, top metric, error message

This is the single-shot observation surface when the docker stack isn't running. Fast, zero-dep (`rich` is already a runtime dep of Typer).

#### Web frontend — ✅

`web/frontend/` — Vite + React 18 + TypeScript + Ant Design 5, serves on `:5180` with `/api → :8090` proxy. It provides the project info page and a convenient local control-plane proxy.

**Verified end-to-end in browser** via `preview_start name=evalops-web` — empty console, empty server logs, all antd components render.

### 6. Proto contract & code generation

**Directory**: `proto/evalops/v1/`

Four `.proto` files:

| File | What it defines |
|---|---|
| `common.proto` | `Metadata` (request_id/trace_id/run_id/case_id), `Cost` (micro-USD + tokens), `KV`, `CapabilityTag` |
| `dataset.proto` | `DatasetService` (CreateBenchmark / AddCases / ListCases), `Benchmark`, `Case` (JSON-in-proto for per-kind schemas), `CaseKind` enum |
| `judge.proto` | `JudgeService` (Score / ScoreBatch), `JudgeConfig` with **content-addressed hash** for cache-key equality, `JudgeKind` including the first-class `LLM_DUAL` |
| `runner.proto` | `RunnerService` (SubmitRun / StreamRunEvents / CancelRun), `Run`, `RunSummary`, `RunEvent` oneof union for streaming (Heartbeat / CaseCompleted / RunFinished / RunFailed) |

**Why JSON-in-proto** for case/rubric payloads: `CaseKind` has a different natural schema per kind (RAG vs Agent vs Hybrid). Encoding them as a `oneof` explodes the .proto on every new task type. JSON blobs keep the wire contract tight while per-kind schemas evolve in code.

**Why content-address `JudgeConfig`**: two runs with different judge names but identical rubric/model/temperature should be comparable; two that look identical but use a different rubric must not. Hashing the canonical form becomes the judge-cache key.

**Generation** — `make proto-gen`:
- Uses **buf** with remote plugins (`buf.build/protocolbuffers/go`, `grpc/go`, `protocolbuffers/python`, `grpc/python`) → **no local `protoc` install needed**
- Go output → `services/control-plane/internal/proto/evalops/v1/` (managed `go_package_prefix`)
- Python output → `services/eval-engine/src/evalops/v1/` (path chosen so `from evalops.v1 import common_pb2` resolves natively without `protoletariat`)
- Generated stubs **are checked into git** so `go build` and `pytest` don't require buf
- `make proto-check` regenerates and diffs against the tree; CI runs this on every PR

### 7. CI guardrails

**File**: `.github/workflows/ci.yml`

Six parallel lanes, `concurrency: ci-<ref>` cancels stale runs on the same branch:

| Lane | What it runs |
|---|---|
| `eval-engine` | Python 3.11 **and** 3.12 matrix · `pip install -e '.[dev]'` · `ruff check` · `pytest -q --cov=evalops` |
| `control-plane` | Go 1.23 · `go vet ./...` · `go build ./...` · `go test -race ./...` |
| `agent-sidecar` | `pip install -e .` · import smoke (`from agent_sidecar.server import create_app; create_app()`) |
| `proto` | `buf lint` · `buf generate` · fail if committed stubs drift |
| `sidecar-sync` | Structural check of `sut-extensions/` and `scripts/deploy-sidecar.sh` |
| `web` | Node 22 · `npm ci` · `npm run typecheck` · `npm run build` |

---

## Repository layout

```
evalops/
├── proto/
│   ├── evalops/v1/                    # The wire contract
│   ├── buf.yaml                       # Lint rules (relaxes RPC_*_UNIQUE for internal API)
│   └── buf.gen.yaml                   # Remote plugin config
├── services/
│   ├── control-plane/                 # Go · Gin · zerolog · Prometheus
│   │   ├── cmd/server/                # Binary entry
│   │   ├── internal/
│   │   │   ├── config/                # Env-var config
│   │   │   ├── handler/               # Gin handlers (health, runs stub)
│   │   │   ├── middleware/            # request_id, logger, metrics
│   │   │   ├── observability/         # Prometheus registry + metric decls
│   │   │   ├── router/                # Route wiring
│   │   │   └── proto/                 # buf-generated stubs (checked in)
│   └── eval-engine/                   # Python · Pydantic · anyio · structlog · LiteLLM
│       ├── src/evalops/
│       │   ├── cli/main.py            # Typer CLI: run / report / show-benchmark
│       │   ├── runner/engine.py       # RunnerEngine + resume logic
│       │   ├── judge/                 # base / rule / llm / llm_stub / metrics / prompts
│       │   ├── adapters/              # base / mock / reference
│       │   ├── datasets/              # loader + hotpotqa adapter
│       │   ├── v1/                    # buf-generated stubs (checked in)
│       │   ├── config.py              # pydantic-settings
│       │   ├── logging.py             # structlog + contextvars
│       │   └── models.py              # 17 Pydantic DTOs (in-process equivalents of proto)
│       └── tests/                     # metrics, runner, llm judge, resume, dual
├── sut-extensions/
│   └── reference-agent-sidecar/       # Source of truth for the additive reference-app patch
│       └── src/agent_sidecar/         # FastAPI · ReAct executor · 4 tools + failure injection
├── datasets/
│   ├── rag-toy/                       # 4 hand-crafted RAG cases
│   ├── agent-toy/                     # 3 hand-crafted Agent cases
│   └── hotpotqa-dev-100/              # 100 public multi-hop cases
├── infra/
│   ├── docker-compose.yml             # PG + Redis + MinIO + Jaeger + Prometheus + Grafana
│   ├── prometheus/prometheus.yml
│   └── grafana/
│       ├── provisioning/              # datasources + dashboards auto-load
│       └── dashboards/                # EvalOps Overview JSON
├── web/frontend/                      # Vite + React + TS + antd info page
├── docs/                              # Architecture and reference SUT changeset
│   ├── architecture.md
│   └── reference-sut-changeset.md
├── scripts/
│   ├── deploy-sidecar.sh              # Mirror sut-extensions → reference-app tree
│   └── fetch-hotpotqa.sh              # Deterministic slicer (size/split configurable)
├── .github/workflows/ci.yml           # 6 parallel lanes
└── Makefile                           # infra-up / proto-gen / smoke / sidecar-* / ...
```

---

## Quick start

### 1. Environment

Always use the dedicated conda env — **never the base env**:

```bash
conda create -n evalops python=3.12 -y
conda activate evalops
pip install -e 'services/eval-engine[dev]'            # core, tests, ruff
pip install -e 'services/eval-engine[llm-judge]'      # LiteLLM (optional)
pip install -e sut-extensions/reference-agent-sidecar
```

### 2. Bring up the observability stack (optional)

```bash
make infra-up      # PG / Redis / MinIO / Jaeger / Prometheus / Grafana
make infra-ps      # health check
```

Then open:

- **Grafana**: http://localhost:3001 (admin / admin) → EvalOps Overview
- **Prometheus**: http://localhost:9091
- **Jaeger**: http://localhost:16696

### 3. Run benchmarks

```bash
# Toy RAG smoke (no external service needed)
evalops run --benchmark datasets/rag-toy --sut mock --out runs/toy.json

# HotpotQA dev-100 against the mock SUT
evalops run --benchmark datasets/hotpotqa-dev-100 --sut mock --out runs/hotpot.json

# Agent benchmark via the reference-app sidecar (real SUT process)
scripts/deploy-sidecar.sh --reinstall
AGENT_SIDECAR_PORT=18081 agent-sidecar &
evalops run --benchmark datasets/agent-toy \
            --sut reference \
            --sut-endpoint http://localhost:18081 \
            --out runs/agent.json

# LLM-as-a-Judge (requires API key)
export OPENAI_API_KEY=...
evalops run --benchmark datasets/hotpotqa-dev-100 --sut mock \
            --judge llm_single --judge-name gpt4o \
            --out runs/hotpot-llm.json

# Dual-judge with run-level Cohen's κ (two providers)
export OPENAI_API_KEY=... ANTHROPIC_API_KEY=...
evalops run --benchmark datasets/rag-toy --sut mock \
            --judge llm_dual --judge-name gpt4o-vs-claude \
            --out runs/dual.json

# Offline stub LLM judge (CI-friendly, no API key)
EVALOPS_LLM_JUDGE=stub evalops run --benchmark datasets/rag-toy \
            --sut mock --judge llm_single --out runs/stub.json

# Resume a partial run — keeps original run_id
evalops run --benchmark datasets/rag-toy --sut mock \
            --resume runs/toy.json --out runs/toy-resumed.json

# Inspect any run
evalops report runs/toy.json
```

### 4. Start the web frontend

```bash
cd web/frontend
npm ci
npm run dev    # http://localhost:5180
```

### 5. Start the Go control plane

```bash
cd services/control-plane
go run ./cmd/server    # http://localhost:8090
# /healthz · /readyz · /metrics · POST /api/v1/runs (stub) · GET /api/v1/runs/:id
```

---

## CLI reference

```
evalops run         Run a benchmark against a SUT and write a Run JSON report
  --benchmark       Path to benchmark dir (must contain benchmark.yaml)
  --sut             mock | reference
  --sut-endpoint    Override endpoint URL (e.g. http://localhost:18081 for sidecar)
  --judge           rule | llm_single | llm_pairwise | llm_dual | hybrid
  --judge-name      Free-form name for the run
  --concurrency     SUT call parallelism (default 4)
  --max-cases       Truncate to first N cases (0 = all)
  --out             Path to write Run JSON (default runs/latest.json)
  --resume          Path to a previous Run JSON; completed cases carried over
  --log-level       INFO | DEBUG | WARNING | ERROR

evalops report      Pretty-print a previously written Run JSON
  <path>            Path to the run JSON

evalops show-benchmark    Dump a benchmark's metadata + case count
  <path>            Path to the benchmark dir
```

---

## Local port map

All services run locally via `make infra-up` — no remote infrastructure. Ports are deliberately offset from a companion app stack so both can run simultaneously.

| Component | Host port | URL | Notes |
|---|---|---|---|
| Go control plane | 8090 | http://localhost:8090 | `/healthz`, `/metrics`, `/api/v1/runs` |
| Python eval-engine metrics | 9100 | — | Disabled by default (`EVALOPS_PROMETHEUS_PORT=0`) |
| Web frontend (Vite) | 5180 | http://localhost:5180 | `/api` proxied to :8090 |
| PostgreSQL | 5452 | `postgres://evalops:evalops@localhost:5452/evalops` | |
| Redis | 6389 | `redis://localhost:6389` | |
| MinIO S3 API | 9010 | http://localhost:9010 | `evalops` / `${EVALOPS_MINIO_ROOT_PASSWORD}` |
| MinIO console | 9011 | http://localhost:9011 | |
| Jaeger UI | 16696 | http://localhost:16696 | Set `EVALOPS_OTEL_EXPORTER_ENDPOINT=http://localhost:4328/v1/traces` to export |
| Jaeger OTLP gRPC | 4327 | — | Ready to receive |
| Jaeger OTLP HTTP | 4328 | — | Ready to receive |
| Prometheus | 9091 | http://localhost:9091 | |
| Grafana | 3001 | http://localhost:3001 | admin / admin; "EvalOps Overview" auto-loaded |
| Agent sidecar | 18081 | http://localhost:18081 | Started manually with `AGENT_SIDECAR_PORT=18081 agent-sidecar` |

---

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
