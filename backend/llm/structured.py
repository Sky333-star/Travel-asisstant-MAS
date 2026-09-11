"""Structured output: getting reliable JSON out of an open-weights LLM.

Hosted frontier models offer strict schema-constrained decoding. Open models
served through HF Inference Providers often do not, so this module supplies the
reliability layer instead, in four escalating steps:

1. **Ask precisely.** The schema is injected into the prompt, and the model is
   told to emit JSON and nothing else.
2. **Extract tolerantly.** Real responses arrive wrapped in prose, fenced in
   ```json blocks, or with a trailing apology. :func:`extract_json` finds the
   JSON object by brace balancing rather than hoping the whole string parses.
3. **Repair once.** If validation fails, the model is shown its own output and
   the exact validation errors and asked to correct them. One repair round
   fixes the overwhelming majority of failures.
4. **Fall back.** If repair also fails, the caller gets ``None`` and uses its
   deterministic path. The system degrades; it does not break.

Every failure increments ``wayfarer_llm_parse_failures_total``, so prompt
regressions are visible in Grafana rather than silently swallowed.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from ..observability import LLM_PARSE_FAILURES
from .hf_client import LLMUnavailable, llm

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json(text: str) -> Any | None:
    """Pull the first valid JSON value out of a model response.

    Handles the three shapes that actually occur in practice: a bare JSON
    document, a fenced code block, and JSON embedded in explanatory prose.
    """
    if not text:
        return None

    candidate = text.strip()

    # 1. The happy path.
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # 2. Fenced block.
    fence = _FENCE_RE.search(candidate)
    if fence:
        try:
            return json.loads(fence.group(1).strip())
        except json.JSONDecodeError:
            candidate = fence.group(1).strip()

    # 3. Brace/bracket balancing: scan for the first structurally complete
    #    object or array, ignoring braces that appear inside strings.
    for opener, closer in (("{", "}"), ("[", "]")):
        start = candidate.find(opener)
        if start == -1:
            continue
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(candidate)):
            char = candidate[index]
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(candidate[start : index + 1])
                    except json.JSONDecodeError:
                        break
    return None


def _schema_hint(model: type[BaseModel]) -> str:
    """Compact JSON-schema description to embed in the prompt.

    The full Pydantic schema is verbose enough to crowd out the actual task, so
    definitions are kept but examples and descriptions of nested models are
    left to the prompt author.
    """
    schema = model.model_json_schema()
    return json.dumps(schema, indent=2, ensure_ascii=False)[:6000]


async def structured_call(
    schema: type[T],
    *,
    system_prompt: str,
    user_prompt: str,
    purpose: str,
    tier: str = "chat",
    temperature: float = 0.2,
    max_tokens: int | None = None,
    trace: Any = None,
    allow_repair: bool = True,
) -> T | None:
    """Call the LLM and return a validated Pydantic model, or ``None``.

    Returning ``None`` rather than raising is deliberate: every caller in this
    codebase has a deterministic fallback, and forcing them to catch an
    exception would make that fallback easy to forget.
    """
    if not llm.available:
        return None

    messages = [
        {
            "role": "system",
            "content": (
                f"{system_prompt}\n\n"
                "Respond with a single JSON object that validates against this "
                "JSON Schema. Output JSON only -- no prose, no code fences, no "
                "explanation before or after.\n\n"
                f"JSON Schema:\n{_schema_hint(schema)}"
            ),
        },
        {"role": "user", "content": user_prompt},
    ]

    try:
        result = await llm.chat(
            messages,
            purpose=purpose,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format_json=True,
            trace=trace,
            tier="fast" if tier == "fast" else "chat",
        )
    except LLMUnavailable as exc:
        logger.warning("LLM unavailable for %s: %s", purpose, exc)
        return None

    parsed = extract_json(result.text)
    if parsed is not None:
        try:
            return schema.model_validate(parsed)
        except ValidationError as exc:
            validation_errors = exc.errors()
            logger.info("Structured parse failed for %s: %s", purpose, exc)
    else:
        validation_errors = [{"msg": "Response contained no parseable JSON."}]
        logger.info("No JSON found in %s response.", purpose)

    LLM_PARSE_FAILURES.labels(purpose=purpose).inc()

    if not allow_repair:
        return None

    # ---- Repair round: show the model its own mistake ----
    error_text = json.dumps(
        [
            {"field": ".".join(str(p) for p in e.get("loc", ())), "problem": e.get("msg")}
            for e in validation_errors[:12]
        ],
        indent=2,
    )
    repair_messages = [
        *messages,
        {"role": "assistant", "content": result.text[:3000]},
        {
            "role": "user",
            "content": (
                "That response did not validate. Problems:\n"
                f"{error_text}\n\n"
                "Return the corrected JSON object only. Keep every value you "
                "got right; change only what the errors point at."
            ),
        },
    ]

    try:
        repaired = await llm.chat(
            repair_messages,
            purpose=f"{purpose}_repair",
            temperature=0.0,
            max_tokens=max_tokens,
            response_format_json=True,
            trace=trace,
            tier="fast" if tier == "fast" else "chat",
        )
    except LLMUnavailable:
        return None

    reparsed = extract_json(repaired.text)
    if reparsed is None:
        LLM_PARSE_FAILURES.labels(purpose=f"{purpose}_repair").inc()
        return None
    try:
        return schema.model_validate(reparsed)
    except ValidationError as exc:
        LLM_PARSE_FAILURES.labels(purpose=f"{purpose}_repair").inc()
        logger.warning("Repair round also failed for %s: %s", purpose, exc)
        return None


async def text_call(
    *,
    system_prompt: str,
    user_prompt: str,
    purpose: str,
    tier: str = "chat",
    temperature: float = 0.4,
    max_tokens: int | None = None,
    trace: Any = None,
) -> str | None:
    """Free-text LLM call for prose (rationales, narratives). ``None`` on failure."""
    if not llm.available:
        return None
    try:
        result = await llm.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            purpose=purpose,
            temperature=temperature,
            max_tokens=max_tokens,
            trace=trace,
            tier="fast" if tier == "fast" else "chat",
        )
        return result.text or None
    except LLMUnavailable as exc:
        logger.warning("LLM text call failed for %s: %s", purpose, exc)
        return None
