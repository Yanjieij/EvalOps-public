# Week 2 Delivery Status

**Milestone**: *Week 2 · evaluation core link* from
`~/.claude/plans/eager-zooming-rabin.md`.

**Goal**: every Week 1 scaffold becomes a loadbearing link. Real public
benchmark, real LLM-backed judge, real run-level statistical agreement,
and three foundation guards (buf, CI, web) so Week 3 can move fast
without losing its footing.

## Delivered

### 1. Foundation: buf proto pipeline ✅
- `proto/buf.gen.yaml` with remote buf.build plugins — no local protoc
- Go stubs at `services/control-plane/internal/proto/evalops/v1/`
- Python stubs at `services/eval-engine/src/evalops/v1/` (deliberate
  path so protobuf's own `from evalops.v1 import ...` internal imports
  resolve natively without protoletariat)
- Committed stubs — `go build` and `pytest` don't need buf locally
- Relaxed `RPC_REQUEST_RESPONSE_UNIQUE` and `RPC_RESPONSE_STANDARD_NAME`
  (internal API by design reuses Benchmark / Run across RPCs)
- `make proto-lint`, `make proto-gen`, `make proto-check` (drift guard)

### 2. Foundation: GitHub Actions CI ✅
- `.github/workflows/ci.yml` with 6 independent jobs:
  - `eval-engine` — py 3.11 + 3.12 matrix, ruff + pytest coverage
  - `control-plane` — go 1.23, vet + build + `-race` tests
  - `agent-sidecar` — import smoke against the mirrored source
  - `proto` — `buf lint` + `buf generate` drift guard
  - `sidecar-sync` — lints the sidecar source tree and the deploy script
  - `web` — `npm ci && npm run typecheck && npm run build`
- `concurrency: ci-<ref>` cancels stale runs on the same branch

### 3. Foundation: Web placeholder ✅
- `web/frontend/` Vite + React 18 + TypeScript + Ant Design 5
- Single informational page listing the four EvalOps pillars with
  per-week status tags — enough for CI to typecheck and build
- Dev server on port 5180 with `/api → :8090` proxy to the control-plane
- `package-lock.json` committed so CI has a deterministic install

### 4. LLM-as-a-Judge via LiteLLM ✅
- Added `litellm` as an optional `[llm-judge]` extra — provider-agnostic
  by design. Judge configs name a model like `gpt-4o-2024-08-06`,
  `claude-3-5-sonnet-20240620`, or `zhipu/glm-4-plus` and LiteLLM
  routes it. Adding a new provider is zero code change.
- `LiteLLMClient` wrapper — lazy import so the slim default install
  isn't forced to pull LiteLLM's dep tree. Tests never hit a real API.
- `LLMJudge` handles three judge kinds end-to-end:
  - **LLM_SINGLE**: rubric prompts for `rag/faithfulness` and
    `rag/answer_relevancy`, strict JSON output, markdown-fence tolerant
    parser, self-consistency via configurable `repeats` + stddev
    threshold, per-metric `unstable` flag
  - **LLM_PAIRWISE**: SUT-vs-baseline two-call swap with SUT-centric
    vote reconciliation; disagreement across swaps → TIE + unstable
    (Zheng et al. position-bias mitigation)
  - **LLM_DUAL**: primary + secondary provider; per-case reports the
    mean metric + `llm/dual_bin_agreement` (3-way bin hit rate).
    Cohen's κ is computed ONCE per run in `RunnerEngine._summarize`
    from all stashed `dual_raw_pairs`, then written to
    `RunSummary.judge_agreement`
- `EVALOPS_LLM_JUDGE=stub` env toggles the Week 1 stub for CI / offline
- `judge/prompts.py` — narrow per-metric rubric templates; separate
  metrics → separate prompts → separate calls

### 5. Conda environment discipline ✅
- Moved all Python work off the conda base env onto a dedicated
  `evalops` conda env (Python 3.12)
- Cleaned base env of Week 1 editable installs
  (`evalops-eval-engine`, `reference-agent-sidecar`)
- `pyproject.toml` ruff config extends exclude to `src/evalops/v1`
  (generated proto stubs) and ignores B008 (Typer defaults), RUF001/002
  (ambiguous unicode in prose), UP042 (str+Enum is the pydantic pattern)

### 6. HotpotQA dev-100 subset + loader ✅
- `scripts/fetch-hotpotqa.sh` — downloads the dev distractor split to
  `.cache/hotpotqa/` (gitignored), slices deterministically, writes
  25-per-file YAML chunks for git-diff friendliness
- `evalops.datasets.hotpotqa.raw_to_case_dict` — converts HotpotQA
  format into Case dicts: question → `input.query`, gold answer,
  supporting titles as `source_ids`, gold supporting sentences (so
  Week 3's LLM faithfulness judge has ground truth), difficulty map,
  capability tags (`rag/multi_hop`, `rag/level/<lvl>`, `rag/<type>`)
- `datasets/hotpotqa-dev-100/` checked in — 100 hard multi-hop cases,
  ~1.4 MB. 79 bridge + 21 comparison.

### 7. Deep RAG metrics ✅
- `judge/metrics.py:context_precision(returned, expected)` — dual of
  `citation_recall`, catches verbose retrievers that drown the
  generator. `1.0` when no retrieval happens (degenerate), `0.0` when
  retrieval happens with no ground truth.
- `judge/metrics.py:faithfulness_lite(answer, context)` — token-overlap
  proxy. Catches blatant hallucinations ("capital of France is Berlin"
  vs. a context that doesn't mention Berlin). Cheap signal; Week 3's
  hybrid judge escalates low scores to the real LLM faithfulness judge.
- Both are reported by the rule judge on every RAG case, so existing
  rag-toy runs pick up the new columns automatically.

### 8. Idempotent runner resume ✅
- `RunnerEngine(..., resume_from=prior_run)` — completed, non-errored
  cases from a prior run are carried forward verbatim; only pending
  and previously-errored cases are re-executed
- Resume keeps the original `run_id` so Jaeger trace correlation
  survives a restart
- Full resume (everything already done) is a pure summary rebuild — no
  SUT call happens
- `evalops run --resume runs/prior.json` wires it into the CLI
- 4 new pytest cases in `test_runner_resume.py`:
  - fresh run baseline
  - resume with a truncated prior run skips completed cases
  - prior errors are retried
  - full-resume is a pure summary rebuild with the original run_id

## Key demonstration — HotpotQA against the mock SUT

```
$ evalops run --benchmark datasets/hotpotqa-dev-100 --sut mock \
             --out runs/week2-hotpot.json

Run ... · hotpotqa-dev-100 @ v0.1.0 · SUT=mock
  Status       succeeded
  Cases        100
  Pass rate    100.0%

  rag/exact_match       1.000
  rag/f1                1.000
  rag/substring_match   1.000
  rag/citation_recall   1.000
  rag/context_precision 0.208   ← new: 2 supporting facts / 10 paragraphs
  rag/faithfulness_lite 1.000
```

EM / F1 / citation_recall all say "perfect", but **context_precision
immediately flags** that the mock's retrieval returns 10 paragraphs
when only ~2 are the supporting facts. That's the kind of signal
Week 3's hybrid judge needs to escalate — "retrieval is noisy even
though the answer ends up right" is a real failure mode.

## Test summary

```
pytest -q                                      →  44 passed in 2.5s
   test_metrics.py                                 23 tests
   test_runner_mock.py                              3 tests
   test_judge_llm.py                               13 tests
   test_runner_dual_judge.py                        3 tests
   test_runner_resume.py                            4 tests (new in W2 commit 4)

go vet ./... && go build ./... && go test -race  →  clean
ruff check src tests                             →  all checks passed
buf lint + make proto-check                      →  clean (no drift)
npm run typecheck && npm run build               →  clean
./scripts/deploy-sidecar.sh --check              →  in sync
```

## Deferred to Week 3+

| Item | Week |
|---|---|
| Agent-as-a-Judge trace auditing | Week 3 |
| Hybrid judge funnel (rule → LLM → agent) | Week 3 |
| Dual-judge run against real OpenAI + Anthropic APIs | Week 3 |
| τ-bench / ToolBench subset for agent benchmarks | Week 3 |
| Bad-case harvester (Jaeger → regression set) | Week 4 |
| Release Gate (GitHub Actions on application PR) | Week 4 |
| Capability radar + judge-κ Grafana panels | Week 4 |
| Web dashboard beyond the placeholder | Week 4 |
| ClickHouse OLAP | Week 4 |

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

# 7. Stub LLM judge smoke
EVALOPS_LLM_JUDGE=stub evalops run --benchmark datasets/rag-toy \
    --sut mock --judge llm_single --out runs/smoke-stub.json

# 8. Runner resume round-trip
evalops run --benchmark datasets/rag-toy --sut mock --out runs/r1.json
evalops run --benchmark datasets/rag-toy --sut mock \
    --resume runs/r1.json --out runs/r2.json
```
