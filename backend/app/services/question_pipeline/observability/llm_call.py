"""
Single choke point for every generate_content call in the question_pipeline,
so real token usage/cost/timing is captured once instead of duplicated at
each of the 3 call sites (understanding.py, generation.py x2). Existing
callers keep their own JSON-parsing/fail-safe logic - this only replaces the
raw openai_client.models.generate_content(...) call itself.
"""
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from backend.app.services.question_pipeline.observability.token_pricing import estimate_cost


@dataclass
class LLMCallResult:
    stage: str
    model: str
    text: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost: float
    duration_ms: int
    prompt_sent: str
    token_source: str  # "usage_metadata" | "char_estimate_fallback"
    extra: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


def _prompt_text(contents: Any) -> str:
    if isinstance(contents, str):
        return contents
    if isinstance(contents, list):
        return "\n".join(str(c) for c in contents)
    return str(contents)


def call_llm(
    openai_client,
    model_name: str,
    contents: Any,
    stage: str,
    config: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> LLMCallResult:
    prompt_text = _prompt_text(contents)
    started = time.time()
    try:
        response = openai_client.models.generate_content(model=model_name, contents=contents, config=config or {})
        text = getattr(response, "text", "") or ""
        # This repo's own OpenAI adapter (backend/app/services/llm/openai_client.py)
        # exposes real counts as .input_tokens/.output_tokens; a raw Gemini
        # SDK response instead carries .usage_metadata - checked in that
        # order since this pipeline's actual client is the former.
        openai_in, openai_out = getattr(response, "input_tokens", None), getattr(response, "output_tokens", None)
        gemini_usage = getattr(response, "usage_metadata", None)
        if openai_in is not None and openai_out is not None:
            input_tokens, output_tokens = openai_in, openai_out
            token_source = "usage_metadata"
        elif gemini_usage is not None:
            input_tokens = getattr(gemini_usage, "prompt_token_count", None) or 0
            output_tokens = getattr(gemini_usage, "candidates_token_count", None) or 0
            token_source = "usage_metadata"
        else:
            # Fallback ONLY when this SDK/response genuinely carries no
            # usage_metadata - clearly tagged so it's never mistaken for a
            # real count in the log report or a cost total.
            input_tokens = round(len(prompt_text) / 4)
            output_tokens = round(len(text) / 4)
            token_source = "char_estimate_fallback"
        duration_ms = round((time.time() - started) * 1000)
        return LLMCallResult(
            stage=stage, model=model_name, text=text,
            input_tokens=input_tokens, output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            cost=estimate_cost(model_name, input_tokens, output_tokens),
            duration_ms=duration_ms, prompt_sent=prompt_text,
            token_source=token_source, extra=extra or {},
        )
    except Exception as e:
        duration_ms = round((time.time() - started) * 1000)
        return LLMCallResult(
            stage=stage, model=model_name, text="",
            input_tokens=0, output_tokens=0, total_tokens=0, cost=0.0,
            duration_ms=duration_ms, prompt_sent=prompt_text,
            token_source="none", extra=extra or {}, error=str(e),
        )
