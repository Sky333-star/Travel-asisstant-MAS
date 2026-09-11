"""Chat endpoints -- streaming the agent graph to the browser over SSE.

WHY SERVER-SENT EVENTS
----------------------
A plan takes 10-40 seconds to build across a dozen tool calls. A single JSON
response would leave the user staring at a spinner with no idea whether the
system is working or hung. SSE gives a one-way stream, which is exactly the
shape of this problem (the client sends one message, the server sends many
updates), and unlike WebSockets it survives proxies, needs no upgrade
handshake, and reconnects on its own.

THE INTERRUPT PROTOCOL
----------------------
The graph pauses twice: once to clarify, once to choose a destination. When
LangGraph emits ``__interrupt__``, this route forwards it as a ``question``
event and ends the stream. The UI shows the question; the answer arrives at
``/api/chat/resume``, which restarts streaming from the checkpoint with
``Command(resume=...)``. From the user's perspective it is one continuous
conversation; underneath, it is three separate HTTP streams over one persisted
graph run.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from langgraph.types import Command

from ..config import settings
from ..graph import get_compiled_graph, initial_state
from ..observability import ACTIVE_SESSIONS, span
from ..schemas.chat import ChatRequest, EventType, ResumeRequest, StageName, StreamEvent
from ..services.session_store import session_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])

# Which node completions produce a user-visible payload, and the stage label
# the UI should show while each is running.
_NODE_STAGES: dict[str, StageName] = {
    "intake": StageName.INTAKE,
    "query_analyst": StageName.ANALYSING,
    "clarifier": StageName.CLARIFYING,
    "destination_scout": StageName.SCOUTING,
    "weather_analyst": StageName.WEATHER,
    "recommender": StageName.RECOMMENDING,
    "selection": StageName.AWAITING_CHOICE,
    "itinerary_planner": StageName.PLANNING,
    "bump_revision": StageName.REVISING,
    "plan_validator": StageName.VALIDATING,
    "presenter": StageName.PRESENTING,
}

_NODE_PROGRESS: dict[str, str] = {
    "intake": "Reading your message",
    "query_analyst": "Working out what you're asking for",
    "clarifier": "Checking I have what I need",
    "destination_scout": "Shortlisting destinations",
    "weather_analyst": "Checking the weather for your dates",
    "recommender": "Ranking the options",
    "itinerary_planner": "Building flights, hotel and day plan",
    "plan_validator": "Checking the plan holds up",
    "bump_revision": "Revising the plan",
    "presenter": "Writing it up",
}


def _dump(model: Any) -> Any:
    """Serialise a Pydantic model (or list of them) for the wire."""
    if model is None:
        return None
    if isinstance(model, list):
        return [_dump(item) for item in model]
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    return model


def _extract_interrupt(chunk: dict[str, Any]) -> dict[str, Any] | None:
    """Pull the payload out of a LangGraph ``__interrupt__`` chunk.

    The wire shape has shifted across LangGraph versions (a tuple of Interrupt
    objects, a bare Interrupt, or a plain dict), so this normalises all three
    rather than pinning to one version's internals.
    """
    raw = chunk.get("__interrupt__")
    if raw is None:
        return None

    candidate = raw[0] if isinstance(raw, (list, tuple)) and raw else raw
    value = getattr(candidate, "value", candidate)

    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return {"type": "question", "question": value}
    return {"type": "question", "question": str(value)}


async def _stream_graph(
    thread_id: str,
    graph_input: Any,
    config: dict[str, Any],
) -> AsyncIterator[str]:
    """Run the graph and translate its updates into SSE frames."""
    graph = await get_compiled_graph()
    ACTIVE_SESSIONS.inc()

    seen_tool_calls = 0
    final_stage = StageName.DONE

    try:
        yield StreamEvent(
            type=EventType.STAGE,
            thread_id=thread_id,
            stage=StageName.INTAKE,
            text="Starting",
        ).to_sse()

        async for chunk in graph.astream(graph_input, config=config, stream_mode="updates"):
            if not isinstance(chunk, dict):
                continue

            # ---- Human-in-the-loop pause ----
            interrupt_payload = _extract_interrupt(chunk)
            if interrupt_payload is not None:
                stage = (
                    StageName.AWAITING_CHOICE
                    if interrupt_payload.get("type", "").startswith("destination")
                    else StageName.CLARIFYING
                )
                await session_store.update(
                    thread_id,
                    stage=stage.value,
                    awaiting_input=True,
                    pending_question=interrupt_payload,
                )
                yield StreamEvent(
                    type=EventType.QUESTION,
                    thread_id=thread_id,
                    stage=stage,
                    text=interrupt_payload.get("question"),
                    payload=interrupt_payload,
                ).to_sse()
                final_stage = stage
                return

            # ---- Normal node completion ----
            for node_name, update in chunk.items():
                if node_name.startswith("__") or not isinstance(update, dict):
                    continue

                stage = _NODE_STAGES.get(node_name, StageName.PLANNING)
                final_stage = stage

                yield StreamEvent(
                    type=EventType.STAGE,
                    thread_id=thread_id,
                    stage=stage,
                    text=_NODE_PROGRESS.get(node_name, node_name.replace("_", " ")),
                ).to_sse()

                # Tool calls made by this node, streamed for the trace panel.
                trace = update.get("tool_trace")
                if trace:
                    for invocation in trace[seen_tool_calls:]:
                        yield StreamEvent(
                            type=EventType.TOOL,
                            thread_id=thread_id,
                            stage=stage,
                            payload=_dump(invocation),
                        ).to_sse()
                    seen_tool_calls = 0  # Each update carries only its own delta.

                if update.get("brief") is not None:
                    yield StreamEvent(
                        type=EventType.BRIEF,
                        thread_id=thread_id,
                        stage=stage,
                        payload=_dump(update["brief"]),
                    ).to_sse()

                if update.get("recommendation") is not None:
                    yield StreamEvent(
                        type=EventType.RECOMMENDATION,
                        thread_id=thread_id,
                        stage=stage,
                        payload=_dump(update["recommendation"]),
                    ).to_sse()

                if update.get("validation") is not None:
                    yield StreamEvent(
                        type=EventType.VALIDATION,
                        thread_id=thread_id,
                        stage=stage,
                        payload=_dump(update["validation"]),
                    ).to_sse()

                if node_name == "presenter" and update.get("plan") is not None:
                    yield StreamEvent(
                        type=EventType.PLAN,
                        thread_id=thread_id,
                        stage=StageName.DONE,
                        payload=_dump(update["plan"]),
                    ).to_sse()

                if update.get("final_message"):
                    yield StreamEvent(
                        type=EventType.MESSAGE,
                        thread_id=thread_id,
                        stage=StageName.DONE,
                        text=update["final_message"],
                    ).to_sse()
                    await session_store.update(
                        thread_id,
                        append_messages=[
                            {"role": "assistant", "content": update["final_message"]}
                        ],
                    )

                if update.get("errors"):
                    for message in update["errors"]:
                        yield StreamEvent(
                            type=EventType.ERROR,
                            thread_id=thread_id,
                            stage=StageName.ERROR,
                            text=message,
                        ).to_sse()

                if update.get("revision") is not None:
                    await session_store.update(thread_id, revision=update["revision"])

                await session_store.update(thread_id, stage=stage.value)

        await session_store.update(
            thread_id, stage=StageName.DONE.value, awaiting_input=False,
            pending_question={},
        )
        yield StreamEvent(
            type=EventType.DONE, thread_id=thread_id, stage=StageName.DONE
        ).to_sse()

    except asyncio.CancelledError:
        # The client disconnected. The graph checkpoint survives, so the
        # conversation can be resumed; nothing to clean up.
        logger.info("Client disconnected from thread %s", thread_id)
        raise
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Graph execution failed for thread %s", thread_id)
        yield StreamEvent(
            type=EventType.ERROR,
            thread_id=thread_id,
            stage=StageName.ERROR,
            text=(
                "Something went wrong while planning. The details are in the "
                "server logs. You can try again or rephrase your request."
            ),
            payload={"error": str(exc)[:400]},
        ).to_sse()
        yield StreamEvent(
            type=EventType.DONE, thread_id=thread_id, stage=StageName.ERROR
        ).to_sse()
    finally:
        ACTIVE_SESSIONS.dec()
        logger.debug("Stream closed for %s at stage %s", thread_id, final_stage)


def _sse_response(generator: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Nginx buffers SSE by default, which defeats the whole point.
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chat")
async def chat(request: ChatRequest, http_request: Request) -> StreamingResponse:
    """Start (or continue) a conversation, streaming agent progress as SSE.

    If the named thread is already waiting on an answer, the message is treated
    as that answer and the graph resumes -- so the client can always just POST
    here without tracking which endpoint applies.
    """
    record = await session_store.get_or_create(request.thread_id)
    thread_id = record.thread_id

    await session_store.update(
        thread_id,
        append_messages=[{"role": "user", "content": request.message}],
    )

    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 60}

    if record.awaiting_input and record.pending_question:
        logger.info("Thread %s is awaiting input; treating message as an answer.", thread_id)
        await session_store.clear_pending(thread_id)
        graph_input: Any = Command(
            resume={"value": request.message, "selected_index": None}
        )
    else:
        graph_input = initial_state(
            thread_id, request.message, request.input_mode.value
        )

    with span("api.chat", **{"chat.thread": thread_id,
                             "chat.mode": request.input_mode.value}):
        return _sse_response(_stream_graph(thread_id, graph_input, config))


@router.post("/chat/resume")
async def resume(request: ResumeRequest) -> StreamingResponse:
    """Answer a pending question and continue the graph from its checkpoint.

    ``selected_index`` lets the UI send an unambiguous card click rather than
    text the selection node has to interpret.
    """
    record = await session_store.get(request.thread_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No active session '{request.thread_id}'. It may have expired "
                f"after 6 hours of inactivity. Start a new conversation."
            ),
        )

    await session_store.update(
        request.thread_id,
        append_messages=[{"role": "user", "content": request.value}],
    )
    await session_store.clear_pending(request.thread_id)

    config = {"configurable": {"thread_id": request.thread_id}, "recursion_limit": 60}
    command = Command(
        resume={"value": request.value, "selected_index": request.selected_index}
    )

    with span("api.chat_resume", **{"chat.thread": request.thread_id}):
        return _sse_response(_stream_graph(request.thread_id, command, config))


@router.get("/graph")
async def graph_definition() -> dict[str, Any]:
    """The agent graph as Mermaid source, for docs and the UI's architecture view."""
    from ..graph.builder import render_mermaid

    return {
        "mermaid": render_mermaid(),
        "nodes": list(_NODE_STAGES.keys()),
        "max_plan_revisions": settings.max_plan_revisions,
    }


@router.get("/config")
async def public_config() -> dict[str, Any]:
    """Non-secret configuration the UI needs to render honestly."""
    return {
        "llm_available": settings.llm_available,
        "llm_model": settings.hf_chat_model if settings.llm_available else None,
        "default_currency": settings.default_currency,
        "max_plan_revisions": settings.max_plan_revisions,
        "enabled_mcp_servers": settings.enabled_servers,
        "pricing_disclaimer": (
            "Flight and lodging prices are modelled estimates built from open "
            "data, not live bookable fares."
        ),
    }


def encode_event(event: StreamEvent) -> str:
    """Exposed for tests: render an event as an SSE frame."""
    return event.to_sse()


def decode_sse(payload: str) -> list[dict[str, Any]]:
    """Exposed for tests: parse an SSE stream back into event dicts."""
    events: list[dict[str, Any]] = []
    for block in payload.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                try:
                    events.append(json.loads(line[6:]))
                except json.JSONDecodeError:
                    pass
    return events
