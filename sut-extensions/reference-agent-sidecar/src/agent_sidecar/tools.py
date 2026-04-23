"""Agent tools — the 4 capabilities EvalOps's agent benchmarks exercise.

Every tool honours a deterministic failure injection knob via two env vars:

    AGENT_SIDECAR_FAIL_TOOLS   — comma-separated tool names to fail
    AGENT_SIDECAR_FAIL_MODE    — "error" (raise) | "empty" (return nothing)

This is what makes the Agent `error_recovery` rubric testable without a
real distributed outage.
"""

from __future__ import annotations

import ast
import operator
import os
from pathlib import Path
from typing import Any

# ---------- failure injection -------------------------------------------------

_FAIL_TOOLS: set[str] = set(
    filter(None, os.getenv("AGENT_SIDECAR_FAIL_TOOLS", "").split(","))
)
_FAIL_MODE = os.getenv("AGENT_SIDECAR_FAIL_MODE", "error")


class ToolError(Exception):
    """Raised when a tool fails. The executor catches this and records it
    as a failed observation, giving the Agent a chance to recover."""


def _maybe_fail(tool_name: str) -> None:
    if tool_name in _FAIL_TOOLS:
        if _FAIL_MODE == "empty":
            raise ToolError(f"{tool_name}: injected empty response")
        raise ToolError(f"{tool_name}: injected failure")


# ---------- RAG query ---------------------------------------------------------

# Tiny in-memory RAG fixture so Week 1 runs zero-config. Week 2 replaces
# this with ai_engine.rag.pipeline import for real retrieval.
_RAG_FIXTURE: dict[str, dict[str, str]] = {
    "toy-geography": {
        "france capital": "Paris",
        "capital of france": "Paris",
        "japan capital": "Tokyo",
        "germany capital": "Berlin",
    },
    "toy-product": {
        "response time": "1200 ms",
        "sla": "99.9% monthly uptime",
    },
    "toy-astronomy": {
        "largest planet": "Jupiter",
    },
}


def rag_query(collection: str, query: str, top_k: int = 3) -> dict[str, Any]:
    """Look up a canned answer in the fixture."""
    _maybe_fail("rag_query")
    q = (query or "").lower().strip()
    col = _RAG_FIXTURE.get(collection, {})
    for key, val in col.items():
        if key in q:
            return {
                "answer": val,
                "sources": [
                    {"id": f"{collection}-{key}", "content": f"{key}: {val}", "score": 0.9}
                ],
            }
    return {"answer": "", "sources": []}


# ---------- Calculator --------------------------------------------------------

_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARYOPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _eval_ast(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval_ast(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ToolError(f"calc: unsupported constant {node.value!r}")
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        return _BINOPS[type(node.op)](_eval_ast(node.left), _eval_ast(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARYOPS:
        return _UNARYOPS[type(node.op)](_eval_ast(node.operand))
    raise ToolError(f"calc: disallowed syntax {ast.dump(node)}")


def calc(expression: str) -> dict[str, Any]:
    """Evaluate a numeric expression using an AST whitelist (no `eval`)."""
    _maybe_fail("calc")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ToolError(f"calc: parse error {exc}") from exc
    result = _eval_ast(tree)
    # Normalize to int if we can — purely cosmetic for the trace.
    if result == int(result):
        result_repr: int | float = int(result)
    else:
        result_repr = result
    return {"result": result_repr}


# ---------- file_read (sandboxed) ---------------------------------------------

_SANDBOX_ROOT = Path(
    os.getenv(
        "AGENT_SIDECAR_SANDBOX",
        str(Path(__file__).resolve().parents[3] / "sandbox"),
    )
).resolve()


def file_read(path: str, max_bytes: int = 8192) -> dict[str, Any]:
    """Read a file under the sandbox root."""
    _maybe_fail("file_read")
    requested = (_SANDBOX_ROOT / path).resolve()
    if not str(requested).startswith(str(_SANDBOX_ROOT)):
        raise ToolError(f"file_read: path escapes sandbox: {path}")
    if not requested.exists():
        return {"content": "", "exists": False}
    data = requested.read_bytes()[:max_bytes]
    return {
        "content": data.decode("utf-8", errors="replace"),
        "exists": True,
        "bytes": len(data),
    }


# ---------- mock_web_search ---------------------------------------------------

_WEB_FIXTURE: dict[str, str] = {
    "paris weather": "Sunny, 18°C",
    "tokyo weather": "Rainy, 22°C",
    "python 4 release": "Python 4 has not been released.",
}


def mock_web_search(query: str) -> dict[str, Any]:
    """Deterministic 'search engine' for agent eval stability."""
    _maybe_fail("mock_web_search")
    q = (query or "").lower().strip()
    for key, val in _WEB_FIXTURE.items():
        if key in q:
            return {"results": [{"title": key, "snippet": val}]}
    return {"results": []}


# ---------- registry ---------------------------------------------------------

TOOL_REGISTRY: dict[str, Any] = {
    "rag_query": rag_query,
    "calc": calc,
    "file_read": file_read,
    "mock_web_search": mock_web_search,
}
