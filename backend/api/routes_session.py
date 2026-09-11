"""Session, health and metrics endpoints."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Response

from ..config import settings
from ..graph import get_compiled_graph
from ..mcp import mcp_manager
from ..observability import render_metrics
from ..schemas.chat import HealthResponse, SessionSnapshot, StageName
from ..services.session_store import session_store

logger = logging.getLogger(__name__)

router = APIRouter(tags=["session"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness and capability report.

    Reports ``degraded`` rather than failing when a non-critical MCP server is
    down: the system genuinely still works without flights or currency, and a
    red health check for a degraded-but-serving system trains people to ignore
    health checks.
    """
    servers = mcp_manager.status()
    critical_down = [
        name for name, state in servers.items()
        if state != "ready" and name in ("weather", "geo")
    ]

    return HealthResponse(
        status="degraded" if critical_down else "ok",
        llm_available=settings.llm_available,
        llm_model=settings.hf_chat_model if settings.llm_available else None,
        mcp_servers=servers,
        observability={
            "otel": settings.otel_enabled,
            "metrics": settings.metrics_enabled,
            "langfuse": settings.langfuse_configured,
        },
    )


@router.get("/metrics")
async def metrics() -> Response:
    """Prometheus scrape endpoint."""
    if not settings.metrics_enabled:
        raise HTTPException(status_code=404, detail="Metrics are disabled.")
    payload, content_type = render_metrics()
    return Response(content=payload, media_type=content_type)


@router.get("/api/session/{thread_id}", response_model=SessionSnapshot)
async def get_session(thread_id: str) -> SessionSnapshot:
    """Rehydrate a conversation after a page reload.

    Reads the authoritative state from the LangGraph checkpointer and falls
    back to the in-process index if the checkpointer has nothing (or is
    unavailable), so a reload degrades to "still knows the stage" rather than
    "conversation lost".
    """
    record = await session_store.get(thread_id)

    snapshot = SessionSnapshot(thread_id=thread_id)
    if record is not None:
        snapshot.stage = StageName(record.stage) if record.stage in {
            s.value for s in StageName
        } else StageName.INTAKE
        snapshot.awaiting_input = record.awaiting_input
        snapshot.messages = list(record.messages)
        snapshot.revision = record.revision

    try:
        graph = await get_compiled_graph()
        state = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    except Exception as exc:  # pragma: no cover - checkpointer optional
        logger.debug("Checkpoint read failed for %s: %s", thread_id, exc)
        state = None

    if state is not None and state.values:
        values = state.values
        snapshot.brief = values.get("brief")
        snapshot.recommendation = values.get("recommendation")
        snapshot.plan = values.get("plan")
        snapshot.validation = values.get("validation")
        snapshot.revision = values.get("revision", snapshot.revision)
        if values.get("messages"):
            snapshot.messages = values["messages"][-40:]
        # `state.next` being non-empty means the graph is paused mid-run.
        snapshot.awaiting_input = bool(state.next) and snapshot.awaiting_input

    if record is None and (state is None or not state.values):
        raise HTTPException(
            status_code=404,
            detail=f"No session '{thread_id}' found. It may have expired.",
        )

    if record is not None and record.pending_question:
        from ..schemas.travel import ClarificationRequest

        question = record.pending_question
        snapshot.pending_question = ClarificationRequest(
            fields=question.get("fields", []),
            question=question.get("question", ""),
            examples=question.get("examples", []),
        )

    return snapshot


@router.delete("/api/session/{thread_id}")
async def delete_session(thread_id: str) -> dict[str, Any]:
    """Forget a conversation's in-process index entry."""
    deleted = await session_store.delete(thread_id)
    return {"deleted": deleted, "thread_id": thread_id}


@router.get("/api/mcp/tools")
async def mcp_tools() -> dict[str, Any]:
    """Live inventory of MCP servers and the tools they expose.

    Useful during a demo: it proves the MCP layer is genuinely running as
    separate processes with discovered tools, not a hard-coded list.
    """
    from ..mcp.registry import SERVERS

    return {
        "servers": [
            {
                "name": name,
                "description": spec.description,
                "state": mcp_manager.status().get(name, "stopped"),
                "declared_tools": list(spec.tools),
                "discovered_tools": mcp_manager.available_tools().get(name, []),
                "critical": spec.critical,
            }
            for name, spec in SERVERS.items()
        ],
        "transport": settings.mcp_transport,
        "enabled": settings.enabled_servers,
    }
