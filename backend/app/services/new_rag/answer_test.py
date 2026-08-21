"""
Standalone answer-generation step for the new RAG test harness.

Reuses the SAME prompt/model the real application uses for answer
generation - answer_service.generate_answer() with GENERATE_ANSWER_SYSTEM /
GENERATE_ANSWER_USER (backend/app/prompts/prompt_templates.py) - called
directly here with no orchestrator, no personalization, no TTS/video call.
Nothing in answer_service.py, chat.py, or the orchestrator is touched or
imported for modification; this only calls the existing generator.
"""
import re
from typing import Dict

from backend.app.services.chat import answer_service
from backend.app.services.retrieval import qdrant_service as qdrant

_TEXT_RE = re.compile(r"\[TEXT_RESPONSE_START\](.*?)\[TEXT_RESPONSE_END\]", re.DOTALL)
_VOICE_RE = re.compile(r"\[VOICE_SCRIPT_START\](.*?)\[VOICE_SCRIPT_END\]", re.DOTALL)


def _ensure_openai_client() -> None:
    """qdrant_service.openai_client is a lazily-initialized module global -
    the new_rag CLI never touches qdrant_service (it has its own, separate
    Qdrant client in indexing/qdrant_indexer.py), so it's never been
    initialized when running the standalone harness. Only the OpenAI client
    is needed here, not the rest of qdrant_service.initialize() (which
    additionally requires Qdrant/embedding setup this test flow doesn't
    use)."""
    if qdrant.openai_client is not None:
        return
    from backend.app.services.llm.openai_client import create_client
    qdrant.openai_client = create_client()
    if not qdrant.generation_model_name:
        from backend.app.services.llm.openai_client import OPENAI_MODEL
        qdrant.generation_model_name = OPENAI_MODEL


def generate_test_answer(query: str, context: str, class_name: str, subject: str) -> Dict[str, str]:
    """Runs the production answer-generation prompt standalone against the
    given context and returns the parsed text/voice parts plus the raw
    response, for direct comparison against the previous flow's answer to
    the same question."""
    _ensure_openai_client()

    raw = "".join(answer_service.generate_answer(
        raw_query=query,
        book_details={"class_name": class_name, "subject": subject},
        context=context,
    ))

    text_match = _TEXT_RE.search(raw)
    voice_match = _VOICE_RE.search(raw)

    return {
        "text_response": text_match.group(1).strip() if text_match else "",
        "voice_script": voice_match.group(1).strip() if voice_match else "",
        "raw_response": raw,
    }
