# EvalOps Proto

The source of truth for the wire contract between the Go control-plane, the
Python eval-engine, and any future clients.

## Files

| File | Purpose |
|---|---|
| `evalops/v1/common.proto` | Shared primitives: `Metadata`, `Cost`, `KV`, `CapabilityTag`. |
| `evalops/v1/dataset.proto` | `DatasetService`: benchmark + case CRUD, with idempotent writes so CI can re-submit safely. |
| `evalops/v1/judge.proto`   | `JudgeService`: rule / LLM / agent / hybrid scoring. **Content-addressed `JudgeConfig.hash`** is what makes runs reproducible. |
| `evalops/v1/runner.proto`  | `RunnerService`: Run lifecycle, SUT registration, streaming progress events. This is the main surface the control-plane consumes. |

## Design notes

### Why JSON-in-proto for `input_json` / `expected_json` / `rubric_json`?

Each `CaseKind` (RAG / Agent / Chat / Hybrid) has a different natural
schema. Encoding them as a `oneof` explodes the .proto at every new task
type. Using a JSON blob keeps the contract tight and lets us evolve
per-kind schemas in code without a proto migration each time. The tradeoff
is that the blob is validated at ingest time instead of at wire time — we
accept that for flexibility.

### Why content-address `JudgeConfig` with a SHA hash?

Two runs with different judge names but identical rubric / model / temperature
should be comparable; two runs that *look* identical but use a different
rubric must not. Hashing the canonical form of the config is the cleanest
way to enforce this — the hash becomes the cache key for judge results and
the equality predicate in the control-plane.

### Why is `SutKind.REFERENCE` first-class?

The reference SUT deserves a dedicated adapter path so we can:

1. Propagate `evalops.run_id` / `evalops.case_id` as OpenTelemetry span
   attributes into the SUT, which makes the bad-case harvester trivial
   (one Jaeger query).
2. Use the gRPC internal interface (`AIService` + the new `AgentService`)
   directly, bypassing HTTP+JWT overhead during batch evaluation.
3. Encode the `X-Eval-Quality-Hint` response header contract.

Generic HTTP / OpenAI-compat adapters remain for third-party SUTs.

### Why `JUDGE_KIND_LLM_DUAL` instead of generic multi-judge?

It's a decision we made up-front (see the plan's §15/§16): we use two
different LLM judges (GPT-4o + Claude 3.5 Sonnet) to compute Cohen's kappa
as our "no-human-annotator-available" proxy for inter-annotator agreement.
Giving it a first-class enum value means dashboards and run summaries can
display the kappa directly without special-casing.

## Code generation (TBD)

Week 1 hand-writes the Python and Go DTOs that the eval-engine and
control-plane actually use, so we don't block on the `buf` toolchain.
Week 2 wires up `buf generate` with `protoc-gen-go` + `protoc-gen-go-grpc`
+ `grpc_tools.protoc` (Python).
