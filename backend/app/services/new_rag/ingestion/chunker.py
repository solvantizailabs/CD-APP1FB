"""
Stage 3/4: parent (topic) and child chunk construction from a validated
topics_manifest + raw_pages. See docs/RAG_REDESIGN_PLAN.md, section 5.

Parent = one topic's full concatenated text, built strictly from the pages
(and anchor-resolved partial pages) assigned to it - never crosses a topic
boundary, by construction. Child = paragraph-aware size-slices within a
single parent's own text only, never independently re-slicing raw pages.
"""
import re
import uuid
from functools import lru_cache
from typing import Dict, List, Optional

CHILD_TARGET_TOKENS = 400
CHILD_OVERLAP_WORDS = 50  # approx bridge between adjacent children of the same parent
PARENT_SOFT_CEILING_TOKENS = 3000

_CHAPTER_NUMBER_RE = re.compile(r"^\s*(\d+)")

# CTO spec section 7's chunk-type set. DIAGRAM_DESCRIPTION and TABLE are
# deliberately excluded here - those are assigned deterministically by the
# diagram/table extraction stage (pipeline/rag_pipeline.py), never by the
# topic-detection LLM call, so they're valid chunk_type values overall but
# not values this normalizer should ever receive from a topic.
VALID_TOPIC_CHUNK_TYPES = {
    "topic", "definition", "concept", "explanation", "example", "formula",
    "theorem", "fact", "process", "question", "exercise", "summary", "activity",
}


@lru_cache(maxsize=1)
def _get_encoder():
    # Lazy (2026-09-02, Render free-tier 512Mi OOM fix): tiktoken's
    # get_encoding() loads/caches the actual BPE vocab table at call time,
    # not just an import - this used to run unconditionally at module
    # import, on every app boot, even though it's only ever needed during
    # real chapter ingestion (an occasional admin action), not for serving
    # normal student questions.
    import tiktoken
    return tiktoken.get_encoding("cl100k_base")


def _token_len(text: str) -> int:
    return len(_get_encoder().encode(text)) if text else 0


def normalize_chunk_type(raw_type: Optional[str]) -> str:
    """
    Validates and lowercases the LLM-reported topic_type into a real
    chunk_type. "topic" (a generic section heading, not a more specific
    type) normalizes to "concept" - the general-purpose default this
    pipeline already used before type expansion, kept as the fallback for
    both a genuine "topic" classification and any value the LLM returns
    that isn't in the known set (never trust an unrecognized value blindly -
    fall back to the safe default instead of propagating garbage).
    """
    t = (raw_type or "").strip().lower()
    if t == "topic":
        return "concept"
    if t in VALID_TOPIC_CHUNK_TYPES:
        return t
    return "concept"


def _extract_chapter_number(chapter_name: str) -> Optional[int]:
    """NCERT chapter titles are printed as "<number> <Title>" (e.g.
    "1 Chemical Reactions and Equations") - the LLM reads this directly as
    the chapter_title in Stage 2, so the leading number is reliably present
    when detection succeeded at all. Returns None rather than guessing when
    it isn't (e.g. an Untitled placeholder chapter_name)."""
    if not chapter_name:
        return None
    m = _CHAPTER_NUMBER_RE.match(chapter_name)
    return int(m.group(1)) if m else None


def metadata_fields(chapter_name: str, topic_name: str, chunk_type: str, start_page: int,
                     section: Optional[str] = None, learning_objective: Optional[str] = None,
                     pdf_page: Optional[int] = None) -> Dict:
    """
    The CTO spec's per-chunk metadata fields (docs/RAG_SPEC_ALIGNMENT_PLAN.md,
    section 3) that are genuinely derivable at chunking time. `subtopic` and
    `concept` are included as real keys but left None - the current chunking
    granularity stops at topic level (see chunker.py module docstring), so
    populating them here would mean fabricating data that doesn't exist yet,
    not a real value. `has_formula`/`has_question`/`has_definition` now
    derive from the real chunk_type classification (Phase 3's 14-type
    expansion) rather than the honest-False placeholder Phase 1 used before
    that classification existed.

    `section`/`learning_objective` (doc 01 §10, doc 02 §1/§4/§6, doc 03 §5)
    are Stage 2's anchor-verified output, passed through as-is - both
    genuinely None for a chapter with no numbered section headings or where
    the LLM didn't produce a value, not a placeholder like subtopic/concept
    above. `pdf_page` (doc 02 §1/§4/§6's "distinct from printed_page") is the
    physical PDF page index computed once in Stage 1 (pdf_parser.py) -
    `page_number` above stays the printed textbook page number for existing
    consumers, this is an additive field, not a replacement.
    """
    return {
        "topic": topic_name,
        "subtopic": None,
        "concept": None,
        "section": section,
        "learning_objective": learning_objective,
        "chapter_number": _extract_chapter_number(chapter_name),
        "page_number": start_page,
        "pdf_page": pdf_page,
        "has_table": chunk_type == "table",
        "has_diagram": chunk_type == "diagram",
        "has_example": chunk_type == "example",
        "has_formula": chunk_type == "formula",
        "has_question": chunk_type in ("question", "exercise"),
        "has_definition": chunk_type == "definition",
    }


def _page_text_slice(page_text: str, start_anchor: Optional[str], end_anchor: Optional[str]) -> str:
    """
    Slice one page's text between two anchors (start inclusive, end
    exclusive), using the same whitespace-tolerant anchor search as Stage 2
    validation - so a heading split across a PDF line-wrap still resolves to
    the correct cut point rather than silently including/excluding it wrong.
    """
    from backend.app.services.new_rag.ingestion.validator import find_anchor_position

    text = page_text
    if start_anchor:
        pos = find_anchor_position(text, start_anchor)
        if pos is not None:
            text = text[pos:]
    if end_anchor:
        pos = find_anchor_position(text, end_anchor)
        if pos is not None:
            text = text[:pos]
    return text


def build_parent_chunks(raw_pages: List[Dict], topics: List[Dict], chapter_id: str, chapter_name: str) -> List[Dict]:
    """
    topics: resolved topic dicts from structure_parser.resolve_topic_boundaries
    (topic_name, topic_type, start_page, start_anchor, end_page, end_anchor).
    Returns one parent chunk dict per topic - the parent's page range can
    never overlap another topic's, since it's built directly from the
    manifest's own boundaries.
    """
    pages_by_num = {p["textbook_page"]: p for p in raw_pages if p.get("textbook_page") is not None}
    parents = []

    for topic in topics:
        parent_id = str(uuid.uuid4())
        text_parts = []
        for pg_num in range(topic["start_page"], topic["end_page"] + 1):
            page = pages_by_num.get(pg_num)
            if not page:
                continue
            start_anchor = topic["start_anchor"] if pg_num == topic["start_page"] else None
            end_anchor = topic.get("end_anchor") if pg_num == topic["end_page"] else None
            text_parts.append(_page_text_slice(page["text"], start_anchor, end_anchor))
        full_text = "\n".join(t for t in text_parts if t.strip())

        chunk_type = normalize_chunk_type(topic.get("topic_type"))
        # The physical PDF page backing this topic's start_page (a printed
        # textbook page number) - looked up from the same raw_pages Stage 1
        # already computed both values for, not re-derived. Falls back to
        # None if this start_page somehow isn't in pages_by_num (shouldn't
        # happen - start_page came from resolve_topic_boundaries, which only
        # ever reports pages present in pages_by_num - but a lookup miss
        # should surface as a missing field, not a wrong guess).
        start_pdf_page = pages_by_num.get(topic["start_page"], {}).get("pdf_page")
        parents.append({
            "parent_chunk_id": parent_id,
            "chapter_id": chapter_id,
            "chapter_name": chapter_name,
            "topic_id": parent_id,  # topic and parent are the same unit, one ID
            "topic_name": topic["topic_name"],
            "chunk_type": chunk_type,
            "text": full_text,
            "token_count": _token_len(full_text),
            "start_page": topic["start_page"],
            "end_page": topic["end_page"],
            **metadata_fields(chapter_name, topic["topic_name"], chunk_type, topic["start_page"],
                               section=topic.get("section"),
                               learning_objective=topic.get("learning_objective"),
                               pdf_page=start_pdf_page),
        })
    return parents


def build_child_chunks(parent: Dict) -> List[Dict]:
    """
    Paragraph-aware slicing strictly within one parent's own text - it is
    mathematically impossible for a child produced here to contain another
    parent's content, since this function only ever sees one parent's text.
    """
    text = parent["text"]
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        # no double-newline paragraph breaks in this parent's text at all -
        # treat single-newlines as paragraph breaks as a fallback split unit
        paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    if not paragraphs and text.strip():
        paragraphs = [text.strip()]

    raw_children: List[str] = []
    current_parts: List[str] = []
    current_tokens = 0

    def flush():
        nonlocal current_parts, current_tokens
        if current_parts:
            raw_children.append("\n\n".join(current_parts))
        current_parts = []
        current_tokens = 0

    for para in paragraphs:
        para_tokens = _token_len(para)

        if para_tokens > CHILD_TARGET_TOKENS:
            # oversized single paragraph - split on sentence boundaries
            flush()
            sentences = [s.strip() for s in para.replace("\n", " ").split(". ") if s.strip()]
            buf, buf_tokens = [], 0
            for s in sentences:
                s_tok = _token_len(s)
                if buf_tokens + s_tok > CHILD_TARGET_TOKENS and buf:
                    raw_children.append(". ".join(buf) + ".")
                    buf, buf_tokens = [], 0
                buf.append(s)
                buf_tokens += s_tok
            if buf:
                raw_children.append(". ".join(buf) + ".")
            continue

        if current_tokens + para_tokens > CHILD_TARGET_TOKENS and current_parts:
            flush()
        current_parts.append(para)
        current_tokens += para_tokens
    flush()

    if not raw_children and text.strip():
        raw_children = [text.strip()]

    children = []
    for i, child_text in enumerate(raw_children):
        overlap_prefix = ""
        if i > 0:
            prev_words = raw_children[i - 1].split()
            overlap_prefix = " ".join(prev_words[-CHILD_OVERLAP_WORDS:]) + " "
        full_text = (overlap_prefix + child_text).strip()
        children.append({
            "chunk_id": str(uuid.uuid4()),
            "parent_chunk_id": parent["parent_chunk_id"],
            "chapter_id": parent["chapter_id"],
            "chapter_name": parent["chapter_name"],
            "topic_id": parent["topic_id"],
            "topic_name": parent["topic_name"],
            "chunk_type": parent["chunk_type"],
            "text": full_text,
            "token_count": _token_len(full_text),
            "start_page": parent["start_page"],
            "end_page": parent["end_page"],
            **metadata_fields(parent["chapter_name"], parent["topic_name"], parent["chunk_type"], parent["start_page"],
                               section=parent.get("section"),
                               learning_objective=parent.get("learning_objective"),
                               pdf_page=parent.get("pdf_page")),
        })
    return children
