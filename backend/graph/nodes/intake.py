"""Intake node -- the graph's front door.

Cheap, synchronous work only: normalise the input, note whether it arrived by
voice, and open the observability trace for the turn. Doing this in its own
node (rather than inline in the analyst) keeps the analyst focused and gives
the UI a stage to show immediately, before the first slow call.
"""

from __future__ import annotations

import logging
import re

from ...observability import CONVERSATIONS, STAGE_ENTERED, langfuse_hook, time_agent
from ..state import AgentState

logger = logging.getLogger(__name__)

# Voice transcripts arrive with filler and no punctuation. Stripping the
# obvious filler measurably improves regex extraction and gives the LLM a
# cleaner input, without changing meaning.
_FILLER = re.compile(
    r"\b(um+|uh+|erm+|hmm+|like|you know|i mean|sort of|kind of|basically|"
    r"actually|literally)\b[,\s]*",
    re.IGNORECASE,
)
_WHITESPACE = re.compile(r"\s+")


def normalise_input(text: str, input_mode: str) -> str:
    """Clean up a user turn, more aggressively for voice than for text."""
    cleaned = (text or "").strip()
    if input_mode == "voice":
        cleaned = _FILLER.sub("", cleaned)
        cleaned = _WHITESPACE.sub(" ", cleaned).strip()
        # Transcripts often lack a final stop, which trips sentence-based
        # regexes that expect a boundary.
        if cleaned and cleaned[-1] not in ".?!":
            cleaned += "."
        # Capitalise the first letter so place-name patterns (which key off
        # capitalisation) are not defeated by an all-lowercase transcript.
        if cleaned:
            cleaned = cleaned[0].upper() + cleaned[1:]
    return _WHITESPACE.sub(" ", cleaned).strip()


async def intake_node(state: AgentState) -> AgentState:
    """Normalise input and start the trace for this turn."""
    with time_agent("intake"):
        STAGE_ENTERED.labels(stage="intake").inc()

        raw = state.get("user_input", "")
        mode = state.get("input_mode", "text")
        cleaned = normalise_input(raw, mode)

        CONVERSATIONS.labels(input_mode=mode).inc()

        trace = langfuse_hook.trace_conversation(
            state.get("thread_id", "unknown"), cleaned, mode
        )

        notes: list[str] = []
        if mode == "voice" and cleaned != raw.strip():
            notes.append("Cleaned voice transcript before analysis.")

        logger.info(
            "Intake [thread=%s mode=%s]: %s",
            state.get("thread_id"), mode, cleaned[:160],
        )

        return AgentState(
            user_input=cleaned,
            stage="analysing",
            langfuse_trace=trace,
            scratch={"original_input": raw, "intake_notes": notes},
        )
