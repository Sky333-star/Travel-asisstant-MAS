"""Observability: OpenTelemetry traces, Prometheus metrics, Langfuse LLM traces."""

from . import langfuse_hook
from .metrics import (
    ACTIVE_SESSIONS,
    CONVERSATIONS,
    LLM_PARSE_FAILURES,
    MCP_SERVERS_UP,
    PLAN_REVISIONS,
    STAGE_ENTERED,
    VALIDATION_ISSUES,
    VALIDATION_RESULT,
    record_llm_call,
    record_mcp_call,
    render_metrics,
    time_agent,
)
from .otel import get_tracer, setup_tracing, shutdown_tracing, span

__all__ = [
    "ACTIVE_SESSIONS",
    "CONVERSATIONS",
    "LLM_PARSE_FAILURES",
    "MCP_SERVERS_UP",
    "PLAN_REVISIONS",
    "STAGE_ENTERED",
    "VALIDATION_ISSUES",
    "VALIDATION_RESULT",
    "get_tracer",
    "langfuse_hook",
    "record_llm_call",
    "record_mcp_call",
    "render_metrics",
    "setup_tracing",
    "shutdown_tracing",
    "span",
    "time_agent",
]
