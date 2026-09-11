"""Langfuse integration for LLM-level observability.

OpenTelemetry answers "how long did this take". Langfuse answers the questions
that are specific to LLM systems: what exact prompt went out, what came back,
did the structured parse succeed, how much did that turn cost, and how did the
same prompt behave last week.

Langfuse is open source with a free managed tier, and this project ships a
self-hosted compose file too, so it stays inside the "free platform" constraint
either way. Everything here is optional -- with no keys configured, all calls
become no-ops.
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import settings

logger = logging.getLogger(__name__)

_client: Any = None
_checked = False


def get_client() -> Any:
    """Return a Langfuse client, or ``None`` when unconfigured/unavailable."""
    global _client, _checked

    if _checked:
        return _client
    _checked = True

    if not settings.langfuse_configured:
        logger.info("Langfuse disabled (no keys configured).")
        return None

    try:
        from langfuse import Langfuse

        _client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
        logger.info("Langfuse tracing enabled -> %s", settings.langfuse_host)
    except Exception as exc:  # pragma: no cover - optional dependency
        logger.warning("Langfuse unavailable (%s); continuing without it.", exc)
        _client = None
    return _client


def trace_conversation(thread_id: str, user_input: str, input_mode: str) -> Any:
    """Open a Langfuse trace for one user turn."""
    client = get_client()
    if client is None:
        return None
    try:
        # Langfuse v2 and v3 expose different entry points; support both.
        if hasattr(client, "trace"):
            return client.trace(
                name="wayfarer-turn",
                session_id=thread_id,
                input={"message": user_input, "input_mode": input_mode},
                metadata={"environment": settings.app_env},
                tags=["multi-agent", "travel", input_mode],
            )
        if hasattr(client, "start_span"):
            return client.start_span(
                name="wayfarer-turn",
                input={"message": user_input, "input_mode": input_mode},
            )
    except Exception as exc:  # pragma: no cover
        logger.debug("Langfuse trace creation failed: %s", exc)
    return None


def log_generation(
    trace: Any,
    *,
    name: str,
    model: str,
    prompt: Any,
    completion: Any,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Record one LLM generation against an open trace."""
    if trace is None:
        return
    try:
        if hasattr(trace, "generation"):
            trace.generation(
                name=name,
                model=model,
                input=prompt,
                output=completion,
                usage={
                    "input": prompt_tokens,
                    "output": completion_tokens,
                    "unit": "TOKENS",
                },
                metadata=metadata or {},
            )
    except Exception as exc:  # pragma: no cover
        logger.debug("Langfuse generation logging failed: %s", exc)


def log_span(
    trace: Any,
    *,
    name: str,
    input_data: Any = None,
    output_data: Any = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Record a non-LLM step (an agent node, an MCP call) on the trace."""
    if trace is None:
        return
    try:
        if hasattr(trace, "span"):
            trace.span(
                name=name,
                input=input_data,
                output=output_data,
                metadata=metadata or {},
            ).end()
    except Exception as exc:  # pragma: no cover
        logger.debug("Langfuse span logging failed: %s", exc)


def finalise(trace: Any, output: Any = None) -> None:
    """Close a trace and flush it."""
    if trace is None:
        return
    try:
        if hasattr(trace, "update"):
            trace.update(output=output)
        elif hasattr(trace, "end"):
            trace.end()
    except Exception as exc:  # pragma: no cover
        logger.debug("Langfuse trace finalisation failed: %s", exc)


def flush() -> None:
    """Flush buffered events (call on shutdown)."""
    client = get_client()
    if client is None:
        return
    try:
        client.flush()
    except Exception:  # pragma: no cover
        pass
