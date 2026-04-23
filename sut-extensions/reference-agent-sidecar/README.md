# Agent Sidecar — additive reference-app extension for EvalOps

This service is the **additive-only** changeset that makes a reference app
evaluatable by the EvalOps agent benchmarks. It lives in its own
directory under `reference-app/services/agent-sidecar/` and touches **zero
existing files** in the host app. It is a separate Python package with its
own `pyproject.toml`, runs as its own process on port `8081`, and is
meant to be started alongside the Go gateway and Python ai-engine.

## Why a sidecar instead of folding into ai-engine

Production PR plan: the ReAct executor lands inside `ai_engine` as a new
`agent/` subpackage, exposed through a new `AgentRun` gRPC RPC on the
existing `AIService`. The Go gateway gains `/api/v1/agent/run` wired to
that RPC via `grpcclient.AIClient`.

For Week 1 we keep it as a sidecar so:

1. Zero risk of regressing the existing host app tests / deployment.
2. No protobuf regeneration dance while the eval engine is in flux.
3. The EvalOps `ReferenceAdapter` can point at whatever endpoint is
   convenient (`http://localhost:8081` for local dev, or the sidecar
   behind the real gateway in CI).

See `../../../../evalops/docs/reference-sut-changeset.md` for the full
additive-manifest, risk notes, and the Week 2 fold-in plan.

## Start it

```bash
cd reference-app/services/agent-sidecar
pip install -e .
agent-sidecar                     # listens on :8081 by default
```

Or with a different port:

```bash
AGENT_SIDECAR_PORT=18081 agent-sidecar
```

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET  | `/healthz` | Liveness |
| GET  | `/agent/tools` | List registered tool names |
| POST | `/agent/run` | Execute the agent on a task |
| POST | `/api/v1/agent/run` | Same as above, path-compatible with the planned gateway route |

### POST /agent/run

```json
{
  "task": "What is the capital of France?",
  "max_steps": 4,
  "tools": ["rag_query", "calc", "file_read", "mock_web_search"],
  "preset_plan": null
}
```

Response:

```json
{
  "final_answer": "Paris",
  "trace": [
    {
      "step": 0,
      "thought": "look up the fact in the knowledge base",
      "action": {"tool": "rag_query", "args": {"collection": "toy-geography", "query": "What is the capital of France?"}},
      "observation": {"answer": "Paris", "sources": [{"id": "toy-geography-france capital", "content": "france capital: Paris", "score": 0.9}]}
    }
  ],
  "latency_ms": 3,
  "steps": 1,
  "run_id": "echo-of-X-EvalOps-Run-Id",
  "case_id": "echo-of-X-EvalOps-Case-Id"
}
```

## Tools

| Name | Contract | Failure injection key |
|---|---|---|
| `rag_query(collection, query, top_k)` | Looks up a canned answer from an in-memory fixture. Week 2 replaces with real `ai_engine.rag.pipeline`. | `rag_query` |
| `calc(expression)` | AST-whitelist numeric eval (no `eval`). | `calc` |
| `file_read(path, max_bytes)` | Sandboxed file read under `AGENT_SIDECAR_SANDBOX`. | `file_read` |
| `mock_web_search(query)` | Deterministic "search engine" from a fixture dict. | `mock_web_search` |

### Failure injection

```bash
AGENT_SIDECAR_FAIL_TOOLS=mock_web_search \
AGENT_SIDECAR_FAIL_MODE=error \
agent-sidecar
```

This drives EvalOps's `agent/error_recovery` rubric — deterministic,
reproducible, no real distributed outage needed.

## Metadata propagation

The sidecar accepts and echoes two headers EvalOps injects:

- `X-EvalOps-Run-Id`
- `X-EvalOps-Case-Id`

In the production PR these become OpenTelemetry span attributes, which
is what makes the Week-4 bad-case harvester trivial (one Jaeger query
by run id).
