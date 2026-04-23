# Reference SUT Changeset Manifest — Week 1

> Inventory of every file EvalOps added to the reference-app tree, with risk
> notes, fold-in plan, and how to revert. Read this if you are reviewing
> the Week 1 deliverables or planning the Week 2 production fold-in.

## Principles

1. **Additive-only**: zero existing files modified. Every change is a
   new file, in a new directory.
2. **Isolated runtime**: the new code runs as its own process on its
   own port. The Go gateway and Python ai-engine are unaware of it.
3. **Reversible**: `rm -rf reference-app/services/agent-sidecar/` undoes
   the entire Week 1 changeset with no side effects. There is no
   database migration, no proto regeneration, no gateway config change.
4. **Honestly labelled**: the new service is named "sidecar" in every
   surface so reviewers know it is not yet part of the host app proper.

## Where the code lives

| Role | Path |
|---|---|
| **Source of truth** (version-controlled in this repo) | `evalops/sut-extensions/reference-agent-sidecar/` |
| **Deployed runtime copy** (pip install -e target, additive to the host app) | `reference-app/services/agent-sidecar/` |
| **Sync tool** | `evalops/scripts/deploy-sidecar.sh` (rsync + optional `--reinstall`, `--check` for CI divergence guard) |

The canonical source now lives in the EvalOps repo so git history tracks
every sidecar change. The copy under the host app tree exists purely so
we can still tell the "additive PR against a companion app" story — iterate
in `sut-extensions/`, run `scripts/deploy-sidecar.sh`, the target
directory picks up the change for the next `agent-sidecar` invocation.

## File inventory

All files live under `sut-extensions/reference-agent-sidecar/`
(and are mirrored to `reference-app/services/agent-sidecar/`).

| File | Purpose |
|---|---|
| `pyproject.toml` | Independent hatchling package, own dependency set (fastapi, uvicorn, pydantic, httpx, structlog), console script `agent-sidecar`. |
| `README.md` | Overview, endpoints, failure injection, production fold-in plan. |
| `src/agent_sidecar/__init__.py` | Version string, module docstring explaining additive-only status. |
| `src/agent_sidecar/tools.py` | The 4 locked-in Week 1 tools (rag_query, calc, file_read, mock_web_search) with env-var failure injection. AST-whitelist numeric evaluator, sandboxed file reads, in-memory RAG and web fixtures. |
| `src/agent_sidecar/executor.py` | ReAct-style executor with a deterministic heuristic Planner and a pluggable `Planner` abstraction for Week 2 LLM replacement. Handles tool error recovery. |
| `src/agent_sidecar/server.py` | FastAPI app: `/healthz`, `/agent/tools`, `/agent/run`, `/api/v1/agent/run`. Echoes `X-EvalOps-Run-Id` / `X-EvalOps-Case-Id` in the response, which is the contract that lets the Week 4 bad-case harvester correlate Jaeger traces with EvalOps runs. |

Total: **6 files**, **all brand-new**.

## What was explicitly NOT done

- **No edits to `reference-app/proto/reference/v1/ai_service.proto`.** The
  plan's Week 1 scope mentions adding an `AgentRun` RPC to the shared
  proto, but doing so would force regeneration of Go + Python stubs
  inside the existing services. For Week 1 we side-step that by serving
  the agent surface over HTTP from a fresh service. The proto extension
  is still specced in the EvalOps proto README and will land in Week 2
  together with `buf generate`.
- **No edits to `reference-app/services/gateway/`.** The planned
  `/api/v1/agent/run` route on the Go gateway would need auth
  middleware, rate limiting, and gRPC client wiring. Out of scope for
  Week 1. The sidecar already accepts the correct path
  (`/api/v1/agent/run`) so swapping the gateway in later is a routing
  change, not a client change.
- **No `service_token` endpoint on the gateway.** Instead the EvalOps
  `ReferenceAdapter` was taught to operate auth-less when no
  credentials are configured — which is exactly what the sidecar needs
  and what the Week 2 real service-account flow will eventually
  replace.
- **No modifications to `reference-app/services/ai-engine/`.** The sidecar
  does not import `ai_engine.*` at all; all Week 1 tool behaviour is
  self-contained. Week 2 fold-in will import `ai_engine.rag.pipeline`
  for the real `rag_query` tool.
- **No shared Python package changes.** The sidecar's `pyproject.toml`
  pins its own deps. It does not depend on the ai-engine package or
  share a virtualenv with it.

## Risk assessment

| Risk | Severity | Mitigation |
|---|---|---|
| Sidecar fixture drifts from real production RAG corpus | Low | Fixture is obviously hardcoded; Week 2 replaces with real `ai_engine.rag.pipeline` import. |
| Sidecar port conflicts with existing services | Low | Default port `8081`; override via `AGENT_SIDECAR_PORT`. Host gateway is `8080`; ai-engine gRPC is `50051`. No collision. |
| Unauthenticated agent endpoint leaks onto the internet | High | Bind to `127.0.0.1` in any shared environment (`AGENT_SIDECAR_HOST=127.0.0.1`). Week 2 fold-in puts the real endpoint behind the gateway's JWT middleware. |
| Sidecar process management complicates demos | Medium | `Makefile` target + README explicitly document "start in background" pattern. `healthz` check is <10 LoC to verify. |
| Planner heuristic is brittle | Low | Week 2 replaces with LLM-backed planner; the Week 1 heuristic exists solely to validate the eval pipeline without API costs. |

## Production fold-in plan (Week 2)

This is the actual PR that would ship in a production environment.

1. **Add `AgentRun` RPC to `proto/reference/v1/ai_service.proto`**
   with `AgentRunRequest { Metadata metadata = 1; string task = 2;
   int32 max_steps = 3; repeated string tools = 4; }` and
   `AgentRunResponse { string final_answer = 1; repeated AgentStep
   trace = 2; Usage usage = 3; }`.
2. **Regenerate Go + Python proto stubs** via `buf generate` in both
   EvalOps and the companion app repos.
3. **Move `src/agent_sidecar/{tools,executor}.py`** into
   `reference-app/services/ai-engine/src/ai_engine/agent/`. Delete the
   standalone `server.py`. The executor stays identical; only the
   planner is swapped for an LLM-backed one that calls the existing
   `ai_engine.llm.zhipu_client`.
4. **Implement `AgentRun` on the Python gRPC server** by wiring the
   executor into `grpc_server.py` using the same pattern as the
   existing `Chat` / `RAGQuery` implementations.
5. **Add `POST /api/v1/agent/run` to the Go gateway** with the same
   JWT middleware as `/api/v1/chat`. Handler calls `grpcclient.
   AIClient.AgentRun` and streams the response.
6. **Add `X-EvalOps-*` headers to the OpenTelemetry span attributes**
   via a new piece of gateway middleware. This is what unlocks the
   Week 4 bad-case harvester.
7. **Delete `reference-app/services/agent-sidecar/`** — the code has moved
   into the real services. The EvalOps adapter already points at
   `/api/v1/agent/run` so it continues to work unchanged.

## Reverting Week 1 completely

```bash
rm -rf ./reference-app/services/agent-sidecar/
pip uninstall reference-agent-sidecar -y
```

Nothing else. No config file, no database row, no proto stub needs to
be touched. The source copy under `evalops/sut-extensions/` is
independent and can stay or be deleted as preferred — it has no effect
on the reference app.
