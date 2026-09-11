"""Voice endpoints.

VOICE STRATEGY
--------------
Speech input uses a two-tier approach, chosen to keep the common case free and
instant:

1. **Browser Web Speech API (primary).** Chrome, Edge and Safari expose
   on-device or vendor speech recognition at zero cost and near-zero latency,
   with live interim results. Most users never touch this endpoint.
2. **Server-side Whisper via Hugging Face (fallback).** For browsers without
   Web Speech (notably Firefox), and for uploaded audio files. This is the
   only place the HF key is used for something other than text generation.

Speech *output* is entirely browser-side (``speechSynthesis``), so there is no
TTS endpoint here -- shipping audio back over the wire would cost latency and
bandwidth for no benefit.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, File, HTTPException, UploadFile

from ..config import settings
from ..llm.hf_client import LLMUnavailable, llm
from ..observability import span
from ..schemas.chat import TranscribeResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voice", tags=["voice"])

# Formats browsers actually produce from MediaRecorder, plus common uploads.
ALLOWED_CONTENT_TYPES = {
    "audio/webm", "audio/webm;codecs=opus", "audio/ogg", "audio/ogg;codecs=opus",
    "audio/wav", "audio/x-wav", "audio/wave", "audio/mpeg", "audio/mp3",
    "audio/mp4", "audio/m4a", "audio/x-m4a", "audio/flac", "application/octet-stream",
}


@router.post("/transcribe", response_model=TranscribeResponse)
async def transcribe(audio: UploadFile = File(...)) -> TranscribeResponse:
    """Transcribe uploaded audio to text.

    Args:
        audio: An audio file. WebM/Opus is what browsers record by default.

    Raises:
        HTTPException 413: File exceeds ``MAX_UPLOAD_BYTES``.
        HTTPException 415: Unsupported content type.
        HTTPException 503: No HF key configured, or the model is unavailable.
    """
    if not settings.llm_available:
        raise HTTPException(
            status_code=503,
            detail=(
                "Server-side transcription needs HUGGINGFACE_API_KEY. Your "
                "browser's built-in speech recognition works without it -- try "
                "Chrome, Edge or Safari."
            ),
        )

    content_type = (audio.content_type or "application/octet-stream").lower()
    if content_type.split(";")[0].strip() not in {
        c.split(";")[0].strip() for c in ALLOWED_CONTENT_TYPES
    }:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported audio type '{content_type}'.",
        )

    payload = await audio.read()
    if not payload:
        raise HTTPException(status_code=400, detail="Empty audio upload.")
    if len(payload) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Audio is {len(payload) / 1e6:.1f} MB; the limit is "
                f"{settings.max_upload_bytes / 1e6:.0f} MB. Record a shorter clip."
            ),
        )

    started = time.perf_counter()
    with span(
        "api.transcribe",
        **{"audio.bytes": len(payload), "audio.content_type": content_type},
    ) as current:
        try:
            text = await llm.transcribe(payload, content_type)
        except LLMUnavailable as exc:
            logger.warning("Transcription failed: %s", exc)
            raise HTTPException(
                status_code=503,
                detail=f"Transcription is unavailable right now: {exc}",
            ) from exc

        elapsed_ms = (time.perf_counter() - started) * 1000
        current.set_attribute("audio.transcript_length", len(text))

    if not text:
        raise HTTPException(
            status_code=422,
            detail="No speech detected in the recording.",
        )

    logger.info("Transcribed %d bytes in %.0f ms: %s", len(payload), elapsed_ms, text[:80])

    return TranscribeResponse(
        text=text, model=settings.hf_stt_model, duration_ms=round(elapsed_ms, 1)
    )


@router.get("/capabilities")
async def capabilities() -> dict[str, object]:
    """What voice features are available, so the UI can adapt its controls."""
    return {
        "server_transcription": settings.llm_available,
        "server_model": settings.hf_stt_model if settings.llm_available else None,
        "recommended": "browser_web_speech",
        "note": (
            "Prefer the browser's Web Speech API: it is free, instant and gives "
            "live interim results. This endpoint is the fallback for browsers "
            "without it."
        ),
    }
