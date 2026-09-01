from google.cloud import firestore
from backend.app.core.firebase.firebase_init import db
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

def save_summary_document(class_name: str, subject: str, book_uuid: str, chapters: list):
    """
    Creates a Firestore summary document containing ALL chapter summaries
    for a given class + subject under the centralized content structure.

    Document path:
        classes/{class}/subjects/{subject}
    Example:
        classes/8/subjects/science
    """
    clean_class = "".join(c for c in str(class_name) if c.isdigit())
    if not clean_class:
        clean_class = "unknown"
        
    doc_ref = db.collection("classes").document(clean_class).collection("subjects").document(subject.strip().lower())

    payload = {
        "class": class_name,
        "subject": subject.lower(),
        "book_uuid": book_uuid,
        "chapters": chapters,   # list of chapter dicts
    }

    try:
        logger.info(f"📤 Uploading to Firestore document: classes/{clean_class}/subjects/{subject}")
        doc_ref.set(payload)
        logger.info(f"✓ Document created/updated successfully")
        logger.info(f"📝 Chapters saved:")
        for chapter in chapters:
            sno = chapter.get('sno', 'N/A')
            ch_name = chapter.get('chapter_name', 'Unknown')
            summary_len = len(chapter.get('summary', ''))
            logger.info(f"   ✓ Chapter {sno}: {ch_name} ({summary_len} chars)")

    except Exception as e:
        logger.error(f"❌ Failed to save summary document classes/{clean_class}/subjects/{subject}: {e}")
        raise


# In-memory cache for summaries to avoid repeated Firestore reads
SUMMARY_CACHE = {}

def load_summary_from_firestore(class_name: str, subject: str):
    """
    Loads classes/{class}/subjects/{subject} from Firestore.
    Caches in memory for FAST access (0ms after first load).
    """
    clean_class = "".join(c for c in str(class_name) if c.isdigit())
    if not clean_class:
        clean_class = "unknown"
        
    key = f"{clean_class}_{subject.strip().lower()}"
    logger.debug(f"Attempting to load summary for key: {key}")

    # Check cached
    if key in SUMMARY_CACHE:
        logger.debug(f"Summary for key '{key}' found in in-memory cache.")
        return SUMMARY_CACHE[key]

    logger.debug(f"Summary for key '{key}' not in cache, fetching from Firestore.")
    # Fetch from Firestore
    doc_ref = db.collection("classes").document(clean_class).collection("subjects").document(subject.strip().lower())
    doc = doc_ref.get()

    if not doc.exists:
        logger.warning(f"Summary document not found in Firestore for classes/{clean_class}/subjects/{subject}")
        return None

    data = doc.to_dict()
    SUMMARY_CACHE[key] = data  # cache it
    logger.debug(f"Summary for key '{key}' fetched from Firestore and cached. Contains {len(data.get('chapters', []))} chapters.")
    return data


def normalize_query_string(q: str) -> str:
    """Normalizes query text to enable robust exact-match caching."""
    import re
    if not q:
        return ""
    q = q.lower().strip()
    q = re.sub(r'[^\w\s]', '', q)
    q = re.sub(r'\s+', ' ', q)
    return q


def check_global_query_cache(raw_query: str, class_name: str, subject: str = None, board: str = None, language: str = None, chapter: str = None):
    """
    Checks Firestore nested 'query_cache' for a matching query record.
    Returns the cached data if found and valid on disk, else None.

    Two-stage lookup (personalized_learning.md SS6.7): an exact/near-exact
    normalized-text match first (fast, cheap, unchanged from the original
    implementation), then - only on a miss - a semantic fallback via a
    dedicated Qdrant index, so differently-worded questions with the same
    intent ("explain photosynthesis" vs "how does photosynthesis work")
    still hit the cache instead of silently missing it.

    board/language added 2026-08-30 (Decision 5, docs/ORCHESTRATOR_FRD_V3_ANALYSIS.md):
    a cached answer for one board/language must never be served as correct
    for a different one. Callers should pass Stage 2's `standalone_question`
    here now, not the raw unreformulated query text (docs/ORCHESTRATOR_DEVELOPMENT_DOCUMENT.md
    Stage 3) - the parameter name is unchanged for compatibility with the
    still-live legacy caller in chat.py.

    chapter added 2026-08-30, in place of `topic` from the original design:
    `topic` was found to have no real grounding data available to Stage 2
    (only chapter-level summaries exist) and no downstream consumer at all
    (the student dashboard's topic-level insight is a separate, existing
    system that clusters real query history after the fact, not this
    field) - `chapter` is the field that actually grounds reliably, the
    same mechanism that makes `subject` reliable.
    """
    import os
    normalized = normalize_query_string(raw_query)
    if not normalized:
        return None

    class_str = str(class_name).strip()
    subj_str = str(subject or "").strip().lower()
    board_str = str(board or "").strip().lower()
    language_str = str(language or "").strip().lower()
    chapter_str = str(chapter or "").strip().lower()
    clean_class = "".join(c for c in class_str if c.isdigit()) or "unknown"

    logger.info(
        f"[CACHE] Checking global cache: normalized='{normalized}', class='{class_str}', "
        f"subject='{subj_str}', board='{board_str}', language='{language_str}', chapter='{chapter_str}'"
    )
    try:
        # If subject is specific, search inside that subject's query_cache
        if subj_str and subj_str not in ["all", "none", "choose your subject..."]:
            query_ref = db.collection("classes").document(clean_class)\
                          .collection("subjects").document(subj_str)\
                          .collection("query_cache")\
                          .where("normalized_query", "==", normalized)\
                          .where("class", "==", class_str)
        else:
            # Fallback to collection group across all query_cache subcollections
            query_ref = db.collection_group("query_cache")\
                          .where("normalized_query", "==", normalized)\
                          .where("class", "==", class_str)

        if board_str:
            query_ref = query_ref.where("board", "==", board_str)
        if chapter_str:
            query_ref = query_ref.where("chapter", "==", chapter_str)
        if language_str:
            query_ref = query_ref.where("language", "==", language_str)

        docs = query_ref.limit(1).get()

        cache_doc = docs[0] if docs else None

        if cache_doc is None:
            # Exact-text miss - try the semantic fallback (SS6.7) before
            # giving up entirely.
            logger.info("[CACHE] Exact-text miss, trying semantic cache lookup...")
            try:
                from backend.app.services.retrieval import qdrant_service
                doc_id = qdrant_service.find_semantic_cache_match(raw_query, class_str, subj_str, board_str, language_str, chapter_str)
            except Exception as sem_err:
                logger.warning(f"[CACHE] Semantic cache lookup failed, treating as miss: {sem_err}")
                doc_id = None

            if not doc_id:
                logger.info("[CACHE] Global cache miss (no exact or semantic match)")
                return None

            # Re-derive the resolved subject the entry was actually filed
            # under, since a semantic match can come from the collection-group
            # fallback path where subj_str was "all"/empty.
            found_subj = subj_str if subj_str and subj_str not in ["all", "none", "choose your subject..."] else None
            if found_subj:
                doc_ref = db.collection("classes").document(clean_class)\
                             .collection("subjects").document(found_subj)\
                             .collection("query_cache").document(doc_id)
                cache_doc = doc_ref.get()
                if not cache_doc.exists:
                    cache_doc = None
            if cache_doc is None:
                # Subject unknown, or doc wasn't under that subject - scan this
                # class's cache entries for the matching doc_id. Bounded to a
                # generous page size; query_cache stays small per class/subject.
                for candidate in db.collection_group("query_cache").where("class", "==", class_str).limit(200).get():
                    if candidate.id == doc_id:
                        cache_doc = candidate
                        break
            if cache_doc is None:
                logger.info("[CACHE] Semantic match found but underlying document is gone - treating as miss")
                return None
            logger.info(f"[CACHE] Semantic cache hit for query: '{raw_query}' -> doc_id={doc_id}")

        cached_data = cache_doc.to_dict()

        # TTL check: a cache entry has no expiry by default, so a wrong
        # classification/answer from a since-fixed prompt or model would
        # otherwise be replayed to every student asking that exact phrasing
        # forever (this bit us for a GENERAL_KNOWLEDGE misclassification
        # that stayed cached after the orchestrator prompt was corrected).
        # Expiring entries after a week keeps the caching benefit for
        # genuinely repeated questions while letting fixes take effect on a
        # reasonable timescale instead of requiring manual cache deletion.
        created_at_str = cached_data.get("created_at")
        if created_at_str:
            try:
                created_at = datetime.fromisoformat(created_at_str)
                if datetime.now() - created_at > timedelta(days=7):
                    logger.info(f"[CACHE] Cached record for '{raw_query}' is older than 7 days. Treating as cache miss.")
                    return None
            except (ValueError, TypeError):
                pass

        orchestrator_val = cached_data.get("orchestrator_output", {})
        if isinstance(orchestrator_val, str):
            import json
            try:
                cached_data["orchestrator_output"] = json.loads(orchestrator_val)
            except Exception as parse_err:
                logger.error(f"[CACHE] Error parsing cached JSON: {parse_err}")
                return None

        video_scenes_val = cached_data.get("video_scenes")
        if isinstance(video_scenes_val, str):
            import json
            try:
                cached_data["video_scenes"] = json.loads(video_scenes_val) if video_scenes_val else None
            except Exception as parse_err:
                logger.error(f"[CACHE] Error parsing cached video_scenes JSON: {parse_err}")
                cached_data["video_scenes"] = None

        out = cached_data.get("orchestrator_output", {})

        # Verify the cached answer has real content: either narration text
        # (QUICK_ANSWER) or scene scripts (VIDEO_REQUIRED - text_narration is
        # no longer generated/used for that path, see save_to_global_query_cache).
        has_content = out.get("text_narration") or cached_data.get("video_scenes")
        if not out or not has_content:
            logger.warning(f"[CACHE] Cached record for '{raw_query}' is incomplete. Treating as cache miss.")
            return None

        # If it was a video, verify local files still exist
        if out.get("format_decision") == "VIDEO_REQUIRED":
            interactive_url = cached_data.get("interactive_url", "")
            if not interactive_url:
                logger.info("[CACHE] Cached lesson is video-required but lacks interactive_url. Treating as miss.")
                return None
            
            # Extract lesson_id from path
            parts = interactive_url.split("/")
            if len(parts) >= 3:
                lesson_id = parts[-2]
                
                # Check standard paths
                MAIN_DIR = os.path.dirname(os.path.abspath(__file__))
                PROJECT_ROOT = os.path.abspath(os.path.join(MAIN_DIR, "..", "..", ".."))
                expected_index = os.path.join(PROJECT_ROOT, "uploads", "visual_lessons", lesson_id, "index.html")
                fallback_index = os.path.join(PROJECT_ROOT, "hyperframes_engine", "outputs", lesson_id, "index.html")
                
                if not (os.path.exists(expected_index) or os.path.exists(fallback_index)):
                    logger.warning(f"[CACHE] Video files missing on disk for lesson_id {lesson_id}. Forcing cache miss.")
                    return None

        logger.info(f"[CACHE] Global cache hit! Reusing payload for query: '{raw_query}'")
        return cached_data

    except Exception as e:
        logger.error(f"[CACHE] Error checking global cache: {e}")
        return None


def save_to_global_query_cache(
    raw_query: str, class_name: str, subject: str, orchestrator_output: dict,
    interactive_url: str = None, video_scenes: list = None, query_json_url: str = None,
    board: str = None, language: str = None, chapter: str = None,
):
    """
    Saves a query execution result into the nested 'query_cache' collection.

    video_scenes: the finished storyboard scenes (teacher_script + audio_url per
    scene) for VIDEO_REQUIRED answers, from generate_visual_lesson_stream()'s
    lesson_package. The orchestrator itself no longer produces a storyboard, so
    this is the only place scene/audio data for a video answer is persisted -
    without it, a cache hit on a video query would have nothing to replay.

    board/language added 2026-08-30 (Decision 5) - same key-shape fix as
    check_global_query_cache above, same reasoning.
    """
    import json
    if not orchestrator_output or not (orchestrator_output.get("text_narration") or video_scenes):
        logger.warning("[CACHE] Rejecting save_to_global_query_cache because orchestrator_output is incomplete.")
        return
    normalized = normalize_query_string(raw_query)
    if not normalized:
        return

    class_str = str(class_name).strip()
    subj_str = str(subject or "").strip().lower()
    board_str = str(board or "").strip().lower()
    language_str = str(language or "").strip().lower()
    chapter_str = str(chapter or "").strip().lower()

    # Resolve subject from orchestrator output if generic "all"
    if not subj_str or subj_str in ["all", "none", "choose your subject..."]:
        subj_str = str(orchestrator_output.get("matched_subject") or "general knowledge").strip().lower()

    payload = {
        "raw_query": raw_query,
        "normalized_query": normalized,
        "class": class_str,
        "subject": subj_str,
        "board": board_str,
        "language": language_str,
        "chapter": chapter_str,
        # Store as string to prevent Firestore map field nesting limits / invalid keys
        "orchestrator_output": json.dumps(orchestrator_output),
        "interactive_url": interactive_url,
        "video_scenes": json.dumps(video_scenes) if video_scenes else None,
        # Per-query debug record (docs/RAG_INTEGRATION_PLAN.md §9.2.D): carried
        # forward so a cache-hit turn still resolves to the ORIGINATING turn's
        # full debug JSON (retrieved chunks, confidence, context sent to the
        # model) instead of leaving the debug trail empty just because no new
        # retrieval ran for this particular hit.
        "query_json_url": query_json_url,
        "created_at": datetime.now().isoformat()
    }

    try:
        # Create a deterministic document ID to prevent duplicate listings
        doc_id = f"{class_str}_{subj_str}_{normalized}"
        if len(doc_id) > 500:
            import hashlib
            doc_id = f"{class_str}_{subj_str}_" + hashlib.md5(normalized.encode()).hexdigest()

        clean_class = "".join(c for c in class_str if c.isdigit()) or "unknown"
        doc_ref = db.collection("classes").document(clean_class)\
                    .collection("subjects").document(subj_str)\
                    .collection("query_cache").document(doc_id)
        doc_ref.set(payload)
        logger.info(f"[CACHE] Successfully registered query in global cache: classes/{clean_class}/subjects/{subj_str}/query_cache/{doc_id}")

        # SS6.7: index this entry semantically so a later, differently-worded
        # question with the same intent can still find it. Best-effort - a
        # failure here must not fail the cache write itself.
        try:
            from backend.app.services.retrieval import qdrant_service
            qdrant_service.index_global_cache_entry(doc_id, raw_query, class_str, subj_str, board_str, language_str, chapter_str)
        except Exception as index_err:
            logger.warning(f"[CACHE] Failed to semantically index cache entry {doc_id}: {index_err}")
    except Exception as e:
        logger.error(f"[CACHE] Failed to write cache record: {e}")