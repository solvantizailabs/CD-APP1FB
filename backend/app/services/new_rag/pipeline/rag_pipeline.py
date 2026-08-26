"""
End-to-end ingestion pipeline, orchestrating Stage 1 -> Stage 2 -> chunking
-> table/diagram extraction -> embedding/storage. See
docs/RAG_REDESIGN_PLAN.md.

STALE-COMMENT CORRECTION (2026-08-25): this module's docstring used to say
it was "additive/parallel" to production and "nothing here is imported by
... the live app" - that was true when this pipeline was first built as a
standalone tool, but is no longer accurate. Since the RAG process swap (see
docs/RAG_INTEGRATION_PLAN.md), backend/app/api/routes/books.py imports and
calls ingest_book() (below) directly for every real book upload - this IS
the live production ingestion path, not a parallel/test one. It writes to
the real textbooks_v3 Qdrant collection (what chat.py's retrieval actually
reads, via new_rag_adapter.py) and to local disk under
backend/app/services/new_rag/outputs/ (local disk is a durability
convenience/inspection copy only - Supabase Storage, via
supabase_artifacts.py, is the durable copy the live app can actually rely
on surviving a redeploy; see local_artifacts.py's own corrected docstring).

The standalone/test-only tooling that still exists in this package is
cli.py and answer_test.py specifically (both explicitly documented as
such, genuinely never imported by books.py/chat.py) - not this file.
"""
import logging
import os
import uuid
from typing import Dict, List, Optional

from pypdf import PdfReader

from backend.app.services.new_rag.ingestion.pdf_parser import (
    extract_raw_pages, extract_diagram_images, extract_vector_diagram_regions, caption_diagram_image,
    detect_table_candidates, flag_boilerplate_images,
)
from backend.app.services.new_rag.ingestion.structure_parser import (
    resolve_page_sequence, validate_page_sequence, call_llm_for_topics, resolve_topic_boundaries,
    attach_reading_order_warnings,
)
from backend.app.services.new_rag.ingestion.chunker import (
    build_parent_chunks, build_child_chunks, PARENT_SOFT_CEILING_TOKENS, metadata_fields,
)
from backend.app.services.new_rag.embeddings.embedding_service import get_openai_client
from backend.app.services.new_rag.indexing.qdrant_indexer import get_qdrant_client, upsert_chunks
from backend.app.services.new_rag import local_artifacts, supabase_artifacts
from backend.app.services.llm.openai_client import create_client

logger = logging.getLogger(__name__)

# Front/back-matter exclusion, checked before running the expensive Stage 2
# LLM call at all. Confirmed live during implementation this needs to be
# stronger than the existing production check in
# books.py::pre_analyze_books (which requires the word "chapter" to be
# ABSENT from the sample) - a real NCERT answers-appendix file
# (jesc1an.pdf) opens with "Answers\nChapter 1\n1. (i) 2. (d)..." and
# literally contains "Chapter" repeatedly as its own per-chapter answer-key
# headers, so that check would not have excluded it either. Without this,
# the pipeline previously treated the answer key as if it were a real
# chapter (LLM read "Chapter 1" as a plausible chapter_title and invented
# 14 fake "topics" out of answer-key text) - a false-positive "success"
# that's worse than a caught failure, since nothing flagged it for review.
_NON_CHAPTER_MARKERS = ["answers", "answer key", "acknowledgements", "table of contents",
                         "preface", "foreword"]


def _looks_like_non_chapter_content(first_page_text: str) -> bool:
    # Whitespace stripped before matching - confirmed live that some NCERT
    # PDFs' text extraction inserts spurious spaces mid-word (e.g. "Answers"
    # extracts as "Answ er s"), which a plain substring check misses.
    sample = (first_page_text or "")[:200].lower().replace(" ", "").replace("\n", "")
    markers_no_space = [m.replace(" ", "") for m in _NON_CHAPTER_MARKERS]
    return any(marker in sample for marker in markers_no_space)


def _book_uuid_for(class_name: str, subject: str) -> str:
    """
    Default book_uuid when no explicit override is passed (only the CLI ever
    hits this path now - books.py always passes its own book_uuid, see
    ingest_book()'s book_uuid parameter). Deliberately matches
    books.py::batch_ingest_books()'s own formula EXACTLY
    (uuid5(NAMESPACE_DNS, f"{class_name}_{subject}")) - this used to include
    an extra "_newrag" suffix, which was the confirmed root cause of a real
    production gap (2026-08-21): a book ingested via the standalone CLI
    landed under a UUID the live app's Firestore-driven book resolution
    could never discover, even though the actual chunk data was correct and
    sitting in the same textbooks_v3 collection the whole time. Matching the
    live app's formula here means a CLI test-ingestion and a live-app
    ingestion of the same (class, subject) now agree on identity by
    default - no separate discovery/remap step needed for future books.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{class_name}_{subject}".lower()))


def _find_enclosing_topic(pdf_page: int, resolved_pages: List[Dict], parents: List[Dict]) -> Optional[Dict]:
    page = next((p for p in resolved_pages if p["pdf_page"] == pdf_page), None)
    if not page or page.get("textbook_page") is None:
        return None
    tb_page = page["textbook_page"]
    for parent in parents:
        if parent["start_page"] <= tb_page <= parent["end_page"]:
            return parent
    return None


def _mirror_to_supabase(local_path: str) -> Optional[str]:
    """
    Uploads an already-saved local artifact to Supabase Storage at the
    identical relative path under the book-processing bucket, mirroring the
    full local outputs/ hierarchy (integration plan §5.1: "does it make a
    difference to the application? No - retrieval only ever reads
    parents_lookup.json back. This is a durability add-on: without it, a
    Render redeploy silently deletes raw pages/manifests/diagrams for every
    book ingested since the last deploy.") Fail-open: upload_binary() never
    raises, so this can be called unconditionally after every local save
    without extra error handling at each call site.

    Returns the Supabase public URL on success, None on failure - most
    callers still ignore this (mirroring is fire-and-forget for them), but
    diagram-image callers need the real URL to store in a chunk's
    structured_content, since a local disk path isn't fetchable by anything
    downstream (a different process, a redeploy, or a future generation-time
    image fetch).
    """
    dest = os.path.relpath(local_path, local_artifacts.OUTPUT_ROOT).replace(os.sep, "/")
    return supabase_artifacts.upload_binary(local_path, dest)


def ingest_book(pdf_path: str, class_name: str, subject: str, chapter_name: Optional[str] = None,
                 start_pdf_page: int = 1, end_pdf_page: Optional[int] = None,
                 model_name: str = "gpt-4o-mini", book_uuid: Optional[str] = None) -> Dict:
    """
    Ingests one chapter (a pdf_page range within a PDF, defaulting to the
    whole PDF) through the full new pipeline. Returns a report dict
    summarizing what happened at every stage - this is what the CLI
    prints/writes so a failed stage is immediately visible, not silent.

    `chapter_name` is optional and only meant as an override - the LLM
    reads the chapter's own title directly from its content during Stage 2
    (same call as topic detection, no extra cost) and that is normally the
    authoritative chapter name. Nothing about naming a chapter needs a human
    to type it; a human override is only useful when the printed title is
    itself unhelpful or you want a different label for your own reference.

    `book_uuid` is optional and only meant as an override - by default this
    function derives its own deterministic UUID from (class_name, subject)
    for standalone/CLI use. Production callers (backend/app/api/routes/
    books.py) pass their own already-computed book_uuid explicitly here so
    both the old textbooks_v2 and new textbooks_v3 collections agree on the
    same book_uuid value for the same book, per the integration plan.
    """
    book_uuid = book_uuid or _book_uuid_for(class_name, subject)
    chapter_id = str(uuid.uuid4())
    source_stem = os.path.splitext(os.path.basename(pdf_path))[0]
    dir_path = local_artifacts.chapter_dir(class_name, subject, source_stem)
    report = {
        "book_uuid": book_uuid, "chapter_id": chapter_id, "chapter_name": chapter_name,
        "pdf_path": pdf_path, "source_stem": source_stem, "output_dir": dir_path, "stages": {},
        "parent_chunks": [],
    }

    def _finish(status: str, message: str) -> Dict:
        report["status"] = status
        report["message"] = message
        status_path = local_artifacts.save_json(dir_path, "00_status.json", report)
        _mirror_to_supabase(status_path)
        index_path = local_artifacts.update_book_index(class_name, subject, source_stem, {
            "chapter_name": report.get("chapter_name"), "status": status,
            "message": message, "folder": os.path.basename(dir_path),
        })
        _mirror_to_supabase(index_path)
        return report

    reader = PdfReader(pdf_path)
    total_pages = len(reader.pages)
    end_pdf_page = end_pdf_page or total_pages

    # --- Stage 1: deterministic page extraction + validation ---
    all_pages = extract_raw_pages(pdf_path)
    chapter_pages = all_pages[start_pdf_page - 1:end_pdf_page]

    if chapter_pages and _looks_like_non_chapter_content(chapter_pages[0]["text"]):
        return _finish(
            "skipped_non_chapter",
            "This file's opening text matches a front/back-matter marker "
            "(answers key, preface, table of contents, etc.) - not treated as "
            "an error, just correctly excluded before running topic detection on it."
        )

    resolved_pages = resolve_page_sequence(chapter_pages)
    is_valid_s1, issues_s1 = validate_page_sequence(resolved_pages)
    attach_reading_order_warnings(resolved_pages)
    reading_order_warning_count = sum(1 for p in resolved_pages if p["reading_order_warnings"])
    if reading_order_warning_count:
        logger.warning(
            f"[NEW_RAG] Stage1 reading-order warnings on {reading_order_warning_count} page(s) "
            f"of {source_stem!r} - non-blocking, see 01_raw_pages.json per-page "
            f"'reading_order_warnings' for detail."
        )
    report["stages"]["stage1_page_extraction"] = {
        "is_valid": is_valid_s1, "issues": issues_s1, "page_count": len(resolved_pages),
        "reading_order_warning_page_count": reading_order_warning_count,
    }
    raw_pages_path = local_artifacts.save_json(dir_path, "01_raw_pages.json", resolved_pages)
    _mirror_to_supabase(raw_pages_path)

    if not is_valid_s1:
        return _finish("blocked_stage1", "Page-sequence validation failed - routed to manual review, ingestion stopped.")

    # --- Stage 2: LLM topic segmentation with anchor boundaries ---
    openai_client = create_client()
    valid_pages = [p for p in resolved_pages if p.get("textbook_page") is not None]
    if not valid_pages:
        return _finish("blocked_stage1", "No pages with a resolved textbook_page in this range - nothing to segment.")
    chapter_end_page = max(p["textbook_page"] for p in valid_pages)

    # Bounded retry (2026-08-21, found via real pilot testing - "AREAS RELATED
    # TO CIRCLES" and "SURFACE AREAS AND VOLUMES" both hit blocked_stage2 on
    # first attempt in a real 17-chapter batch run): the topic-detection LLM
    # call is not perfectly deterministic even at temperature 0 (confirmed
    # elsewhere in this pipeline's own testing notes - topic COUNT varies
    # run to run), so a manifest that fails the coverage/anchor validation
    # gate on one attempt can genuinely pass on a fresh attempt with no other
    # change. One retry, same as the bounded-retry pattern already used at
    # retrieval time (hybrid_retriever.py) - never silently loop forever,
    # and a second consecutive failure is still routed to manual review, not
    # papered over.
    MAX_STAGE2_ATTEMPTS = 2
    llm_result = None
    topic_result = None
    for attempt in range(1, MAX_STAGE2_ATTEMPTS + 1):
        try:
            llm_result = call_llm_for_topics(openai_client, model_name, resolved_pages)
        except Exception as e:
            # Previously uncaught: an LLM failure here (timeout, context-length
            # overflow, exhausted retries on a 429) propagated straight out of
            # ingest_book and, in a folder upload, killed every chapter after
            # it too (see cli.py's per-chapter try/except, added for the same
            # reason). Converting it to a normal blocked status keeps this
            # chapter's failure isolated and visible in chapter_info.json
            # instead of a raw traceback.
            logger.error(f"[NEW_RAG] Stage2 topic-detection LLM call failed for {source_stem!r} (attempt {attempt}/{MAX_STAGE2_ATTEMPTS}): {e}")
            return _finish("failed_stage2_llm_error", f"Topic-detection LLM call failed: {e}")

        topic_result = resolve_topic_boundaries(resolved_pages, llm_result["topics"], chapter_end_page)
        if topic_result["is_valid"]:
            break
        if attempt < MAX_STAGE2_ATTEMPTS:
            logger.warning(
                f"[NEW_RAG] Stage2 topic-manifest validation failed for {source_stem!r} "
                f"(attempt {attempt}/{MAX_STAGE2_ATTEMPTS}) - retrying once before blocking."
            )

    report["stages"]["stage2_topic_detection"] = topic_result
    manifest_path = local_artifacts.save_json(dir_path, "02_topics_manifest.json", topic_result)
    _mirror_to_supabase(manifest_path)

    if not topic_result["is_valid"]:
        return _finish(
            "blocked_stage2",
            f"Topic-manifest validation failed after {MAX_STAGE2_ATTEMPTS} attempts - "
            f"routed to manual review, ingestion stopped."
        )

    # Chapter name resolution: an explicit override always wins; otherwise
    # use the title the LLM read directly from the chapter's own content;
    # only fall back to a generic placeholder if even that came back empty
    # (e.g. a malformed/non-chapter file) - never silently invent a name.
    detected_title = llm_result.get("chapter_title")
    chapter_name = chapter_name or detected_title or f"Untitled ({source_stem})"
    report["chapter_name"] = chapter_name
    report["detected_chapter_title"] = detected_title

    overview_path = local_artifacts.save_chapter_markdown(dir_path, chapter_name, resolved_pages, topic_result["topics"])
    _mirror_to_supabase(overview_path)

    # Chunking, diagram extraction/captioning, and table detection are each
    # individually resilient (see ingestion/pdf_parser.py), but the block as
    # a whole was not - confirmed live that a chapter died somewhere in here
    # with no chunks.json ever written, killing the entire book upload since
    # nothing caught it (see cli.py's per-chapter isolation, added for the
    # same reason). Wrapping the whole stage is a backstop for anything
    # unexpected, matching the same "blocked, not crashed" treatment as
    # Stage 2/Stage 6 below.
    try:
        # --- Chunking: parent (topic) + child construction ---
        parents = build_parent_chunks(resolved_pages, topic_result["topics"], chapter_id, chapter_name)
        oversized = [p for p in parents if p["token_count"] > PARENT_SOFT_CEILING_TOKENS]
        all_embeddable_chunks: List[Dict] = []
        for parent in parents:
            all_embeddable_chunks.extend(build_child_chunks(parent))

        # --- Stage 5: diagram extraction, then captioned WITH its enclosing
        # topic's context (chapter/topic name), resolved before captioning
        # rather than after - a caption generated in isolation from pixels
        # alone can't ground itself in what THIS chapter's diagram is
        # actually illustrating; see prompts/diagram_caption.txt's
        # {context_block}.
        openai_raw_client = get_openai_client()
        raw_diagrams = extract_diagram_images(pdf_path, start_pdf_page, end_pdf_page)
        # Vector-drawn diagrams (factor trees, geometric figures - built from
        # PDF drawing operators, not an embedded image object) are structurally
        # invisible to the raster extraction above - confirmed live against
        # jemh101.pdf (Class 10 Maths). extract_vector_diagram_regions() finds
        # them via the page's actual vector primitives and returns them in the
        # exact same dict shape as raster results, so they flow through the
        # SAME boilerplate/is_content/captioning pipeline below unchanged -
        # verified live that the existing is_content classifier alone
        # correctly separates the real diagrams this finds (a factor tree, a
        # formula box) from the false positives its cruder "enough vector
        # shapes clustered together" heuristic inevitably also picks up (a
        # chapter title banner, stray boxed-formula text fragments) - no new
        # downstream logic needed.
        vector_diagrams = extract_vector_diagram_regions(pdf_path, start_pdf_page, end_pdf_page)
        raw_diagrams = raw_diagrams + vector_diagrams
        raw_diagrams = flag_boilerplate_images(raw_diagrams, total_pages=len(valid_pages))
        boilerplate_count = sum(1 for d in raw_diagrams if d["is_boilerplate"])
        if boilerplate_count:
            logger.info(
                f"[NEW_RAG][Stage5] Excluded {boilerplate_count}/{len(raw_diagrams)} extracted "
                f"images as recurring page-furniture (watermark/margin icon) for {source_stem!r} - "
                f"never captioned, never chunked."
            )

        # Caches, keyed by image_hash, so a real illustration that's
        # deliberately reused more than once in the same chapter (below the
        # boilerplate threshold - see flag_boilerplate_images) gets ONE
        # caption call and ONE saved/uploaded file, reused for every
        # occurrence, rather than a fresh (and potentially inconsistent)
        # caption + duplicate upload per repeat.
        caption_cache: Dict[str, str] = {}
        image_url_cache: Dict[str, str] = {}
        diagram_chunks = []
        # chunk_id -> raw image bytes, kept only for Stage 2's image-vector
        # embedding right below (CLIP needs pixels, not the caption) - not
        # persisted anywhere, thrown away once this chapter's ingest_book()
        # call returns.
        diagram_image_bytes: Dict[str, bytes] = {}
        non_content_count = 0
        for d in raw_diagrams:
            if d["is_boilerplate"]:
                continue

            enclosing = _find_enclosing_topic(d["pdf_page"], resolved_pages, parents)
            image_hash = d["image_hash"]

            if image_hash in caption_cache:
                caption_result = caption_cache[image_hash]
            else:
                caption_result = caption_diagram_image(
                    openai_raw_client, d["image_bytes"], d["image_format"],
                    chapter_name=chapter_name,
                    topic_name=enclosing["topic_name"] if enclosing else None,
                )
                caption_cache[image_hash] = caption_result
            caption = caption_result["caption"]
            if not caption:
                continue
            # Second, independent filter alongside is_boilerplate - catches
            # page furniture the hash-based filter structurally can't (a
            # one-off title banner or QR code that only ever appears once
            # in the chapter, so it never had a chance to look "recurring").
            # See caption_diagram_image()'s docstring for why this stays
            # separate from the boilerplate check rather than replacing it.
            if not caption_result["is_content"]:
                non_content_count += 1
                continue

            if image_hash in image_url_cache:
                image_location = image_url_cache[image_hash]
            else:
                image_rel_path = f"05_diagrams/images/p{d['pdf_page']}_{d['image_name']}"
                image_local_path = local_artifacts.save_binary(dir_path, image_rel_path, d["image_bytes"])
                # Prefer the real, fetchable Supabase URL - a local relative
                # path isn't reachable by anything downstream (a different
                # process, a Render redeploy, or a future generation-time
                # image fetch). Fall back to the local path only if the
                # Supabase upload itself failed, so structured_content is
                # never silently None.
                image_location = _mirror_to_supabase(image_local_path) or image_rel_path
                image_url_cache[image_hash] = image_location

            new_chunk_id = str(uuid.uuid4())
            diagram_image_bytes[new_chunk_id] = d["image_bytes"]
            diagram_chunks.append({
                "chunk_id": new_chunk_id,
                "parent_chunk_id": enclosing["parent_chunk_id"] if enclosing else None,
                "chapter_id": chapter_id,
                "chapter_name": chapter_name,
                "topic_id": enclosing["topic_id"] if enclosing else None,
                "topic_name": enclosing["topic_name"] if enclosing else None,
                "chunk_type": "diagram",
                "text": caption,
                "structured_content": image_location,
                "token_count": len(caption.split()),
                "start_page": d["pdf_page"],
                "end_page": d["pdf_page"],
                **metadata_fields(chapter_name, enclosing["topic_name"] if enclosing else None,
                                   "diagram", d["pdf_page"]),
            })

        if non_content_count:
            logger.info(
                f"[NEW_RAG][Stage5] Excluded {non_content_count} extracted image(s) as "
                f"page furniture (title banner/QR code/decorative icon, per the caption "
                f"model's own is_content classification) for {source_stem!r} - captioned "
                f"but never chunked."
            )

        table_chunks = []
        for page in resolved_pages:
            candidates = detect_table_candidates(page["text"])
            for block in candidates:
                enclosing = _find_enclosing_topic(page["pdf_page"], resolved_pages, parents)
                table_chunks.append({
                    "chunk_id": str(uuid.uuid4()),
                    "parent_chunk_id": enclosing["parent_chunk_id"] if enclosing else None,
                    "chapter_id": chapter_id,
                    "chapter_name": chapter_name,
                    "topic_id": enclosing["topic_id"] if enclosing else None,
                    "topic_name": enclosing["topic_name"] if enclosing else None,
                    "chunk_type": "table",
                    # No dedicated table-to-description model implemented yet
                    # (see ingestion/pdf_parser.py docstring) - the raw
                    # detected block is used for both fields as an honest
                    # placeholder, not a claim of real structured extraction.
                    "text": block,
                    "structured_content": block,
                    "token_count": len(block.split()),
                    "start_page": page.get("textbook_page"),
                    "end_page": page.get("textbook_page"),
                    **metadata_fields(chapter_name, enclosing["topic_name"] if enclosing else None,
                                       "table", page.get("textbook_page")),
                })

        # Parent/child text chunks, diagrams, and tables each get their own
        # clearly-separated file/folder - user feedback after real testing
        # was that a single combined chunks.json (parents + every embeddable
        # chunk type mixed together) made it hard to tell at a glance which
        # content came from which stage. Every filename below is prefixed
        # 04/05/06 to match pipeline order (see local_artifacts.py docstring).
        parent_chunks_path = local_artifacts.save_json(dir_path, "04_chunks/parent_chunks.json", parents)
        child_chunks_path = local_artifacts.save_json(dir_path, "04_chunks/child_chunks.json", all_embeddable_chunks)
        captions_path = local_artifacts.save_json(dir_path, "05_diagrams/captions.json", diagram_chunks)
        tables_path = local_artifacts.save_json(dir_path, "06_tables.json", table_chunks)
        for saved_path in (parent_chunks_path, child_chunks_path, captions_path, tables_path):
            _mirror_to_supabase(saved_path)

        report["parent_chunks"] = parents

        all_embeddable_chunks.extend(diagram_chunks)
        all_embeddable_chunks.extend(table_chunks)

        report["stages"]["chunking"] = {
            "parent_count": len(parents),
            "child_count": len(all_embeddable_chunks) - len(diagram_chunks) - len(table_chunks),
            "diagram_count": len(diagram_chunks),
            "table_candidate_count": len(table_chunks),
            "oversized_parents": [{"topic_name": p["topic_name"], "token_count": p["token_count"]} for p in oversized],
        }

        # parents_lookup.json lives at the BOOK level (not per-chapter) since
        # retrieval's parent-escalation lookup needs to find a parent chunk by ID
        # without knowing in advance which chapter it came from.
        book_dir = local_artifacts.book_dir(class_name, subject)
        book_key = os.path.basename(book_dir)  # reuses local_artifacts' own sanitized "Class10_science" naming

        # Accumulate onto the EXISTING cumulative state - Supabase first, not
        # local disk. Confirmed live this matters: if local disk were ever
        # deleted/wiped (it's not the durable source anymore, see below) and
        # this still read from local first, re-ingesting even one chapter
        # would start the merge from empty and overwrite Supabase's full
        # multi-chapter file with just that one chapter's parents - silent
        # data loss for every other already-ingested chapter of this book.
        # Reading Supabase first (falling back to local only if Supabase has
        # nothing yet, e.g. the very first chapter of a brand new book)
        # makes this correct regardless of local disk state.
        parents_lookup = supabase_artifacts.download_json(f"{book_key}/parents_lookup.json")
        if parents_lookup is None:
            parents_lookup = local_artifacts.load_json(book_dir, "parents_lookup.json") or {}
        for p in parents:
            parents_lookup[p["parent_chunk_id"]] = p
        local_artifacts.save_json(book_dir, "parents_lookup.json", parents_lookup)

        # Production-durable copy, per the locked design (docs/RAG_REDESIGN_PLAN.md
        # section 7) - local disk alone does not survive a Render redeploy
        # (confirmed, git commit e1bc145), so parent-escalation lookups at
        # retrieval time need a copy that outlives this process. Uploaded on
        # every chapter (not just once) since parents_lookup is a growing,
        # book-level dict - each chapter's ingestion adds its own parents to
        # the same file, and the Supabase copy needs to reflect that too, not
        # just whatever existed when the book was first touched.
        supabase_artifacts.upload_json(parents_lookup, f"{book_key}/parents_lookup.json")
    except Exception as e:
        logger.error(f"[NEW_RAG] Chunking/diagram/table stage failed for {source_stem!r}: {e}")
        return _finish("failed_chunking_error", f"Chunking/diagram/table extraction failed: {e}")

    # --- Stage 6: embed + store ---
    client = get_qdrant_client()
    try:
        upserted = upsert_chunks(client, openai_raw_client, all_embeddable_chunks, book_uuid, class_name, subject,
                                  document_name=source_stem)
    except Exception as e:
        # Same rationale as the Stage2 try/except above - this used to be
        # the single riskiest unprotected call in the pipeline (whole
        # chapter's worth of chunks embedded in one request, no retry), and
        # a failure here threw away all of Stage 1-5's completed work for
        # this chapter with no chance to isolate or retry per-chapter.
        logger.error(f"[NEW_RAG] Stage6 embedding/upsert failed for {source_stem!r}: {e}")
        return _finish("failed_stage6_embedding_error", f"Embedding/upsert failed: {e}")

    # --- Stage 7: image-vector embedding (docs/IMAGE_PIPELINE_PLAN.md
    # section 3) - a separate collection from textbooks_v3, purely additive
    # to what Stage 6 already did. Deliberately non-fatal: this chapter's
    # text/caption retrieval (the core of the app) already fully succeeded
    # above, so a failure here (e.g. the local CLIP model failing to load)
    # must not turn an otherwise-successful ingestion into a failure - it
    # only means this one chapter has no image-vector search until a retry,
    # same "degrade, don't fail" contract as the other per-stage guards in
    # this function.
    image_vectors_upserted = 0
    if diagram_chunks:
        try:
            import io
            from PIL import Image
            from backend.app.services.new_rag.embeddings.image_embedding_service import embed_images
            from backend.app.services.new_rag.indexing.image_indexer import upsert_diagram_images

            pil_images = [Image.open(io.BytesIO(diagram_image_bytes[c["chunk_id"]])).convert("RGB")
                          for c in diagram_chunks]
            image_vectors = embed_images(pil_images)
            image_vectors_upserted = upsert_diagram_images(
                client, diagram_chunks, image_vectors, book_uuid, class_name, subject,
            )
        except Exception as e:
            logger.error(f"[NEW_RAG] Stage7 image-vector embedding failed for {source_stem!r} "
                         f"(non-fatal - text/caption retrieval is unaffected): {e}")

    report["stages"]["image_embedding"] = {"vectors_upserted": image_vectors_upserted}

    return _finish("ingested", (
        f"Ingested {upserted} chunks ({len(parents)} topics, "
        f"{len(diagram_chunks)} diagrams, {len(table_chunks)} table candidates) "
        f"into Qdrant collection 'textbooks_v3'."
    ))
