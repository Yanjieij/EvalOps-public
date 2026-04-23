"""Pydantic models — the in-process equivalent of the .proto DTOs.

Week 1 deliberately hand-writes these instead of generating from proto. That
lets us ship the end-to-end smoke test without the buf/protoc toolchain and
gives us space to add ergonomics (default factories, validators, helpers)
that raw protobuf messages don't provide.

When Week 2 wires up proto code generation, these models will still live
in this module — they'll just become thin adapters over the generated
`_pb2` classes.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

# -----------------------------------------------------------------------------
# Common primitives
# -----------------------------------------------------------------------------


class CaseKind(str, Enum):
    RAG = "rag"
    AGENT = "agent"
    CHAT = "chat"
    HYBRID = "hybrid"


class SutKind(str, Enum):
    HTTP = "http"
    GRPC = "grpc"
    OPENAI_COMPAT = "openai_compat"
    REFERENCE = "reference"
    MOCK = "mock"


class JudgeKind(str, Enum):
    RULE = "rule"
    LLM_SINGLE = "llm_single"
    LLM_PAIRWISE = "llm_pairwise"
    LLM_DUAL = "llm_dual"
    AGENT_TRACE = "agent_trace"
    HYBRID = "hybrid"


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PARTIAL = "partial"


class Cost(BaseModel):
    micro_usd: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def __add__(self, other: Cost) -> Cost:
        return Cost(
            micro_usd=self.micro_usd + other.micro_usd,
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
        )


class CapabilityTag(BaseModel):
    path: str                         # "rag/faithfulness"
    weight: float = 1.0


class Metadata(BaseModel):
    request_id: str = Field(default_factory=lambda: str(uuid4()))
    trace_id: str = ""
    span_id: str = ""
    run_id: str = ""
    case_id: str = ""
    tenant: str = ""


# -----------------------------------------------------------------------------
# Benchmark & Case
# -----------------------------------------------------------------------------


class Benchmark(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    version: str = "v0.1.0"
    description: str = ""
    taxonomy_root: str = ""           # "rag", "agent", "chat"
    labels: dict[str, str] = Field(default_factory=dict)


class Case(BaseModel):
    id: str                           # stable within a benchmark version
    benchmark_id: str = ""
    kind: CaseKind
    input: dict[str, Any]             # kind-specific payload for the adapter
    expected: dict[str, Any] = Field(default_factory=dict)
    rubric: dict[str, Any] = Field(default_factory=dict)
    capability_tags: list[CapabilityTag] = Field(default_factory=list)
    difficulty: int = 3
    source: str = "synthetic"         # public:* | synthetic | harvest | adversarial
    labels: dict[str, str] = Field(default_factory=dict)


# -----------------------------------------------------------------------------
# SUT I/O — the language that adapters speak
# -----------------------------------------------------------------------------


class SutOutput(BaseModel):
    """Adapter-returned object, uniform across all SUT kinds.

    `answer` is the primary natural-language output. `sources` are RAG
    citations. `agent_trace` is a list of dict steps for Agent cases. These
    are intentionally untyped blobs — the rule / LLM / agent judges know
    how to read them.
    """

    answer: str = ""
    sources: list[dict[str, Any]] = Field(default_factory=list)
    agent_trace: list[dict[str, Any]] = Field(default_factory=list)
    latency_ms: int = 0
    cost: Cost = Field(default_factory=Cost)
    raw: dict[str, Any] = Field(default_factory=dict)   # adapter-specific details
    quality_hint: str = ""                              # X-Eval-Quality-Hint


# -----------------------------------------------------------------------------
# Judge
# -----------------------------------------------------------------------------


class JudgeConfig(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    kind: JudgeKind
    rubric: dict[str, Any] = Field(default_factory=dict)
    model: str = ""
    baseline_model: str = ""
    temperature: float = 0.0
    max_tokens: int = 1024
    repeats: int = 1
    # hash is computed lazily by judge engine's cache layer


class MetricScore(BaseModel):
    name: str
    value: float
    confidence: float = 1.0
    rationale: str = ""


class JudgeResult(BaseModel):
    metrics: list[MetricScore]
    cost: Cost = Field(default_factory=Cost)
    judge_trace: dict[str, Any] = Field(default_factory=dict)
    unstable: bool = False


# -----------------------------------------------------------------------------
# Run
# -----------------------------------------------------------------------------


class Sut(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    kind: SutKind
    endpoint: str = ""
    version_label: str = ""
    auth: dict[str, str] = Field(default_factory=dict)
    capabilities: list[str] = Field(default_factory=list)


class CaseResult(BaseModel):
    case_id: str
    passed: bool
    sut_output: SutOutput
    judge_result: JudgeResult
    latency_ms: int
    error: str = ""

    @property
    def cost(self) -> Cost:
        return self.sut_output.cost + self.judge_result.cost


class RunSummary(BaseModel):
    metrics: dict[str, float] = Field(default_factory=dict)
    total_cost: Cost = Field(default_factory=Cost)
    pass_rate: float = 0.0
    judge_agreement: float = -1.0     # -1 = not applicable
    unstable_cases: int = 0


class Run(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    benchmark: Benchmark
    sut: Sut
    judge_config: JudgeConfig
    status: Literal[
        "pending", "running", "succeeded", "failed", "cancelled", "partial"
    ] = "pending"
    started_at_unix: int = 0
    finished_at_unix: int = 0
    concurrency: int = 4
    results: list[CaseResult] = Field(default_factory=list)
    summary: RunSummary = Field(default_factory=RunSummary)
    idempotency_key: str = ""
