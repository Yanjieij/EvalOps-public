"""FastAPI surface for the Agent sidecar.

Endpoints:
- GET  /healthz           — liveness
- POST /agent/run         — execute the agent on a task and return a trace
- POST /api/v1/agent/run  — alias that matches the Go gateway's path style

The EvalOps adapter hits either path transparently.
"""

from __future__ import annotations

import os
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, Request
from pydantic import BaseModel, Field

from .executor import AgentExecutor


class AgentRunRequest(BaseModel):
    task: str
    max_steps: int = 6
    tools: list[str] | None = None
    preset_plan: list[dict[str, Any]] | None = None  # test-only: bypass the planner


class AgentRunResponse(BaseModel):
    final_answer: str
    trace: list[dict[str, Any]]
    latency_ms: int
    steps: int
    run_id: str = Field(default="", description="Echo of X-EvalOps-Run-Id if present")
    case_id: str = Field(default="", description="Echo of X-EvalOps-Case-Id if present")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Reference App Agent Sidecar",
        version="0.1.0",
        description=(
            "Minimal ReAct-style Agent surface added to a reference app so "
            "EvalOps can evaluate multi-step / tool-use capabilities. "
            "Additive-only: no existing application files modified."
        ),
    )
    executor = AgentExecutor()

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/agent/tools")
    async def list_tools() -> dict[str, list[str]]:
        from .tools import TOOL_REGISTRY
        return {"tools": sorted(TOOL_REGISTRY.keys())}

    async def _run(
        req: AgentRunRequest,
        run_id: str,
        case_id: str,
    ) -> AgentRunResponse:
        result = executor.run(
            task=req.task,
            max_steps=req.max_steps,
            preset_plan=req.preset_plan,
        )
        return AgentRunResponse(
            final_answer=result["final_answer"],
            trace=result["trace"],
            latency_ms=result["latency_ms"],
            steps=result["steps"],
            run_id=run_id,
            case_id=case_id,
        )

    @app.post("/agent/run", response_model=AgentRunResponse)
    async def agent_run(
        body: AgentRunRequest,
        _request: Request,
        x_evalops_run_id: str = Header(default=""),
        x_evalops_case_id: str = Header(default=""),
    ) -> AgentRunResponse:
        return await _run(body, x_evalops_run_id, x_evalops_case_id)

    @app.post("/api/v1/agent/run", response_model=AgentRunResponse)
    async def api_agent_run(
        body: AgentRunRequest,
        _request: Request,
        x_evalops_run_id: str = Header(default=""),
        x_evalops_case_id: str = Header(default=""),
    ) -> AgentRunResponse:
        return await _run(body, x_evalops_run_id, x_evalops_case_id)

    return app


app = create_app()


def main() -> None:
    host = os.getenv("AGENT_SIDECAR_HOST", "0.0.0.0")
    port = int(os.getenv("AGENT_SIDECAR_PORT", "8081"))
    uvicorn.run(
        "agent_sidecar.server:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
