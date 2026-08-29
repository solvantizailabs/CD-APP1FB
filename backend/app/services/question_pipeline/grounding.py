"""
Stage 13 (spec section 15): Grounding Validation.

Reuses the existing (previously unused) lexical-overlap checker at
backend/app/services/new_rag/validation/grounding.py::check_grounding rather
than writing a new one - it already implements exactly this stage's contract
(per-sentence word-overlap against the retrieved context, all-or-nothing
grounding verdict) and was built for this exact purpose but never wired in.
Adds the class-appropriateness check the spec also asks for (section 12,
15), which the original module didn't cover.
"""
from backend.app.services.new_rag.validation.grounding import check_grounding
from backend.app.services.question_pipeline.schemas import CurriculumContext, GroundingResult

# Rough word-length ceiling per class band, just to catch answers that are
# clearly too advanced/wordy for the stated grade - not a readability model.
_MAX_WORDS_BY_BAND = {
    "low": 120,   # classes 1-5
    "mid": 220,   # classes 6-8
    "high": 350,  # classes 9-12
}


def _band_for_class(class_name: str) -> str:
    try:
        grade = int("".join(ch for ch in str(class_name) if ch.isdigit()) or "0")
    except ValueError:
        grade = 0
    if 1 <= grade <= 5:
        return "low"
    if 6 <= grade <= 8:
        return "mid"
    if grade >= 9:
        return "high"
    return "mid"


def validate_grounding(answer: str, context: str, curriculum: CurriculumContext) -> GroundingResult:
    if not context.strip():
        # Nothing to ground against - the answer must be a refusal/explanation, not fabricated content.
        return GroundingResult(is_grounded=False, reason="no retrieved context to check grounding against")

    grounding = check_grounding(context, answer)

    band = _band_for_class(curriculum.class_name)
    word_count = len((answer or "").split())
    class_appropriate = word_count <= _MAX_WORDS_BY_BAND[band]

    return GroundingResult(
        is_grounded=bool(grounding["is_grounded"]),
        overlap_ratio=grounding["overlap_ratio"],
        unsupported_sentences=grounding["unsupported_sentences"],
        class_appropriate=class_appropriate,
        reason="ok" if grounding["is_grounded"] else "unsupported sentences detected",
    )
