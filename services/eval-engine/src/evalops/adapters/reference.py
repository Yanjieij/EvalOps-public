"""Reference SUT HTTP adapter.

Targets the Go gateway at /api/v1/{chat,knowledge/query,agent/run}.  The
agent endpoint is part of the additive-only SUT changeset documented in
docs/reference-sut-changeset.md — if the SUT doesn't have it yet, Agent cases
fail with a clear "agent endpoint not available" error rather than
silently degrading.

We propagate `X-Request-ID` and inject two custom headers:

- ``X-EvalOps-Run-Id``   so the SUT can tag its OpenTelemetry spans
- ``X-EvalOps-Case-Id``  same, at case granularity

Both are consumed by the additive reference-app middleware and end up as span
attributes in Jaeger, which makes the bad-case harvester a simple trace
query (`run_id=... AND status=error`).
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from evalops.logging import get_logger
from evalops.models import Case, CaseKind, Cost, Metadata, Sut, SutOutput

from .base import SutAdapter

log = get_logger(__name__)


class ReferenceAdapter(SutAdapter):
    def __init__(self, sut: Sut) -> None:
        super().__init__(sut)
        base_url = sut.endpoint or "http://localhost:8080"
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(float(sut.auth.get("timeout_s", "60"))),
        )
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---- auth ----------------------------------------------------------------

    async def _ensure_token(self) -> str | None:
        """Return a JWT for the reference gateway, or None if auth-less.

        Resolution order:
        1. No auth config at all → return None (anonymous). This is how
           the Agent sidecar in ``reference-app/services/agent-sidecar/``
           is reached: it has no JWT middleware.
        2. `auth.token` pre-minted → use it as a Bearer token.
        3. `auth.user` + `auth.password` → login to /api/v1/auth/login
           and cache the returned access token + expiry.
        """
        if self._token and time.time() < self._token_expires_at - 30:
            return self._token

        static = self.sut.auth.get("token")
        if static:
            self._token = static
            self._token_expires_at = time.time() + 3600
            return self._token

        email = self.sut.auth.get("user")
        password = self.sut.auth.get("password")
        if not email or not password:
            return None  # anonymous — used by the sidecar path

        resp = await self._client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._token_expires_at = float(data.get("expires_at", time.time() + 3600))
        return self._token

    async def _headers(self, metadata: Metadata) -> dict[str, str]:
        headers: dict[str, str] = {
            "X-Request-ID": metadata.request_id,
            "X-EvalOps-Run-Id": metadata.run_id,
            "X-EvalOps-Case-Id": metadata.case_id,
            "Content-Type": "application/json",
        }
        token = await self._ensure_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    # ---- dispatch ------------------------------------------------------------

    async def call(self, case: Case, metadata: Metadata) -> SutOutput:
        started = time.perf_counter()
        if case.kind == CaseKind.RAG:
            out = await self._rag(case, metadata)
        elif case.kind == CaseKind.CHAT:
            out = await self._chat(case, metadata)
        elif case.kind == CaseKind.AGENT:
            out = await self._agent(case, metadata)
        else:
            raise ValueError(f"Reference adapter does not handle {case.kind}")
        out.latency_ms = int((time.perf_counter() - started) * 1000)
        return out

    async def _rag(self, case: Case, metadata: Metadata) -> SutOutput:
        payload = {
            "query": case.input.get("query") or case.input.get("question"),
            "collection": case.input.get("collection", ""),
            "top_k": case.input.get("top_k", 5),
            "min_score": case.input.get("min_score", 0.0),
        }
        resp = await self._client.post(
            "/api/v1/knowledge/query",
            json=payload,
            headers=await self._headers(metadata),
        )
        resp.raise_for_status()
        body = resp.json()
        usage = body.get("usage") or {}
        return SutOutput(
            answer=body.get("answer", ""),
            sources=body.get("sources", []),
            cost=Cost(
                prompt_tokens=int(usage.get("prompt_tokens", 0)),
                completion_tokens=int(usage.get("completion_tokens", 0)),
            ),
            raw=body,
            quality_hint=resp.headers.get("X-Eval-Quality-Hint", ""),
        )

    async def _chat(self, case: Case, metadata: Metadata) -> SutOutput:
        payload = {
            "message": case.input.get("message"),
            "conversation_id": case.input.get("conversation_id", ""),
            "collection": case.input.get("collection", ""),
        }
        resp = await self._client.post(
            "/api/v1/chat/sync",
            json=payload,
            headers=await self._headers(metadata),
        )
        resp.raise_for_status()
        body = resp.json()
        usage = body.get("usage") or {}
        return SutOutput(
            answer=body.get("message", ""),
            sources=body.get("sources", []),
            cost=Cost(
                prompt_tokens=int(usage.get("prompt_tokens", 0)),
                completion_tokens=int(usage.get("completion_tokens", 0)),
            ),
            raw=body,
        )

    async def _agent(self, case: Case, metadata: Metadata) -> SutOutput:
        # This endpoint is part of the Week 1 additive changeset.  Until
        # deployed it returns 404, which will surface as a clear error in
        # the runner.
        #
        # ``case.input.preset_plan`` (Week 3) forwards a canonical plan
        # straight through to the sidecar's executor, which bypasses the
        # heuristic planner.  The τ-bench-lite benchmark uses this for
        # byte-identical traces on every run so Agent-as-a-Judge scores
        # are deterministic and CI doesn't flap.
        payload: dict[str, Any] = {
            "task": case.input.get("task"),
            "max_steps": case.input.get("max_steps", 8),
            "tools": case.input.get("tools", []),
        }
        if "preset_plan" in case.input:
            payload["preset_plan"] = case.input["preset_plan"]
        resp = await self._client.post(
            "/api/v1/agent/run",
            json=payload,
            headers=await self._headers(metadata),
        )
        resp.raise_for_status()
        body = resp.json()
        return SutOutput(
            answer=body.get("final_answer", ""),
            agent_trace=body.get("trace", []),
            raw=body,
        )
