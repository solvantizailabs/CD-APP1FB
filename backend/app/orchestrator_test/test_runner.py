import os
import sys
import os
import json
import time
import datetime
import re
from typing import Dict, Any, Optional

# Ensure project root is in python path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(override=True)

# Imports from backend app
from backend.app.services.llm.openai_client import OPENAI_MODEL, create_client
from backend.app.core.firebase.firebase_init import db
from backend.app.services.retrieval import qdrant_service

# Path to master prompt file
PROMPT_FILE_PATH = os.path.join(os.path.dirname(__file__), "master_orchestrator_prompt.txt")
OUTPUTS_DIR = os.path.join(os.path.dirname(__file__), "test_outputs")
os.makedirs(OUTPUTS_DIR, exist_ok=True)

# Initialize Qdrant and Gemini API Client
try:
    qdrant_service.initialize()
except Exception as _qdrant_init_err:
    print(f"[WARN] Qdrant initialization failed at startup (will retry on first request): {_qdrant_init_err}")
# openai_client will be dynamically fetched inside functions to avoid NoneType binding issue



def load_master_prompt_template() -> str:
    """Reads the locked Master System Prompt from file."""
    if os.path.exists(PROMPT_FILE_PATH):
        with open(PROMPT_FILE_PATH, "r", encoding="utf-8") as f:
            return f.read()
    else:
        raise FileNotFoundError(f"Master prompt file not found at: {PROMPT_FILE_PATH}")


from google.cloud.firestore_v1.base_query import FieldFilter

def authenticate_student_by_email(email: str) -> Dict[str, Any]:
    """
    Authenticates/fetches student profile from Firestore by email.
    Strips trailing spaces from Firestore string fields to ensure robust matching.
    """
    email_clean = email.strip().lower()
    firestore_db = db

    if firestore_db:
        try:
            users_ref = firestore_db.collection("users").get()
            for doc in users_ref:
                data = doc.to_dict()
                doc_email = str(data.get("email", "")).strip().lower()
                if doc_email == email_clean:
                    user_name = str(data.get("name", "Student")).strip()
                    user_class = int(data.get("class", 7))
                    user_board = str(data.get("board", "CBSE")).strip()
                    user_role = str(data.get("role", "student")).strip()
                    print(f"[AUTH SUCCESS] Logged in as: {user_name} (Class {user_class}, {user_board})")
                    return {
                        "uid": doc.id,
                        "email": email_clean,
                        "name": user_name,
                        "class": user_class,
                        "board": user_board,
                        "role": user_role
                    }
        except Exception as e:
            print(f"[AUTH WARN] Error querying Firestore users: {e}")

    # Default fallback profile for testing (e.g. Praneeth Class 7)
    print(f"[AUTH NOTICE] Email '{email_clean}' not found in Firestore. Using fallback profile.")
    return {
        "uid": "test_user_007",
        "email": email_clean,
        "name": "Praneeth",
        "class": 7,
        "board": "CBSE",
        "role": "student"
    }


def get_cached_curriculum_metadata(grade: int) -> str:
    """
    Fetches available subject & chapter summaries for the student's grade.
    Prefers the new classes/{grade}/subjects/{subject} schema (real,
    LLM-generated per-chapter summaries from the 'rag' branch's ingestion
    pipeline) and only falls back to the old flat collections/local cache
    (bare chapter titles, no summary content) if nothing exists there yet.
    """
    chapter_summaries = []
    for subject_id, data in _get_classes_subjects_docs(grade):
        subject_label = data.get("subject") or subject_id
        for ch in data.get("chapters", []):
            ch_name = ch.get("chapter_name", "")
            summary = (ch.get("summary") or "").strip()
            if summary:
                # Keep prompt size sane - full summaries are prose paragraphs.
                chapter_summaries.append(f"• {subject_label} | Chapter: {ch_name} -> Summary: {summary[:400]}")
            else:
                chapter_summaries.append(f"• {subject_label} | Chapter: {ch_name}")

    if chapter_summaries:
        return "\n".join(chapter_summaries)

    firestore_db = db

    if firestore_db:
        try:
            chapters_ref = firestore_db.collection("chapters").where(filter=FieldFilter("class_level", "==", grade)).get()
            for doc in chapters_ref:
                data = doc.to_dict()
                subject = data.get("subject", "Science")
                title = data.get("title") or data.get("chapter_name", "Chapter")
                summary = data.get("summary") or data.get("topics", "")
                chapter_summaries.append(f"â€¢ {subject} | {title} -> Key topics: {summary}")
        except Exception as e:
            print(f"[CURRICULUM CACHE WARN] Firestore query exception: {e}")

    # Load local JSON chapter cache from chapterdata/chapters_cache.json
    cache_path = os.path.join(PROJECT_ROOT, "chapterdata", "chapters_cache.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                local_cache = json.load(f)
            for key, val in local_cache.items():
                # Key format: '8_social', '6_science'
                parts = key.split("_")
                key_grade = int(parts[0]) if parts[0].isdigit() else None
                subj_name = parts[1].capitalize() if len(parts) > 1 else "Curriculum"
                
                # Match grade (or load for all available grades)
                if key_grade is None or key_grade == grade:
                    chaps = val.get("chapters", [])
                    for ch in chaps:
                        ch_name = ch.get("chapter_name", "")
                        chapter_summaries.append(f"â€¢ Class {key_grade or grade} {subj_name} | Chapter: {ch_name}")
        except Exception as e:
            print(f"[CURRICULUM CACHE WARN] chapters_cache.json load exception: {e}")

    if not chapter_summaries:
        # Fallback chapter list for Class 7 / Class 8 testing
        if grade == 7:
            chapter_summaries = [
                "â€¢ Science | Chapter 1: Nutrition in Plants -> Key topics: Photosynthesis, chlorophyll, stomata, autotrophs, solar energy.",
                "â€¢ Science | Chapter 2: Nutrition in Animals -> Key topics: Human digestive system, alimentary canal, stomach, small intestine, ruminants.",
                "â€¢ Science | Chapter 3: Heat -> Key topics: Temperature measurement, conduction, convection, radiation, insulators.",
                "â€¢ Mathematics | Chapter 1: Integers -> Key topics: Positive/negative numbers, addition/subtraction rules, number line.",
                "â€¢ Social Science | Chapter 1: Environment -> Key topics: Ecosystem, biotic/abiotic components, atmosphere, hydrosphere."
            ]
        else:
            chapter_summaries = [
                "â€¢ Social Studies | Chapter: The Kakatiyas - Emergence of a Regional Kingdom -> Key topics: Rani Rudramadevi, Prataparudra, Warangal Fort, Kakatiya administration.",
                "â€¢ Social Studies | Chapter: Making of Laws in the State Assembly -> Key topics: Legislative Assembly, MLA, Bill to Law, Governor approval.",
                "â€¢ Social Studies | Chapter: The Indian Constitution -> Key topics: Preamble, Fundamental Rights, Secularism, Democracy.",
                "â€¢ Science | Chapter: Crop Production -> Key topics: Agricultural practices, sowing, irrigation, harvesting."
            ]

    return "\n".join(chapter_summaries)


def _get_classes_subjects_docs(grade: int) -> list:
    """
    Reads the new consolidated schema: classes/{grade}/subjects/{subject_id},
    each holding {class, subject, book_uuid, chapters: [{chapter_name, summary, ...}]}.
    This is the schema the 'rag' branch's ingestion pipeline writes (real,
    LLM-generated chapter summaries) - it replaced the old flat 'summaries'/
    'chapters' collections, which are now empty in production. Returns a list
    of (subject_doc_id, data_dict) tuples, or [] if nothing exists yet for
    this grade (e.g. before any book has been migrated/ingested).
    """
    try:
        subj_refs = db.collection("classes").document(str(grade)).collection("subjects").stream()
        return [(doc.id, doc.to_dict() or {}) for doc in subj_refs]
    except Exception as e:
        print(f"[CURRICULUM CACHE WARN] classes/{grade}/subjects query exception: {e}")
        return []


def resolve_book_uuid_for_subject(grade: int, matched_subject: str) -> str:
    """
    Resolves the real book_uuid for a validated matched_subject directly from
    classes/{grade}/subjects/{subject}.book_uuid. Replaces the old
    summaries/{subject}_{grade} lookup, which always misses now (that
    collection is empty - see [[rag-branch-merge-plan]] memory). Several
    near-duplicate subject documents can exist per grade (e.g. 'social',
    'social science', 'social studies' for the same real subject) due to
    inconsistent naming at write time; among fuzzy-matching candidates, this
    prefers the one with a real book_uuid and the most chapters.

    Candidates are gated on qdrant_service.book_has_content(): a subject doc
    can have Firestore chapter metadata (written at upload time) with zero
    matching Qdrant vectors (never actually (re-)ingested through the
    parent-child pipeline) - a book_uuid with no content would resolve
    "successfully" here but then silently return zero RAG chunks downstream.
    """
    normalized = normalize_subject_name(matched_subject or "")
    if not normalized:
        return ""
    best_uuid, best_score = "", -1
    for subject_id, data in _get_classes_subjects_docs(grade):
        sid = subject_id.strip().lower()
        if normalized == sid or normalized in sid or sid in normalized:
            book_uuid = data.get("book_uuid") or ""
            if not book_uuid or not qdrant_service.book_has_content(book_uuid):
                continue
            score = 100 + len(data.get("chapters", []))
            if score > best_score:
                best_score, best_uuid = score, book_uuid
    return best_uuid


def get_valid_subjects_for_grade(grade: int) -> set:
    """
    Real subject names actually available for this grade. Used to validate the
    orchestrator LLM's claimed matched_subject before trusting a CURRICULUM
    classification - the LLM can otherwise hallucinate a plausible-sounding
    subject (e.g. "Class 8 Science" for a student whose grade only actually
    has Social Studies loaded), silently mis-marking a general-knowledge
    question as curriculum and skipping real grounding.

    A subject only counts as "valid" here if its book_uuid actually has
    ingested content in Qdrant (qdrant_service.book_has_content()) - a
    classes/{grade}/subjects/{subject} doc can exist with chapter metadata
    but zero matching vectors (uploaded but never (re-)ingested through the
    parent-child pipeline). Without this gate, such a subject would pass
    validation, RAG retrieval would then resolve a book_uuid but return no
    chunks, and the student would get an ungrounded "curriculum" answer
    instead of being correctly routed to GENERAL_KNOWLEDGE.
    """
    subjects = {
        sid.strip().lower()
        for sid, data in _get_classes_subjects_docs(grade)
        if qdrant_service.book_has_content(data.get("book_uuid") or "")
    }
    if subjects:
        return subjects

    # Fall back to the old flat schema / local cache if the new schema has
    # nothing for this grade yet (no book migrated/ingested there yet).
    firestore_db = db
    if firestore_db:
        try:
            chapters_ref = firestore_db.collection("chapters").where(filter=FieldFilter("class_level", "==", grade)).get()
            for doc in chapters_ref:
                data = doc.to_dict()
                subj = data.get("subject")
                if subj:
                    subjects.add(str(subj).strip().lower())
        except Exception as e:
            print(f"[CURRICULUM VALIDATION WARN] Firestore query exception: {e}")

    cache_path = os.path.join(PROJECT_ROOT, "chapterdata", "chapters_cache.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                local_cache = json.load(f)
            for key, entry in local_cache.items():
                parts = key.split("_")
                if len(parts) >= 2 and parts[0].isdigit() and int(parts[0]) == grade:
                    if qdrant_service.book_has_content(entry.get("book_uuid") or ""):
                        subjects.add(parts[1].strip().lower())
        except Exception as e:
            print(f"[CURRICULUM VALIDATION WARN] chapters_cache.json load exception: {e}")

    return subjects


def normalize_subject_name(raw: str) -> str:
    """Strips 'Class N'/'Grade N' framing the LLM sometimes adds, e.g.
    'Class 8 Science' -> 'science', so it can be compared against real subject names."""
    if not raw:
        return ""
    s = re.sub(r"\b(?:class|grade)\s*\d+\b", "", raw, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip(" -_:")
    return s.lower()


def restyle_text_narration(text: str, style: str, client, model: str) -> Optional[str]:
    """
    Focused, single-purpose restyle pass (see call site in
    run_orchestrator_pipeline for why this exists as a separate call rather
    than folding style into the main orchestrator prompt). Only rewrites
    PRESENTATION - must not add, remove, or change any fact/number/formula.
    Returns None (caller keeps the original) on any failure - this is a
    quality enhancement, never allowed to block or corrupt an answer.
    """
    if style == "storytelling":
        instruction = (
            "Rewrite this into a STORYTELLING style: open with ONE concrete narrative or "
            "analogy sentence that frames the concept (e.g. a relatable everyday scenario), "
            "then weave the existing facts into flowing prose around that analogy. "
            "Do not use bullet points. Do not lose or alter any fact, number, or formula."
        )
    elif style == "detailed":
        instruction = (
            "Rewrite this into a DETAILED style: explicit numbered steps (1. 2. 3. ...), "
            "each step a complete sentence. Do not use unordered bullets or a single paragraph. "
            "Do not lose or alter any fact, number, or formula."
        )
    else:
        return None

    prompt = f"{instruction}\n\nOriginal text:\n{text}\n\nRewritten text (same facts, new style, no preamble/commentary, just the rewritten text itself):"

    try:
        response = client.models.generate_content(model=model, contents=[prompt], config={"temperature": 0.4})
        rewritten = (response.text or "").strip()
        return rewritten if rewritten else None
    except Exception as e:
        print(f"[RESTYLE] LLM call failed: {e}")
        return None


def detect_inline_style_override(query: str) -> Optional[str]:
    """
    SS2.2's "a stated preference is a default, not a lock" requirement,
    applied to the restyle pass (see restyle_text_narration) - the main
    orchestrator prompt's own OVERRIDE RULE directive was confirmed
    unreliable for the same reason the base style directive was (too many
    competing instructions in one call), so inline intent needs to be
    detected here too, not just described in the prompt.
    """
    q = (query or "").lower()
    if any(p in q for p in ("step by step", "step-by-step", "in detail", "in-depth", "detailed")):
        return "detailed"
    if any(p in q for p in ("story", "analogy", "storytelling", "like a story")):
        return "storytelling"
    if any(p in q for p in ("quick answer", "quickly", "brief", "short answer", "just the answer", "direct answer")):
        return "direct"
    return None


def run_orchestrator_pipeline(raw_query: str, student_profile: Dict[str, Any]) -> Dict[str, Any]:
    """
    Executes the single-pass Orchestrator LLM, runs RAG search if CURRICULUM,
    and returns a complete execution report without Sarvam TTS audio or video rendering.
    """
    # Debug: log the incoming student_profile to verify propagation from request
    try:
        print(f"[ORCH DEBUG] run_orchestrator_pipeline received student_profile: {student_profile}")
    except Exception:
        print("[ORCH DEBUG] run_orchestrator_pipeline received student_profile: <unprintable>")

    openai_client = qdrant_service.openai_client
    if openai_client is None:
        try:
            qdrant_service.initialize()
        except Exception as e:
            print(f"[WARN] Failed to fully initialize qdrant_service dynamically (possibly due to Qdrant DB connection): {e}")
        # Always fetch it after trying, since Gemini client initialization happens first
        openai_client = qdrant_service.openai_client

    if openai_client is None:
        try:
            from backend.app.utils.llm_tracker import instrument_client
            openai_client = create_client()
            openai_client = instrument_client(openai_client)
            print("[ORCHESTRATOR] Standalone fallback OpenAI client initialized successfully.")
        except Exception as fallback_err:
            print(f"[ERROR] Standalone fallback OpenAI client initialization failed: {fallback_err}")

    start_time = time.time()
    system_prompt = load_master_prompt_template()

    # Step 1: Fetch Curriculum Chapter Cache
    grade = student_profile.get("class", 7)
    curriculum_cache_text = get_cached_curriculum_metadata(grade)

    # Step 2: Format System Prompt
    current_date_time = datetime.datetime.now().strftime("%A, %B %d, %Y (%H:%M:%S)")
    formatted_prompt = system_prompt.replace("{student_name}", student_profile.get("name", "Student"))
    formatted_prompt = formatted_prompt.replace("{student_grade}", str(grade))
    formatted_prompt = formatted_prompt.replace("{student_board}", student_profile.get("board", "CBSE"))
    formatted_prompt = formatted_prompt.replace("{current_date_time}", current_date_time)
    formatted_prompt = formatted_prompt.replace("{cached_subjects_and_chapter_summaries}", curriculum_cache_text)
    formatted_prompt = formatted_prompt.replace("{retrieved_top10_chunks}", "[RAG Chunks will be provided if CURRICULUM]")

    # Personalization context (personalized_learning.md SS6.1-SS6.4). All
    # optional - a student with no profile/history yet gets honest
    # "not set"/"none" placeholders rather than a broken template string.
    response_style = student_profile.get("response_style") or "not set"
    quadrant = student_profile.get("quadrant") or "not set"
    escalation_level = student_profile.get("escalation_level", 0) or 0
    if escalation_level >= 2:
        escalation_instruction = f"student has repeated a similar basic question {escalation_level} times on this topic - escalate strongly (favor a diagram/visual framing)"
    elif escalation_level == 1:
        escalation_instruction = "student repeated a similar basic question once on this topic - escalate to a concrete example/analogy"
    else:
        escalation_instruction = "none"

    per_student_history = student_profile.get("per_student_history") or []
    if per_student_history:
        history_lines = []
        for h in per_student_history[:3]:
            q = h.get("reformulated_question") or h.get("question") or ""
            summary = (h.get("answer_summary") or "")[:200]
            history_lines.append(f"- Previously asked: \"{q}\" -> covered: {summary}")
        per_student_memory_context = "\n".join(history_lines)
    else:
        per_student_memory_context = "none - this is either the student's first question on this topic, or no related prior history was found"

    tough_subjects = student_profile.get("tough_subjects") or []
    easy_subjects = student_profile.get("easy_subjects") or []
    subject_notes = []
    if tough_subjects:
        subject_notes.append(f"finds these subjects tough: {', '.join(tough_subjects)}")
    if easy_subjects:
        subject_notes.append(f"finds these subjects easy: {', '.join(easy_subjects)}")
    tough_easy_subjects_note = "; ".join(subject_notes) if subject_notes else "not set"

    formatted_prompt = formatted_prompt.replace("{student_response_style}", response_style)
    formatted_prompt = formatted_prompt.replace("{student_quadrant}", quadrant)
    formatted_prompt = formatted_prompt.replace("{escalation_instruction}", escalation_instruction)
    formatted_prompt = formatted_prompt.replace("{per_student_memory_context}", per_student_memory_context)
    formatted_prompt = formatted_prompt.replace("{tough_easy_subjects_note}", tough_easy_subjects_note)

    # Step 3: Run Orchestrator LLM (Single Pass)
    print(f"\n[ORCHESTRATOR LLM] Executing single-pass evaluation for Class {grade} query...")
    user_prompt = f"USER RAW QUERY: \"{raw_query}\""

    MODEL = os.environ.get("OPENAI_MODEL", OPENAI_MODEL)

    # Step 1/3 â€” Query classification (keyword-based, instant, free)
    # If query is GK/current events, we perform live Google Search grounding to answer.
    # Otherwise, for curriculum/school questions, we skip search to save 15-20 seconds.
    _GK_KEYWORDS = {
        "yesterday", "today", "latest", "recent", "breaking", "live", "ongoing",
        "won", "win", "lost", "score", "result", "match", "election", "elected",
        "protest", "strike", "rally", "arrested", "verdict", "announced", "launched",
        "world cup", "ipl", "fifa", "olympics", "championship",
        "party", "government", "minister", "president", "prime minister",
        "news", "happened", "incident", "2026", "2025",
    }
    query_lower = raw_query.lower()
    # Match keywords as complete words. A substring check would classify
    # "winter" as GK because the GK list contains the word "win".
    is_gk_query = any(
        re.search(rf"(?<!\w){re.escape(kw)}(?!\w)", query_lower)
        for kw in _GK_KEYWORDS
    )
    query_type = "GK_KNOWLEDGE" if is_gk_query else "CURRICULUM"
    print(f"[ORCHESTRATOR] Step 1/3 â€” Query type: {query_type} (keyword match, 0ms)")

    # Enable Google Search grounding dynamically for GK/live queries
    config = {"temperature": 0.2}

    # Step 2/3 â€” Main Orchestrator LLM call â€” single model: gemini-2.5-flash
    search_note = "with Google Search Grounding (may take 15-25s)" if is_gk_query else "without Search Grounding (fast)"
    response = None
    last_error = None
    for attempt in range(1, 4):  # up to 3 retries on 503
        try:
            _llm_start = time.time()
            print(f"[ORCHESTRATOR] Step 2/3 â€” [{MODEL}] {search_note} (Attempt {attempt}/3)...")
            response = openai_client.models.generate_content(
                model=MODEL,
                contents=[formatted_prompt, user_prompt],
                config=config
            )
            _llm_dur = time.time() - _llm_start
            if response and response.text:
                print(f"[ORCHESTRATOR LLM SUCCESS] [{MODEL}] responded in {_llm_dur:.1f}s")
                break
        except Exception as err:
            last_error = err
            _err_str = str(err)
            _elapsed = time.time() - _llm_start
            if "503" in _err_str or "UNAVAILABLE" in _err_str:
                _backoff = 2.0 * attempt
                print(f"[WARN] [{MODEL}] 503 (attempt {attempt}/3, {_elapsed:.1f}s), retrying in {_backoff:.0f}s...")
                time.sleep(_backoff)
            else:
                print(f"[ERROR] [{MODEL}] failed after {_elapsed:.1f}s: {_err_str[:120]}")
                break

    if not response or not response.text:
        print(f"[ERROR] [{MODEL}] failed. Last error: {last_error}")
        return {
            "error": str(last_error),
            "raw_user_query": raw_query,
            "status": "FAILED"
        }





    raw_json_text = response.text.strip()

    # The model's text_narration field routinely contains LaTeX (e.g. \frac{a}{b},
    # \times, \left(, \right, \text) - single backslashes that are not valid JSON
    # string content. Most of those letters (b, f, n, r, t, u) also happen to be
    # legal JSON escapes, so json.loads doesn't always error - it silently eats the
    # leading letter as a control character instead (\frac -> "\x0crac"). Only \"
    # and \\ are ever intentional here; every other backslash is LaTeX and must be
    # doubled so it survives as literal text.
    def _repair_invalid_escapes(text: str) -> str:
        return re.sub(
            r'\\(.)',
            lambda m: m.group(0) if m.group(1) in ('"', '\\') else '\\\\' + m.group(1),
            text,
        )

    # Robust JSON extractor
    def extract_and_parse_json(text: str) -> dict:
        text_clean = text.strip()
        first_brace = text_clean.find("{")
        last_brace = text_clean.rfind("}")
        json_candidate = (
            text_clean[first_brace:last_brace + 1]
            if first_brace != -1 and last_brace != -1 and last_brace > first_brace
            else text_clean
        )
        try:
            return json.loads(json_candidate)
        except json.JSONDecodeError:
            return json.loads(_repair_invalid_escapes(json_candidate))

    def _salvage_text_narration(text: str) -> str | None:
        """Best-effort extraction of just the text_narration value when the
        JSON as a whole still won't parse, so we never show the user the raw
        JSON payload."""
        match = re.search(r'"text_narration"\s*:\s*"(.*?)(?<!\\)"\s*,\s*"video_storyboard"', text, re.DOTALL)
        if not match:
            match = re.search(r'"text_narration"\s*:\s*"(.*?)(?<!\\)"\s*\}', text, re.DOTALL)
        if not match:
            return None
        raw_value = match.group(1)
        try:
            return json.loads(f'"{_repair_invalid_escapes(raw_value)}"')
        except Exception:
            return raw_value.replace('\\"', '"').replace('\\n', '\n')

    try:
        # Try cleaning markdown code fences first
        cleaned_text = raw_json_text
        if cleaned_text.startswith("```json"):
            cleaned_text = cleaned_text[7:]
        if cleaned_text.startswith("```"):
            cleaned_text = cleaned_text[3:]
        if cleaned_text.endswith("```"):
            cleaned_text = cleaned_text[:-3]

        orchestrator_output = extract_and_parse_json(cleaned_text.strip())
    except Exception as parse_err:
        print(f"[WARN] Failed to parse LLM JSON: {parse_err}. Attempting to salvage text_narration.")
        salvaged = _salvage_text_narration(raw_json_text)
        # Fall back to the raw text only if salvage genuinely fails - never show
        # the user the raw JSON envelope itself.
        narration = salvaged if salvaged is not None else raw_json_text
        orchestrator_output = {
            "is_authorized": True,
            "refusal_reason": None,
            "classification": "GENERAL_KNOWLEDGE",
            "reformulated_query": raw_query,
            "matched_subject": "General Knowledge",
            "matched_chapter": None,
            "complexity_level": 1,
            "format_decision": "QUICK_ANSWER",
            "text_narration": narration,
            "video_storyboard": None
        }


    # Extract Orchestrator Decisions
    is_authorized = orchestrator_output.get("is_authorized", True)
    classification = orchestrator_output.get("classification", "CURRICULUM")
    reformulated_query = orchestrator_output.get("reformulated_query") or raw_query
    matched_subject = orchestrator_output.get("matched_subject")
    matched_chapter = orchestrator_output.get("matched_chapter")
    format_decision = orchestrator_output.get("format_decision", "QUICK_ANSWER")

    # Validate the LLM's claimed curriculum match against real subject data
    # before trusting it - see get_valid_subjects_for_grade() docstring.
    if is_authorized and classification == "CURRICULUM":
        valid_subjects = get_valid_subjects_for_grade(grade)
        normalized_match = normalize_subject_name(matched_subject or "")
        subject_is_real = bool(normalized_match) and any(
            normalized_match == v or normalized_match in v or v in normalized_match
            for v in valid_subjects
        )
        if not subject_is_real:
            print(
                f"[CURRICULUM VALIDATION] Rejecting hallucinated match '{matched_subject}' - "
                f"Class {grade} only actually has: {sorted(valid_subjects) or 'no cached subjects'}. "
                f"Downgrading classification to GENERAL_KNOWLEDGE."
            )
            classification = "GENERAL_KNOWLEDGE"
            matched_subject = None
            matched_chapter = None
            orchestrator_output["classification"] = classification
            orchestrator_output["matched_subject"] = None
            orchestrator_output["matched_chapter"] = None

    rag_chunks = []
    rag_executed = False
    resolved_book_uuid = ""

    # Step 4: Handle RAG Retrieval if Authorized + CURRICULUM
    if is_authorized and classification == "CURRICULUM":
        print(f"[ORCHESTRATOR] Step 3/3 â€” RAG vector search for: '{reformulated_query[:60]}...'")
        print(f"[RAG SEARCH] Running hybrid vector search for: '{reformulated_query}'...")
        rag_executed = True
        try:
            # Resolve book_uuid from the new classes/{grade}/subjects/{subject}
            # schema (replaces the old summaries/{subject}_{grade} lookup,
            # which always misses now - that collection is empty in production).
            if matched_subject:
                resolved_book_uuid = resolve_book_uuid_for_subject(grade, matched_subject)
                if resolved_book_uuid:
                    print(f"[RAG SEARCH] Resolved book_uuid for subject '{matched_subject}' (Class {grade}): {resolved_book_uuid}")
                else:
                    print(f"[RAG SEARCH WARNING] No book_uuid found for subject '{matched_subject}' (Class {grade})")

            if not resolved_book_uuid:
                raise RuntimeError(
                    f"Could not resolve a book UUID for subject '{matched_subject}' and Class {grade}."
                )

            # Query Qdrant vector database using qdrant_service.hybrid_search
            if hasattr(qdrant_service, 'hybrid_search'):
                filters = {}
                if matched_chapter:
                    filters["chapter_names"] = [matched_chapter]
                raw_chunks, _, _ = qdrant_service.hybrid_search(
                    book_uuid=resolved_book_uuid,
                    query=reformulated_query,
                    keywords=[],
                    conceptual_score=0.7,
                    metadata_filters=filters
                )

                # Safety net: the orchestrator LLM's matched_chapter guess is
                # sometimes wrong (e.g. it can confuse lexically-similar
                # chapters like "respiration" vs "reproduction"), which
                # silently narrows the search to the wrong chapter and
                # returns weak, irrelevant top hits instead of erroring. A
                # low top score on a chapter-filtered search is a reliable
                # signal of that failure mode, since a real match in the
                # right chapter typically scores well above this. When it
                # happens, retry across the whole subject book (still gated
                # to the validated book_uuid) rather than trust the guess.
                if filters:
                    top_score = 0.0
                    if raw_chunks:
                        first = raw_chunks[0]
                        top_score = first[0] if isinstance(first, tuple) and len(first) >= 2 else getattr(first, "score", 0.0)
                    if (top_score or 0.0) < 0.55:
                        print(
                            f"[RAG SEARCH] Weak top score ({top_score:.3f}) for chapter-filtered "
                            f"search on '{matched_chapter}' - retrying across the whole subject book."
                        )
                        raw_chunks, _, _ = qdrant_service.hybrid_search(
                            book_uuid=resolved_book_uuid,
                            query=reformulated_query,
                            keywords=[],
                            conceptual_score=0.7,
                            metadata_filters={}
                        )
            elif hasattr(qdrant_service, 'search_books_hybrid'):
                raw_chunks = qdrant_service.search_books_hybrid(
                    query=reformulated_query,
                    limit=10,
                    subject=matched_subject,
                    class_level=grade
                )
            else:
                raw_chunks = []

            for idx, c in enumerate(raw_chunks[:10], start=1):
                # hybrid_search returns (hybrid_score, payload_dict), while
                # some fallback implementations return a result object/dict.
                # Normalize both shapes before writing the audit report.
                if isinstance(c, tuple) and len(c) >= 2:
                    result_score, payload = c[0], c[1]
                else:
                    result_score, payload = getattr(c, "score", 0.0), c

                if hasattr(payload, "payload"):
                    payload = payload.payload
                if not isinstance(payload, dict):
                    payload = {}

                rag_chunks.append({
                    "chunk_index": idx,
                    "score": round(float(result_score or 0.0), 4),
                    "book_name": payload.get("book_name", payload.get("book", "")),
                    "chapter_name": payload.get("chapter_name", payload.get("chapter", "")),
                    "page_number": payload.get("chpstpage") or payload.get("pdf_page"),
                    "content_snippet": (payload.get("text", payload.get("content", "")) or "")[:150] + "..."
                })
        except Exception as e:
            print(f"[RAG SEARCH NOTICE] Qdrant search fallback: {e}")
            # Do not write a fabricated chunk when retrieval fails; the audit
            # report must reflect the actual Qdrant result.
            rag_chunks = []

    # Restyle pass (personalized_learning.md - real bug found via live log
    # analysis against class10_personalization_test_guide.md): asking the
    # single main orchestrator call to simultaneously handle safety,
    # classification, RAG grounding, JSON-schema output, AND a storytelling/
    # detailed style directive was CONFIRMED not to work reliably in
    # practice - two rounds of strengthening the wording in
    # master_orchestrator_prompt.txt made no measurable difference (still
    # plain bulleted facts, zero analogy, for a "storytelling" preference).
    # Root cause: too many competing instructions in one call for a small
    # model to reliably prioritize a stylistic one over factual/grounding
    # ones. Fix: a second, narrowly-scoped LLM call whose ONLY job is to
    # restyle already-correct text - nothing else competing for its
    # attention - so it's a much easier instruction-following task.
    # Inline query intent (SS2.2) wins over the stored preference - e.g. a
    # "direct" student who explicitly asks for "detailed step-by-step points"
    # gets detailed for this turn only.
    _response_style_for_restyle = detect_inline_style_override(raw_query) or (student_profile or {}).get("response_style")
    if (
        format_decision == "QUICK_ANSWER"
        and orchestrator_output.get("text_narration")
        and _response_style_for_restyle in ("storytelling", "detailed")
    ):
        try:
            restyled = restyle_text_narration(
                orchestrator_output["text_narration"], _response_style_for_restyle, openai_client, MODEL
            )
            if restyled:
                orchestrator_output["text_narration"] = restyled
        except Exception as restyle_err:
            print(f"[RESTYLE] Failed, keeping original text_narration: {restyle_err}")

    execution_time = round(time.time() - start_time, 2)

    # Assemble Audit Report
    report = {
        "timestamp": datetime.datetime.now().isoformat(),
        "execution_time_seconds": execution_time,
        "authenticated_student": student_profile,
        "raw_user_query": raw_query,
        "orchestrator_output": orchestrator_output,
        "rag_retrieval_executed": rag_executed,
        "retrieved_top10_chunks": rag_chunks,
        # The real (validated, Firestore-resolved) book_uuid for this query's
        # matched_subject+grade, or "" if this is GENERAL_KNOWLEDGE / resolution
        # failed. Callers (e.g. chat.py) should use THIS instead of whatever
        # book the student happens to have open, when triggering video generation.
        "resolved_book_uuid": resolved_book_uuid
    }

    # Save Audit Report to test_outputs/
    timestamp_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_filename = f"query_report_{timestamp_str}.json"
    report_path = os.path.join(OUTPUTS_DIR, report_filename)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    report["saved_report_path"] = report_path
    return report

if __name__ == "__main__":
    print("\n--- RUNNING STANDALONE ORCHESTRATOR TEST ---")
    user = authenticate_student_by_email("student8@cg.com")
    rep = run_orchestrator_pipeline("Explain about the rule of Rani Rudramadevi?", user)
    print("\n[SUCCESS] Pipeline Executed!")
    print(f"Report File  : {rep.get('saved_report_path')}")
    out = rep.get("orchestrator_output", {})
    print(f"Authorized   : {out.get('is_authorized')}")
    print(f"Classified As: {out.get('classification')}")
    print(f"Matched Chap : {out.get('matched_chapter')}")
    print(f"Format Dec.  : {out.get('format_decision')}")
    print(f"Reformulated : {out.get('reformulated_query')}")
    print("--------------------------------------------\n")

