"""
Standalone answer-generation step for the new RAG test harness (called by
cli.py option 2).

Calls answer_service.generate_answer() with GENERATE_ANSWER_SYSTEM /
GENERATE_ANSWER_USER (backend/app/prompts/prompt_templates.py) directly,
with no orchestrator, no personalization, no TTS/video call. Nothing in
answer_service.py, chat.py, or the orchestrator is touched or imported for
modification; this only calls the existing generator.

STALE-COMMENT CORRECTION (2026-08-25): this used to say generate_answer()
is "the SAME prompt/model the real application uses for answer generation"
- that's no longer true. The live app's real answers go through a
different call chain entirely: chat.py -> run_orchestrator_pipeline
(backend/app/orchestrator_test/test_runner.py) generates text_narration
from a master orchestrator prompt BEFORE retrieval runs, then
ground_text_narration() only revises it using retrieved chunks -
answer_service.generate_answer() is never called in that live path (see
docs/IMAGE_PIPELINE_PLAN.md section 4.2 for how this was traced). This
module still calls a real, working production function
(generate_answer() genuinely exists and works), it's just not the one a
live student query actually produces - useful for inspecting how retrieved
chunks affect a generated answer, not a faithful preview of the live app's
output.
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
