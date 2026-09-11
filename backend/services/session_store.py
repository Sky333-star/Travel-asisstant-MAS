"""In-memory session index over the LangGraph checkpointer.

LangGraph's checkpointer already persists the authoritative conversation state.
This store exists for the things a checkpointer is not designed to answer
quickly:

* "Is this thread waiting on the user, and what did we ask?" -- needed to route
  a ``/api/chat`` call to resume rather than start fresh.
* "What should the UI render after a page reload?" -- a compact snapshot,
  without replaying the graph.
* Cheap eviction of idle conversations so a long-running server does not grow
  unbounded.

It is deliberately in-process. Making it durable would mean a second source of
truth competing with the checkpointer; instead, a lost index just means the
next turn starts a new thread, and the checkpoint history remains intact on
disk. A multi-replica deployment would swap this for Redis -- the interface is
small enough that the swap is contained.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..schemas.chat import SessionSnapshot, StageName

logger = logging.getLogger(__name__)

SESSION_TTL_SECONDS = 60 * 60 * 6   # 6 hours of inactivity.
MAX_SESSIONS = 500


@dataclass
class SessionRecord:
    thread_id: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    stage: str = StageName.INTAKE.value
    awaiting_input: bool = False
    pending_question: dict[str, Any] | None = None
    snapshot: SessionSnapshot | None = None
    messages: list[dict[str, str]] = field(default_factory=list)
    revision: int = 0

    def touch(self) -> None:
        self.updated_at = time.time()

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.updated_at) > SESSION_TTL_SECONDS


class SessionStore:
    """Thread-safe session index."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionRecord] = {}
        self._lock = asyncio.Lock()

    async def create(self) -> SessionRecord:
        thread_id = f"wf-{uuid.uuid4().hex[:16]}"
        async with self._lock:
            await self._evict_locked()
            record = SessionRecord(thread_id=thread_id)
            self._sessions[thread_id] = record
            logger.info("Created session %s", thread_id)
            return record

    async def get(self, thread_id: str) -> SessionRecord | None:
        async with self._lock:
            record = self._sessions.get(thread_id)
            if record is None:
                return None
            if record.is_expired:
                self._sessions.pop(thread_id, None)
                return None
            record.touch()
            return record

    async def get_or_create(self, thread_id: str | None) -> SessionRecord:
        if thread_id:
            existing = await self.get(thread_id)
            if existing is not None:
                return existing
            # An unknown-but-supplied id is honoured: the checkpointer may
            # still hold its history even though this process's index does not
            # (after a restart, say).
            async with self._lock:
                record = SessionRecord(thread_id=thread_id)
                self._sessions[thread_id] = record
                return record
        return await self.create()

    async def update(
        self,
        thread_id: str,
        *,
        stage: str | None = None,
        awaiting_input: bool | None = None,
        pending_question: dict[str, Any] | None = None,
        snapshot: SessionSnapshot | None = None,
        append_messages: list[dict[str, str]] | None = None,
        revision: int | None = None,
    ) -> None:
        async with self._lock:
            record = self._sessions.get(thread_id)
            if record is None:
                record = SessionRecord(thread_id=thread_id)
                self._sessions[thread_id] = record

            if stage is not None:
                record.stage = stage
            if awaiting_input is not None:
                record.awaiting_input = awaiting_input
            # `pending_question` is cleared by passing an explicit empty dict,
            # so `None` can mean "leave unchanged".
            if pending_question is not None:
                record.pending_question = pending_question or None
            if snapshot is not None:
                record.snapshot = snapshot
            if append_messages:
                record.messages.extend(append_messages)
                # Keep the tail only; the checkpointer holds the full history.
                record.messages = record.messages[-40:]
            if revision is not None:
                record.revision = revision
            record.touch()

    async def clear_pending(self, thread_id: str) -> None:
        async with self._lock:
            record = self._sessions.get(thread_id)
            if record is not None:
                record.pending_question = None
                record.awaiting_input = False
                record.touch()

    async def delete(self, thread_id: str) -> bool:
        async with self._lock:
            return self._sessions.pop(thread_id, None) is not None

    async def count(self) -> int:
        async with self._lock:
            return len(self._sessions)

    async def _evict_locked(self) -> None:
        """Drop expired sessions, then the oldest if still over capacity."""
        expired = [tid for tid, rec in self._sessions.items() if rec.is_expired]
        for thread_id in expired:
            self._sessions.pop(thread_id, None)
        if expired:
            logger.debug("Evicted %d expired session(s).", len(expired))

        if len(self._sessions) >= MAX_SESSIONS:
            oldest = sorted(self._sessions.items(), key=lambda kv: kv[1].updated_at)
            for thread_id, _ in oldest[: len(self._sessions) - MAX_SESSIONS + 1]:
                self._sessions.pop(thread_id, None)


session_store = SessionStore()
