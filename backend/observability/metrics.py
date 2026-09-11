"""Prometheus metrics for the agent system.

The metric set is chosen to answer the questions that actually come up when a
multi-agent system misbehaves:

* *Which agent is slow?*            -> ``wayfarer_agent_duration_seconds``
* *Is an MCP server failing?*       -> ``wayfarer_mcp_calls_total{ok="false"}``
* *Is the LLM the bottleneck?*      -> ``wayfarer_llm_latency_seconds``
* *Are we burning tokens?*          -> ``wayfarer_llm_tokens_total``
* *Do plans pass on the first try?* -> ``wayfarer_plan_revisions``
* *Where do users drop off?*        -> ``wayfarer_stage_entered_total``

Everything degrades to a no-op if ``prometheus_client`` is absent, so metrics
are never a hard dependency.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from ..config import settings

logger = logging.getLogger(__name__)

_ENABLED = settings.metrics_enabled

try:  # pragma: no cover - exercised implicitly
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )
except ImportError:  # pragma: no cover
    _ENABLED = False
    CONTENT_TYPE_LATEST = "text/plain"

    def generate_latest() -> bytes:  # type: ignore[misc]
        return b"# prometheus_client not installed\n"


class _NoopMetric:
    def labels(self, *_args: Any, **_kwargs: Any) -> _NoopMetric:
        return self

    def inc(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def observe(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def set(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _counter(name: str, doc: str, labels: tuple[str, ...] = ()) -> Any:
    if not _ENABLED:
        return _NoopMetric()
    try:
        return Counter(name, doc, labels)
    except ValueError:
        # Duplicate registration happens under test reloads; reuse is fine.
        return _NoopMetric()


def _histogram(name: str, doc: str, labels: tuple[str, ...] = (),
               buckets: tuple[float, ...] | None = None) -> Any:
    if not _ENABLED:
        return _NoopMetric()
    try:
        kwargs: dict[str, Any] = {"buckets": buckets} if buckets else {}
        return Histogram(name, doc, labels, **kwargs)
    except ValueError:
        return _NoopMetric()


def _gauge(name: str, doc: str, labels: tuple[str, ...] = ()) -> Any:
    if not _ENABLED:
        return _NoopMetric()
    try:
        return Gauge(name, doc, labels)
    except ValueError:
        return _NoopMetric()


# ---- Agent-level metrics ----
AGENT_DURATION = _histogram(
    "wayfarer_agent_duration_seconds",
    "Wall-clock time spent inside each agent node.",
    ("node",),
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 45, 90),
)
AGENT_ERRORS = _counter(
    "wayfarer_agent_errors_total", "Exceptions raised inside an agent node.", ("node",)
)
STAGE_ENTERED = _counter(
    "wayfarer_stage_entered_total", "Times the graph entered a stage.", ("stage",)
)

# ---- MCP metrics ----
MCP_CALLS = _counter(
    "wayfarer_mcp_calls_total", "MCP tool invocations.", ("server", "tool", "ok")
)
MCP_LATENCY = _histogram(
    "wayfarer_mcp_latency_seconds",
    "MCP tool round-trip latency.",
    ("server", "tool"),
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 45),
)
MCP_SERVERS_UP = _gauge(
    "wayfarer_mcp_servers_up", "1 when an MCP server session is live.", ("server",)
)

# ---- LLM metrics ----
LLM_CALLS = _counter(
    "wayfarer_llm_calls_total", "LLM chat-completion calls.", ("model", "purpose", "ok")
)
LLM_LATENCY = _histogram(
    "wayfarer_llm_latency_seconds",
    "LLM call latency.",
    ("model", "purpose"),
    buckets=(0.25, 0.5, 1, 2, 4, 8, 15, 30, 60, 120),
)
LLM_TOKENS = _counter(
    "wayfarer_llm_tokens_total", "Tokens consumed.", ("model", "kind")
)
LLM_PARSE_FAILURES = _counter(
    "wayfarer_llm_parse_failures_total",
    "Structured-output parses that failed and needed repair or fallback.",
    ("purpose",),
)

# ---- Outcome metrics ----
PLAN_REVISIONS = _histogram(
    "wayfarer_plan_revisions",
    "Revisions needed before a plan passed validation.",
    (),
    buckets=(0, 1, 2, 3, 4, 5),
)
VALIDATION_RESULT = _counter(
    "wayfarer_validation_result_total", "Validator verdicts.", ("passed",)
)
VALIDATION_ISSUES = _counter(
    "wayfarer_validation_issues_total", "Validation issues raised.", ("code", "severity")
)
CONVERSATIONS = _counter(
    "wayfarer_conversations_total", "Conversations started.", ("input_mode",)
)
ACTIVE_SESSIONS = _gauge("wayfarer_active_sessions", "Graph runs currently executing.")


# LangGraph signals a human-in-the-loop pause by raising GraphInterrupt, and
# cancellation raises CancelledError. Neither is an agent failure, and counting
# them as errors would make the error rate track how often users are asked a
# question. Matched by name so this module keeps no LangGraph import.
_CONTROL_FLOW_EXCEPTIONS = {"GraphInterrupt", "GraphBubbleUp", "CancelledError"}


@contextmanager
def time_agent(node: str) -> Iterator[None]:
    """Time an agent node and count genuine failures."""
    started = time.perf_counter()
    try:
        yield
    except BaseException as exc:
        if type(exc).__name__ not in _CONTROL_FLOW_EXCEPTIONS:
            AGENT_ERRORS.labels(node=node).inc()
        raise
    finally:
        AGENT_DURATION.labels(node=node).observe(time.perf_counter() - started)


def record_mcp_call(server: str, tool: str, seconds: float, ok: bool) -> None:
    MCP_CALLS.labels(server=server, tool=tool, ok=str(ok).lower()).inc()
    MCP_LATENCY.labels(server=server, tool=tool).observe(seconds)


def record_llm_call(
    model: str,
    purpose: str,
    seconds: float,
    ok: bool,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> None:
    LLM_CALLS.labels(model=model, purpose=purpose, ok=str(ok).lower()).inc()
    LLM_LATENCY.labels(model=model, purpose=purpose).observe(seconds)
    if prompt_tokens:
        LLM_TOKENS.labels(model=model, kind="prompt").inc(prompt_tokens)
    if completion_tokens:
        LLM_TOKENS.labels(model=model, kind="completion").inc(completion_tokens)


def render_metrics() -> tuple[bytes, str]:
    """Payload and content type for the ``/metrics`` endpoint."""
    return generate_latest(), CONTENT_TYPE_LATEST
