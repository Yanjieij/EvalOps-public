# Week 1 Delivery Status

**Milestone**: *Week 1 · 底座 + SUT 扩展* from
`~/.claude/plans/eager-zooming-rabin.md`.

**Goal**: every subsequent week adds value, not rework. Week 1 ships a
small-but-real vertical slice instead of a wide-but-hollow skeleton:
CLI → adapter → real SUT process → rule judge → written artifact →
pytest assertion.

## Delivered

### 1. Mono-repo scaffold ✅
- `proto/`, `services/control-plane`, `services/eval-engine`,
  `datasets/`, `infra/`, `docs/`, `web/`
- Top-level `Makefile` with `help`, `infra-up/down/nuke/ps`,
  `py-install/test/lint`, `go-build/run/test`, `smoke` targets
- `.gitignore`, `README.md` with Quick Start
- `docs/architecture.md` for the at-a-glance view

### 2. EvalOps gRPC protocol ✅
- `proto/evalops/v1/common.proto` — Metadata, Cost, KV, CapabilityTag
- `proto/evalops/v1/dataset.proto` — Benchmark + Case CRUD, idempotent writes
- `proto/evalops/v1/judge.proto` — Rule / LLM / Dual-LLM (κ proxy) /
  Agent / Hybrid surfaces with content-addressed JudgeConfig hashing
- `proto/evalops/v1/runner.proto` — Run lifecycle, SUT registration,
  streaming events (Heartbeat / CaseCompleted / RunFinished / RunFailed)
- `proto/buf.yaml` + `proto/README.md` explaining design choices
  (JSON-in-proto for per-kind schemas, why SUT_KIND_REFERENCE is
  first-class, why LLM_DUAL is its own enum)

### 3. Python eval-engine package ✅
- `pyproject.toml` with hatchling, dev/llm-judge/analysis/rag-metrics extras
- `src/evalops/{config,logging,models}.py` — settings, structlog with
  contextvar binding (`run_id` / `case_id` / `request_id` everywhere),
  17 Pydantic DTOs covering the whole run lifecycle
- `src/evalops/adapters/{base,mock,reference}.py` — SutAdapter
  protocol, in-process MockAdapter, httpx-based ReferenceAdapter with
  optional JWT auth and `X-EvalOps-Run-Id` / `X-EvalOps-Case-Id`
  header propagation
- `src/evalops/judge/{base,metrics,rule,llm_stub}.py` — Judge protocol,
  free of 3rd-party deps: EM / substring / F1 / citation recall / tool
  selection accuracy; rubric-driven rule judge dispatches per CaseKind;
  LLM judge stub exercises the `llm_single/pairwise/dual` paths
- `src/evalops/runner/{engine,io}.py` — RunnerEngine with structured
  concurrency (`anyio.Semaphore`), per-case error isolation, pass-rate
  aggregation, Run JSON persistence
- `src/evalops/datasets/__init__.py` — YAML loader for benchmark directories
- `src/evalops/cli/main.py` — Typer CLI with `run`, `report`,
  `show-benchmark`; Rich tables for summary + per-case view
- `tests/` — 16 tests covering metric primitives and runner end-to-end
  paths (rag-toy + agent-toy + hallucination + refusal); all green

### 4. Go control-plane scaffold ✅
- `cmd/server/main.go` — graceful start/stop, structured zerolog
- `internal/config/config.go` — env-only configuration, documented
  defaults for HTTP, eval-engine gRPC, Postgres, Redis, Jaeger
- `internal/router/router.go` — Gin router wiring middleware +
  handlers + `/metrics`
- `internal/middleware/{request_id,logger,metrics}.go` — request id
  propagation, per-request structured log, Prometheus histograms
- `internal/observability/metrics.go` — process-local Prometheus
  registry with 4 EvalOps-domain metrics
  (`evalops_runs_submitted_total`, `evalops_run_duration_seconds`,
  `evalops_run_cost_micro_usd_total`, `evalops_run_pass_rate`) plus
  HTTP counters + histograms
- `internal/handler/{health,runs}.go` — `/healthz`, `/readyz`,
  `POST /api/v1/runs` stub, `GET /api/v1/runs/:id` stub
- **Status**: compiles, binds :8090, all four endpoints verified with curl

### 5. Local infrastructure docker-compose ✅
- `infra/docker-compose.yml` — Postgres, Redis, MinIO, Jaeger,
  Prometheus, Grafana on offset ports so a companion app stack can run
  beside it
- `infra/prometheus/prometheus.yml` — scrapes control-plane (`:8090`)
  and eval-engine (`:9100`) via `host.docker.internal`
- `infra/grafana/provisioning/` — datasources (Prometheus + Jaeger)
  and dashboard provider
- `infra/grafana/dashboards/evalops-overview.json` — 5-panel dashboard:
  submitted runs, latest pass rate, cost burn, HTTP RPS, HTTP p95
- `infra/README.md` — port table + operational notes

### 6. Reference-App Agent layer (additive-only) ✅
- `reference-app/services/agent-sidecar/` — brand-new service directory,
  **zero edits** to existing host-app files
- `pyproject.toml` — independent FastAPI/uvicorn package
- `src/agent_sidecar/tools.py` — the 4 locked-in tools
  (`rag_query`, `calc`, `file_read`, `mock_web_search`) with
  deterministic failure injection via `AGENT_SIDECAR_FAIL_TOOLS` /
  `AGENT_SIDECAR_FAIL_MODE` env vars
- `src/agent_sidecar/executor.py` — ReAct-style executor with a
  heuristic Planner (Week 2 swaps in an LLM-backed planner; the rest
  of the code is isolated)
- `src/agent_sidecar/server.py` — FastAPI app exposing `/healthz`,
  `/agent/tools`, `/agent/run`, `/api/v1/agent/run` with
  `X-EvalOps-Run-Id` / `X-EvalOps-Case-Id` header propagation
- `README.md` — port, endpoints, failure injection, production
  fold-in plan
- **Status**: installs, runs, two multi-step tasks verified end-to-end

### 7. End-to-end smoke test ✅
Shipped as both a Typer CLI workflow and a pytest regression:

**CLI smoke (mock SUT)**
```bash
evalops run --benchmark datasets/rag-toy --sut mock --out runs/smoke-rag.json
# -> 4 cases, 75% pass rate, hallucinate case correctly fails,
#    unanswerable case correctly passes, 5 metrics computed.

evalops run --benchmark datasets/agent-toy --sut mock --out runs/smoke-agent.json
# -> 3 cases, 66.7% pass rate, 5 agent metrics computed.
```

**CLI smoke (real SUT via Agent sidecar)**
```bash
AGENT_SIDECAR_PORT=18081 agent-sidecar &
evalops run --benchmark datasets/agent-toy \
           --sut reference --sut-endpoint http://localhost:18081 \
           --out runs/e2e-agent-sidecar.json
# -> EvalOps CLI → httpx adapter → FastAPI sidecar (separate process)
#    → ReAct executor → 4 tools → trace → rule judge → run JSON.
```

**pytest regression**
```bash
cd services/eval-engine && pytest -q
# -> 16 passed in 0.6s
```

### 8. Documentation ✅
- `README.md` — project pitch, four pillars, scope, quick start,
  delivery checklist
- `docs/architecture.md` — layered + process-model view, design rationales
- `docs/week1-status.md` — this file
- `docs/reference-sut-changeset.md` — exact inventory of new files added
  to the reference app, risk assessment, Week 2 fold-in plan

## Deferred by design

| Item | Plan week |
|---|---|
| LLM-as-a-Judge (GPT-4o + Claude 3.5 Sonnet) | Week 2 |
| Dual-judge Cohen's κ inter-agreement | Week 3 |
| Agent-as-a-Judge trace auditing | Week 3 |
| Hybrid judge funnel (rule → LLM → agent) | Week 3 |
| Real benchmarks (HotpotQA / MS MARCO / τ-bench / AgentBench subsets) | Week 2 |
| Synthetic dataset generator from application knowledge base | Week 2 |
| Bad-case harvester (Jaeger → regression set) | Week 4 |
| Release Gate (GitHub Actions on application PR) | Week 4 |
| Grafana capability-radar + judge-kappa panels | Week 4 |
| Web frontend | Week 4 |
| ClickHouse for OLAP result storage | Week 4 (PG + JSONB for Weeks 1-3) |
| buf-generated proto stubs | Week 2 |

## Verification checklist

Run these to confirm Week 1 is still green after any change:

```bash
# 1. Python unit + integration tests
cd services/eval-engine && pytest -q

# 2. Smoke: mock SUT, RAG + Agent benchmarks
evalops run --benchmark datasets/rag-toy   --sut mock --out runs/smoke-rag.json
evalops run --benchmark datasets/agent-toy --sut mock --out runs/smoke-agent.json

# 3. Go control-plane boots, metrics + run stub respond
cd services/control-plane && go build ./cmd/server && ./bin/control-plane &
curl -s localhost:8090/healthz
curl -s -X POST localhost:8090/api/v1/runs \
     -H 'Content-Type: application/json' \
     -d '{"benchmark_id":"rag-toy","sut_name":"mock","sut_kind":"mock","judge_kind":"rule","concurrency":4}'
kill %1

# 4. End-to-end with the reference-app Agent sidecar
AGENT_SIDECAR_PORT=18081 agent-sidecar > /tmp/sidecar.log 2>&1 &
evalops run --benchmark datasets/agent-toy \
           --sut reference --sut-endpoint http://localhost:18081 \
           --out runs/e2e-agent-sidecar.json
kill %1
```

All four must succeed with exit code 0. Pass rates are deterministic
given the mock adapter's rubric-driven mode selection, so CI can assert
exact numbers (RAG 75% / Agent 66.7%) to catch metric regressions.
