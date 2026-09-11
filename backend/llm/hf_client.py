"""Hugging Face Inference Providers client.

The only credential this project needs is ``HUGGINGFACE_API_KEY``. HF's router
exposes an OpenAI-compatible ``/v1/chat/completions`` endpoint, so we talk to
it with plain ``httpx`` rather than pulling in a heavier SDK -- fewer moving
parts, and the wire format is stable and well documented.

Two models are configured deliberately:

* ``HF_CHAT_MODEL`` -- a large instruct model for reasoning-heavy work
  (understanding a messy request, writing the recommendation rationale, the
  final narrative).
* ``HF_FAST_MODEL`` -- a small model for high-frequency, low-stakes calls
  (classification, short rewrites). Routing cheap work to a cheap model is
  what keeps a multi-agent system affordable.

Every call is metered, traced and tolerant of failure: :class:`LLMUnavailable`
is raised rather than propagating transport errors, and each caller has a
deterministic fallback for that case.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from ..config import settings
from ..observability import langfuse_hook, record_llm_call, span

logger = logging.getLogger(__name__)


class LLMUnavailable(RuntimeError):
    """Raised when the LLM cannot answer. Callers must degrade, not crash."""


@dataclass
class LLMResult:
    """A completion plus the metadata the observability layer wants."""

    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    finish_reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class HuggingFaceLLM:
    """Async chat-completion client for HF Inference Providers."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    # ---------------- lifecycle ----------------

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            async with self._lock:
                if self._client is None or self._client.is_closed:
                    self._client = httpx.AsyncClient(
                        base_url=settings.hf_base_url.rstrip("/"),
                        timeout=httpx.Timeout(settings.hf_timeout_seconds, connect=15.0),
                        headers={
                            "Authorization": f"Bearer {settings.huggingface_api_key}",
                            "Content-Type": "application/json",
                        },
                    )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    @property
    def available(self) -> bool:
        return settings.llm_available

    # ---------------- core call ----------------

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        purpose: str = "general",
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format_json: bool = False,
        trace: Any = None,
        tier: Literal["chat", "fast"] = "chat",
    ) -> LLMResult:
        """Run one chat completion.

        Args:
            messages: OpenAI-style message list.
            purpose: Label used in metrics and traces (e.g. ``"analyse_query"``).
            model: Explicit model id; otherwise chosen from ``tier``.
            temperature: Sampling temperature.
            max_tokens: Completion cap.
            response_format_json: Ask the provider for JSON output when
                supported. We still parse defensively -- not every provider
                honours it.
            trace: Optional Langfuse trace to attach this generation to.
            tier: ``"chat"`` for the large model, ``"fast"`` for the small one.

        Raises:
            LLMUnavailable: if no key is configured or all retries failed.
        """
        if not self.available:
            raise LLMUnavailable("HUGGINGFACE_API_KEY is not configured.")

        chosen = model or (
            settings.hf_chat_model if tier == "chat" else settings.hf_fast_model
        )
        payload: dict[str, Any] = {
            "model": chosen,
            "messages": messages,
            "temperature": (
                settings.hf_temperature if temperature is None else float(temperature)
            ),
            "max_tokens": int(max_tokens or settings.hf_max_tokens),
            "stream": False,
        }
        if response_format_json:
            payload["response_format"] = {"type": "json_object"}

        client = await self._get_client()
        started = time.perf_counter()
        last_error: Exception | None = None

        with span(
            "llm.chat",
            **{
                "llm.model": chosen,
                "llm.purpose": purpose,
                "llm.message_count": len(messages),
            },
        ) as current:
            for attempt in range(1, settings.hf_max_retries + 1):
                try:
                    response = await client.post("/chat/completions", json=payload)
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last_error = exc
                else:
                    if response.status_code == 200:
                        result = self._parse(response.json(), chosen, started)
                        record_llm_call(
                            chosen, purpose, result.latency_ms / 1000.0, True,
                            result.prompt_tokens, result.completion_tokens,
                        )
                        current.set_attribute("llm.prompt_tokens", result.prompt_tokens)
                        current.set_attribute(
                            "llm.completion_tokens", result.completion_tokens
                        )
                        langfuse_hook.log_generation(
                            trace,
                            name=purpose,
                            model=chosen,
                            prompt=messages,
                            completion=result.text,
                            prompt_tokens=result.prompt_tokens,
                            completion_tokens=result.completion_tokens,
                            metadata={"attempt": attempt, "tier": tier},
                        )
                        return result

                    body = response.text[:400]
                    # 503 means the model is warming up on the provider side;
                    # 429 is rate limiting. Both are worth waiting out.
                    if response.status_code in (429, 500, 502, 503, 504):
                        last_error = LLMUnavailable(
                            f"HTTP {response.status_code} from HF router: {body}"
                        )
                    else:
                        record_llm_call(chosen, purpose, time.perf_counter() - started, False)
                        raise LLMUnavailable(
                            f"HF router rejected the request "
                            f"(HTTP {response.status_code}): {body}"
                        )

                if attempt < settings.hf_max_retries:
                    delay = min(12.0, 1.5 * 2 ** (attempt - 1)) * (0.7 + random.random() * 0.6)
                    logger.warning(
                        "LLM attempt %d/%d failed (%s); retrying in %.1fs",
                        attempt, settings.hf_max_retries, last_error, delay,
                    )
                    await asyncio.sleep(delay)

            record_llm_call(chosen, purpose, time.perf_counter() - started, False)
            raise LLMUnavailable(
                f"LLM unavailable after {settings.hf_max_retries} attempts: {last_error}"
            )

    @staticmethod
    def _parse(body: dict[str, Any], model: str, started: float) -> LLMResult:
        choices = body.get("choices") or []
        if not choices:
            raise LLMUnavailable("HF router returned no choices.")
        message = choices[0].get("message") or {}
        usage = body.get("usage") or {}
        return LLMResult(
            text=(message.get("content") or "").strip(),
            model=body.get("model") or model,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            latency_ms=(time.perf_counter() - started) * 1000.0,
            finish_reason=choices[0].get("finish_reason"),
            raw=body,
        )

    # ---------------- speech to text ----------------

    async def transcribe(self, audio: bytes, content_type: str = "audio/webm") -> str:
        """Transcribe audio via the HF inference API.

        This is the *fallback* path. The browser's Web Speech API handles voice
        input for free and with zero latency when available; this server-side
        route covers browsers without it (notably Firefox) and uploaded files.
        """
        if not self.available:
            raise LLMUnavailable("HUGGINGFACE_API_KEY is not configured.")

        url = f"https://api-inference.huggingface.co/models/{settings.hf_stt_model}"
        started = time.perf_counter()

        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=15.0)) as client:
            for attempt in range(1, settings.hf_max_retries + 1):
                try:
                    response = await client.post(
                        url,
                        content=audio,
                        headers={
                            "Authorization": f"Bearer {settings.huggingface_api_key}",
                            "Content-Type": content_type,
                        },
                    )
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    if attempt == settings.hf_max_retries:
                        raise LLMUnavailable(f"Transcription transport error: {exc}") from exc
                    await asyncio.sleep(2.0 * attempt)
                    continue

                if response.status_code == 200:
                    try:
                        body = response.json()
                    except ValueError as exc:
                        raise LLMUnavailable("Transcription returned non-JSON.") from exc
                    text = (
                        body.get("text")
                        if isinstance(body, dict)
                        else (body[0].get("text") if body else "")
                    )
                    record_llm_call(
                        settings.hf_stt_model, "transcribe",
                        time.perf_counter() - started, True,
                    )
                    return (text or "").strip()

                # 503 with an estimated_time means the model is loading.
                if response.status_code == 503 and attempt < settings.hf_max_retries:
                    wait = 8.0
                    try:
                        wait = min(30.0, float(response.json().get("estimated_time", 8.0)))
                    except (ValueError, AttributeError, TypeError):
                        pass
                    logger.info("STT model loading; waiting %.0fs", wait)
                    await asyncio.sleep(wait)
                    continue

                record_llm_call(
                    settings.hf_stt_model, "transcribe",
                    time.perf_counter() - started, False,
                )
                raise LLMUnavailable(
                    f"Transcription failed (HTTP {response.status_code}): "
                    f"{response.text[:200]}"
                )

        raise LLMUnavailable("Transcription failed after retries.")


# Module-level singleton -- one connection pool for the whole process.
llm = HuggingFaceLLM()


def messages_to_text(messages: list[dict[str, str]]) -> str:
    """Flatten a message list for logging."""
    return json.dumps(messages, ensure_ascii=False)[:4000]
