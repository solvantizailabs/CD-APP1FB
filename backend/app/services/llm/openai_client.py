"""Small OpenAI SDK adapter preserving the application's existing text API.

The rest of the application consumes ``response.text`` and streamed chunks
with ``chunk.text``. Keeping that shape here lets the provider change without
changing orchestration, RAG, TTS, or frontend behavior.
"""

import os
from dataclasses import dataclass
from typing import Any, Iterator, Optional

from openai import OpenAI


OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


@dataclass
class TextChunk:
    text: str


@dataclass
class Candidate:
    finish_reason: str = "STOP"


class TextResponse:
    def __init__(self, text: str, input_tokens: Optional[int] = None, output_tokens: Optional[int] = None):
        self.text = text or ""
        self.parts = [self.text] if self.text else []
        self.candidates = [Candidate()] if self.text else []
        # Real token counts from OpenAI's own `usage` object, when the
        # caller passed one through - None (not 0) if unavailable, so
        # observability/llm_call.py can tell "genuinely zero tokens" apart
        # from "this call site didn't have a usage object to read."
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


def _usage_tokens(usage: Any) -> tuple[Optional[int], Optional[int]]:
    """Normalizes OpenAI usage across the two APIs this adapter calls:
    chat.completions uses prompt_tokens/completion_tokens, the Responses
    API (web_search path) uses input_tokens/output_tokens."""
    if usage is None:
        return None, None
    input_tokens = getattr(usage, "prompt_tokens", None)
    if input_tokens is None:
        input_tokens = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "completion_tokens", None)
    if output_tokens is None:
        output_tokens = getattr(usage, "output_tokens", None)
    return input_tokens, output_tokens


def _messages(contents: Any) -> list[dict[str, Any]]:
    if isinstance(contents, str):
        return [{"role": "user", "content": contents}]
    if isinstance(contents, list):
        # Multimodal path (docs/IMAGE_PIPELINE_PLAN.md Stage 3): a dict item
        # anywhere in the list (e.g. {"type": "image_url", "image_url": {...}})
        # means the caller built a real OpenAI content-block array, not plain
        # text to join. Any plain string items get wrapped into a text block
        # so they can sit in the same array, and the whole thing becomes ONE
        # user message's content - the actual multimodal message shape,
        # unlike every other branch here which only ever sends plain text.
        # Every existing caller only ever passes strings, so this branch
        # never triggers for them - added, not a replacement.
        if any(isinstance(item, dict) for item in contents):
            blocks = [
                {"type": "text", "text": item} if isinstance(item, str) else item
                for item in contents
            ]
            return [{"role": "user", "content": blocks}]
        if len(contents) == 2 and all(isinstance(item, str) for item in contents):
            return [
                {"role": "system", "content": contents[0]},
                {"role": "user", "content": contents[1]},
            ]
        return [{"role": "user", "content": "\n\n".join(map(str, contents))}]
    return [{"role": "user", "content": str(contents)}]


class _Models:
    def __init__(self, client: OpenAI):
        self._client = client

    @staticmethod
    def _temperature(config: Any) -> Optional[float]:
        if isinstance(config, dict):
            return config.get("temperature")
        return getattr(config, "temperature", None)

    @staticmethod
    def _response_mime_type(config: Any) -> Optional[str]:
        if isinstance(config, dict):
            return config.get("response_mime_type")
        return getattr(config, "response_mime_type", None)

    @staticmethod
    def _web_search(config: Any) -> bool:
        if isinstance(config, dict):
            return bool(config.get("web_search"))
        return bool(getattr(config, "web_search", False))

    def generate_content(self, model: Optional[str] = None, contents: Any = None, config: Any = None) -> TextResponse:
        # Backward compatibility for call signatures like generate_content(contents)
        if contents is None and model is not None:
            actual_contents = model
            actual_model = OPENAI_MODEL
        else:
            actual_contents = contents
            actual_model = model or OPENAI_MODEL

        temperature = self._temperature(config)

        # Live/current-events queries are explicitly flagged by the caller
        # (test_runner.py's is_gk_query keyword gate) - NOT every
        # GENERAL_KNOWLEDGE-classified question, only ones that actually need
        # fresh, real-world facts a frozen training cutoff can't have. Routed
        # through the Responses API's hosted web_search tool instead of a
        # bare completion, which otherwise hallucinates plausible-sounding
        # but wrong answers for anything after the model's training cutoff.
        if self._web_search(config):
            kwargs: dict[str, Any] = {
                "model": actual_model,
                "input": _messages(actual_contents),
                "tools": [{"type": "web_search"}],
            }
            if temperature is not None:
                kwargs["temperature"] = temperature
            result = self._client.responses.create(**kwargs)
            in_tok, out_tok = _usage_tokens(getattr(result, "usage", None))
            return TextResponse(getattr(result, "output_text", "") or "", input_tokens=in_tok, output_tokens=out_tok)

        kwargs: dict[str, Any] = {
            "model": actual_model,
            "messages": _messages(actual_contents),
        }

        if temperature is not None:
            kwargs["temperature"] = temperature

        mime_type = self._response_mime_type(config)
        if mime_type == "application/json":
            kwargs["response_format"] = {"type": "json_object"}

        result = self._client.chat.completions.create(**kwargs)
        text = result.choices[0].message.content if result.choices else ""
        in_tok, out_tok = _usage_tokens(getattr(result, "usage", None))
        return TextResponse(text or "", input_tokens=in_tok, output_tokens=out_tok)

    def generate_content_stream(self, model: Optional[str] = None, contents: Any = None, config: Any = None) -> Iterator[TextChunk]:
        # Backward compatibility for call signatures like generate_content_stream(contents)
        if contents is None and model is not None:
            actual_contents = model
            actual_model = OPENAI_MODEL
        else:
            actual_contents = contents
            actual_model = model or OPENAI_MODEL

        kwargs: dict[str, Any] = {
            "model": actual_model,
            "messages": _messages(actual_contents),
            "stream": True,
        }
        
        temperature = self._temperature(config)
        if temperature is not None:
            kwargs["temperature"] = temperature
            
        for event in self._client.chat.completions.create(**kwargs):
            if not event.choices:
                continue
            text = event.choices[0].delta.content or ""
            if text:
                yield TextChunk(text)


class OpenAIClient:
    def __init__(self, api_key: Optional[str] = None):
        self._client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))
        self.models = _Models(self._client)


def create_client() -> OpenAIClient:
    return OpenAIClient()


