# Week 3 Delivery Status

**Milestone**: *Week 3 · Judge upgrade + analysis* from
`~/.claude/plans/eager-zooming-rabin.md`.

**Goal**: turn EvalOps from a RAG-rule-first harness into the tool
that actually implements the project's core differentiator —
**Agent-as-a-Judge** — and make that judge part of a cost-aware
three-tier funnel. At the same time, close the observability loop
on the Python side so the eval-engine's own metrics land in Grafana
alongside the Go control-plane's.

## Delivered

### 1. Agent-as-a-Judge (`judge/agent.py`) ✅

The project's single biggest differentiator. Scores the Agent's full
ReAct trace on **four independent dimensions** — not the final answer —
via a LiteLLM-backed call:

| Dimension              | What it catches |
|------------------------|----------------|
| `plan_quality`         | sensible task decomposition, wasted steps |
| `tool_selection`       | right tool for each step, reasonable args |
| `reasoning_coherence`  | thoughts consistent with prior observations |
| `error_recovery`       | retry/adapt when a tool fails or returns empty |

Implementation notes:

- Prompt is in `judge/prompts.py:AGENT_TRACE_USER` with explicit rubric
  anchors per dimension — 1.0 / 0.7 / 0.4 / 0.0 exemplars. Anchors
  matter: they measurably reduce inter-run variance without changing
  the scoring model.
- Trace renderer (`_render_trace`) clips long observations at 280
  characters and caps total steps at 40, so `file_read` cases can't
  blow the judge's token budget with raw file bytes.
- Per-dim self-consistency: when `config.repeats > 1`, each rep
  scores all four dims; if any dim's stddev exceeds
  `rubric.unstable_stddev` (default 0.15) the whole result is flagged
  `unstable=True`.
- `rubric.dimension_weights` lets a benchmark tilt the overall toward
  a specific axis — τ-bench-lite uses it to weight `error_recovery`
  at 2.0 on the recovery subset.
- Emits 5 `MetricScore`s: one per dim plus
  `agent_judge/overall` (weighted mean).
- Parse errors (judge returned garbage) become 0.0 for every dim +
  `unstable=True`, instead of raising — the runner logs the error
  and continues.

### 2. Hybrid funnel (`judge/hybrid.py`) ✅

Three-tier cost/quality cascade:

```
Rule judge (free, always runs)
      │
      ▼   escalation policy
LLM judge (medium cost)
      │   (when rag/faithfulness_lite or rag/citation_recall
      │    falls below a threshold, or rubric.always_llm)
      ▼
Agent-as-a-Judge (expensive)
      (for every CaseKind.AGENT case, or rubric.always_agent_judge)
```

- Rule metrics are **never** discarded, even when higher tiers fire.
  Release Gate (Week 4) only trusts rule-tier metrics as pass/fail
  gates because they're deterministic; LLM/Agent judges run alongside
  for diagnostics.
- Per-case escalation lives in `_needs_llm` / `_needs_agent` static
  methods — rubric overrides: `always_llm`, `skip_llm`,
  `always_agent_judge`, `skip_agent_judge`, `escalate_faithfulness`,
  `escalate_citation_recall`, `llm_model`, `agent_judge_model`.
- `judge_trace["escalations"]` is an ordered list — `["rule"]`,
  `["rule", "llm"]`, `["rule", "agent"]`, or `["rule", "llm", "agent"]`
  — the Grafana panel reads it to break judge cost down per tier.
- Lazy judge construction: an LLM judge is instantiated only when the
  first case actually escalates, and it's cached on the hybrid
  instance by model name. Rule-only runs pay zero LiteLLM import cost.
- Honours `EVALOPS_LLM_JUDGE=stub` — hybrid + `make smoke` now exercises
  the full funnel end-to-end without a LiteLLM API key.

### 3. τ-bench-lite benchmark — 20 agent cases ✅

`datasets/tau-bench-lite/` checked in as 3 YAML case files plus a
benchmark manifest. Structure:

| Subset                    | Count | Capability                             |
|---------------------------|------:|----------------------------------------|
| Single-step lookup (RAG)  | 6     | `tool_selection`, `single_step`        |
| Multi-hop                 | 1     | `multi_hop`, `plan_quality`            |
| Multi-tool (RAG + calc)   | 4     | `multi_tool`, `plan_quality`           |
| file_read                 | 3     | `tool_selection`, `single_step`        |
| Unanswerable              | 3     | `unanswerable`, `faithfulness`         |
| Error recovery            | 3     | `error_recovery`, `plan_quality`       |

Every case carries **both** an `input.preset_plan` (so the sidecar's
ReAct executor produces a byte-identical trace every time, keeping
Agent-as-a-Judge scores stable for release gates) **and** an
`expected.trace` (so the `MockAdapter` can replay the benchmark
offline with no sidecar). The two representations are kept in sync
by construction.

Fixtures for the `file_read` tool — `product-specs.md` and
`release-notes.txt` — live under
`sut-extensions/reference-agent-sidecar/sandbox/`.

### 4. ReferenceAdapter `preset_plan` forwarding ✅

`adapters/reference.py:_agent` now forwards
`case.input.preset_plan` verbatim to the sidecar's
`/api/v1/agent/run`. The sidecar's `Planner.propose` already uses
the preset plan path when present, which means τ-bench-lite against
the real sidecar reproduces the same trace the MockAdapter replays
from `expected.trace` — full isomorphism between online and offline.

### 5. Python OTel + Prometheus (`observability/`) ✅

Brand-new subpackage with two modules:

#### `observability.metrics` — process-local Prometheus registry

Seven metric families, all prefixed `evalops_ee_*` so they coexist
with the Go control-plane's `evalops_cp_*` / `evalops_*` series on
the same Grafana dashboard:

| Metric                                     | Type      | Labels                 |
|--------------------------------------------|-----------|------------------------|
| `evalops_ee_runs_total`                    | Counter   | benchmark, sut, status |
| `evalops_ee_judge_calls_total`             | Counter   | kind, model            |
| `evalops_ee_judge_cost_micro_usd_total`    | Counter   | kind, model            |
| `evalops_ee_run_duration_seconds`          | Histogram | benchmark, sut         |
| `evalops_ee_case_duration_seconds`         | Histogram | benchmark, sut, kind   |
| `evalops_ee_run_pass_rate`                 | Gauge     | benchmark, sut         |
| `evalops_ee_run_judge_agreement`           | Gauge     | benchmark, sut         |

Design choices:

- **Process-local `CollectorRegistry`**, not the default global. Short
  CLI runs otherwise drown the Prometheus text output in Python
  runtime noise, and our tests would have to paper over metric
  duplication on re-import. A dedicated registry also lets pytest
  assert on exact counter values without racing background exporters.
- HTTP exporter is **optional**. `start_metrics_server(port)` is only
  invoked when `--metrics-port` / `EVALOPS_PROMETHEUS_PORT` is set.
  CLI smokes skip it entirely; a long-lived Week 4 batch runner can
  enable it and Prometheus will scrape for the process lifetime.
- `record_*` helpers bury label-building at the call site so the
  RunnerEngine doesn't need to know about Prometheus internals.

#### `observability.tracing` — OTel spans with lazy SDK import

- `configure_tracing(endpoint=...)` installs a `TracerProvider` with
  an OTLP HTTP exporter only when the endpoint is non-empty. When
  unset (dev / CI / offline), OTel falls back to a no-op tracer and
  the runner's `with run_span(...)` / `with case_span(...)` blocks
  are free.
- SDK imports (`opentelemetry.sdk.trace`, `OTLPSpanExporter`) are
  deferred inside `configure_tracing` so the test suite stays fast
  and the CLI can run with `EVALOPS_OTEL_EXPORTER_ENDPOINT=""` as
  the default.
- Span attributes mirror the Prometheus label set — `evalops.run_id`,
  `evalops.case_id`, `evalops.benchmark`, `evalops.sut`,
  `evalops.case_kind` — so Tempo / Jaeger traces can be joined
  against Prometheus time series on the same panels.

#### Runner integration

- `RunnerEngine.run` wraps the entire run in a `run_span` and emits
  `record_run_start` + `record_run_finish`.
- `_run_one` wraps each case in a `case_span` (automatically a child
  of the run span via OTel context propagation) and calls
  `record_case_done` on every terminal path — including sut_error
  and judge_error — so the histogram reflects real wall time.
- `_emit_run_metrics` attributes total judge cost to the outer
  judge_config's kind/model. Week 4 will split this per tier when
  the hybrid judge starts reporting tier-level cost back to the
  runner.

#### Grafana dashboard refresh

`infra/grafana/dashboards/evalops-overview.json` gained 4 new panels:

- Eval-engine runs/sec by status (started / succeeded / partial / failed)
- Judge cost burn per tier (kind × model)
- Dual-judge Cohen's κ gauge (-1 == N/A)
- Case duration p95 by benchmark × kind

The existing control-plane panels are unchanged so the dashboard
stays diffable.

### 6. CLI surface expansion ✅

`evalops run` grew three new flags:

- `--judge-model` — LiteLLM model name for llm / agent / hybrid tiers
  (e.g. `gpt-4o`, `claude-3-5-sonnet-20240620`, `zhipu/glm-4-plus`).
- `--judge-baseline-model` — secondary provider for `llm_dual` and
  the agent-judge model inside hybrid.
- `--metrics-port` — starts the Prometheus HTTP exporter on the
  given port for the duration of the run.

Dataset-side, `agent_trace` is now one of the legal `--judge`
values, alongside `hybrid`.

## Key demonstration — τ-bench-lite against the mock SUT

```
$ evalops run --benchmark datasets/tau-bench-lite --sut mock --judge rule \
              --out runs/week3-tbl-mock.json

Run <id> · tau-bench-lite @ v0.1.0 · SUT=mock
  Status       succeeded
  Cases        20
  Pass rate    95.0%
  Cost (µUSD)  9000

  agent/error_recovery  0.667   ← 2/3 recovery cases retry, 1 gives up
  agent/final_em        0.950
  agent/final_f1        0.950
  agent/plan_efficiency 1.000
  agent/tool_selection  1.000
```

`tbl-020-recovery-gave-up` is the designed negative case — the
agent gave up after the first calc error, no retry, so
`agent/error_recovery` scores 0 and the case fails. The remaining
19/20 pass under the rule judge. Swap in `--judge hybrid
--judge-model gpt-4o --judge-baseline-model gpt-4o-mini` to also
get per-dim `agent_judge/*` scores via the real LLM tiers.

## Test summary

```
pytest -q                                      →  64 passed in 3.5s
   test_metrics.py                                 23 tests
   test_runner_mock.py                              3 tests
   test_judge_llm.py                               13 tests
   test_runner_dual_judge.py                        3 tests
   test_runner_resume.py                            4 tests
   test_judge_agent.py                              6 tests (NEW W3)
   test_judge_hybrid.py                             5 tests (NEW W3)
   test_tau_bench_lite.py                           4 tests (NEW W3)
   test_observability.py                            5 tests (NEW W3)

go vet ./... && go build ./... && go test -race  →  clean
ruff check src tests                             →  all checks passed
./scripts/deploy-sidecar.sh --check              →  in sync
```

## Deferred to Week 4

| Item                                          | Week |
|------------------------------------------------|-----|
| Bad-case harvester (Jaeger → regression set)   | 4 |
| Release Gate (GitHub Actions on application PR) | 4 |
| Capability radar panel (Grafana)               | 4 |
| Web dashboard beyond the placeholder           | 4 |
| ClickHouse OLAP                                | 4 |
| Per-tier cost attribution in hybrid            | 4 |
| Real-API dual-judge smoke against OpenAI+Claude | 4 (optional, $$) |

## Verification checklist

```bash
# 1. Python unit + integration
cd services/eval-engine && pytest -q

# 2. Ruff
cd services/eval-engine && ruff check src tests

# 3. Go
cd services/control-plane && go vet ./... && go build ./... && go test -race ./...

# 4. Proto drift
make proto-check

# 5. Web
cd web/frontend && npm run typecheck && npm run build

# 6. Sidecar sync
./scripts/deploy-sidecar.sh --check

# 7. τ-bench-lite smoke against the mock (offline)
cd services/eval-engine && evalops run \
    --benchmark ../../datasets/tau-bench-lite --sut mock --judge rule \
    --out /tmp/tbl.json

# 8. Hybrid funnel smoke with stub LLM
cd services/eval-engine && EVALOPS_LLM_JUDGE=stub evalops run \
    --benchmark ../../datasets/rag-toy --sut mock --judge hybrid \
    --out /tmp/hybrid.json

# 9. Prometheus exporter smoke
cd services/eval-engine && evalops run \
    --benchmark ../../datasets/rag-toy --sut mock --judge rule \
    --metrics-port 9100 --out /tmp/metrics.json &
curl -s http://localhost:9100/metrics | grep evalops_ee_
```
