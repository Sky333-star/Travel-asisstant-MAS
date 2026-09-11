"""LLM access layer: Hugging Face client, structured output, rule-based fallback."""

from .fallback import build_brief, compute_missing, merge_briefs
from .hf_client import HuggingFaceLLM, LLMResult, LLMUnavailable, llm
from .structured import extract_json, structured_call, text_call

__all__ = [
    "HuggingFaceLLM",
    "LLMResult",
    "LLMUnavailable",
    "build_brief",
    "compute_missing",
    "extract_json",
    "llm",
    "merge_briefs",
    "structured_call",
    "text_call",
]
