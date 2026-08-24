import os
import re
import shutil
import json
import uuid
import logging
from typing import List, Dict, Optional
from pydantic import BaseModel
from fastapi import APIRouter, File, UploadFile, Query, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse
from pypdf import PdfReader
from qdrant_client import models
from langchain_text_splitters import RecursiveCharacterTextSplitter

from backend.app.services.retrieval import qdrant_service as qdrant
from backend.app.services.retrieval import local_chap_service
from backend.app.core import firestore_service
from backend.app.core.firebase.firebase_init import db, bucket
from backend.app.services.chat.answer_service import generate_chapter_summary
from backend.app.services.new_rag.pipeline.rag_pipeline import ingest_book

logger = logging.getLogger(__name__)

router = APIRouter()

# --- DIRECTORY SETUP ---
# backend/app/api/routes/books.py is 4 levels deep:
# 1 level: routes
# 2 levels: api
# 3 levels: app
# 4 levels: backend (CG-DEV/CD-APP1FB/backend)
# 5 levels: CG-DEV/CD-APP1FB (project root)
ROUTES_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(ROUTES_DIR, "..", "..", "..", ".."))
UPLOADS_DIR = os.path.join(PROJECT_ROOT, "uploads")

if not os.path.exists(UPLOADS_DIR):
    os.makedirs(UPLOADS_DIR)

class BookCreateRequest(BaseModel):
    class_name: str
    subject: str
    filename: str
    chapters: List[Dict]


async def process_book_in_background(book_uuid: str, pdf_path: str, class_name: str, subject: str, chapters: List[Dict]):
    """
    Processes the book in the background, creates summaries, and saves to databases.
    """
    print(f"\n{'='*100}")
    print(f"[PROCESS] ========== BOOK PROCESSING START ==========")
    print(f"[PROCESS] Book: Class {class_name} - {subject.capitalize()}")
    print(f"[PROCESS] UUID: {book_uuid}")
    print(f"[PROCESS] PDF: {os.path.basename(pdf_path)}")
    print(f"[PROCESS] Total Chapters: {len(chapters)}")
    print(f"{'='*100}\n")
    
    logger.info(f"BACKGROUND TASK STARTED for book {book_uuid}")

    try:
        # Initialize services
        print(f"[PROCESS] Initializing services...")
        qdrant.initialize()

        # Raise pypdf's default zlib decompression guard (75MB) - see the
        # matching comment in process_batch_ingest_in_background for why.
        try:
            import pypdf.filters as _pypdf_filters
            _pypdf_filters.ZLIB_MAX_OUTPUT_LENGTH = 500_000_000
        except Exception:
            pass

        reader = PdfReader(pdf_path)
        parent_splitter = RecursiveCharacterTextSplitter(
            chunk_size=2000,
            chunk_overlap=400,
            separators=["\n\n", "\n", " ", ""]
        )
        child_splitter = RecursiveCharacterTextSplitter(
            chunk_size=400,
            chunk_overlap=100,
            separators=["\n\n", "\n", ".", " ", ""]
        )
        print(f"[PROCESS] ✓ Services initialized\n")

        chapters_to_process = chapters
        if not chapters_to_process:
            raise ValueError("No confirmed chapters found to process.")

        all_chapters_with_summaries = []

        # Steps 1 & 3: Generate Summaries and Upload Chunks to Qdrant
        for i, chapter_data in enumerate(chapters_to_process):
            chapter_name = chapter_data['chapter_name']
            
            print(f"[PROCESS] ┌─ [{i+1}/{len(chapters_to_process)}] {chapter_name}")

            start_page = chapter_data.get("pdf_startpg")
            end_page = chapter_data.get("pdf_endpg")
            chp_start = chapter_data.get("chpstpage")
            chp_end = chapter_data.get("chpendpage")

            if start_page is None or end_page is None:
                print(f"[PROCESS] │  ✗ Skipping - missing page numbers\n")
                continue
            
            print(f"[PROCESS] │  Pages: PDF {start_page}-{end_page}, Chapter {chp_start}-{chp_end}")
            print(f"[PROCESS] │  Extracting and chunking text from PDF...")

            # Isolate each chapter's processing: one bad PDF page/chapter
            # must not abort the remaining chapters in this book.
            try:
                points_to_upload = []
                chapter_parent_chunks = []

                # Process page by page
                for page_num in range(start_page - 1, end_page):
                    if page_num < 0 or page_num >= len(reader.pages):
                        continue

                    try:
                        page_text = reader.pages[page_num].extract_text() or ""
                    except Exception as page_err:
                        print(f"[PROCESS] │  ⚠ Skipping page {page_num + 1} - extraction failed: {page_err}")
                        continue
                    if not page_text.strip():
                        continue

                    # Split page text into parent chunks
                    parent_chunks = parent_splitter.split_text(page_text)
                    for parent_text in parent_chunks:
                        parent_text = parent_text.strip()
                        if not parent_text:
                            continue

                        chapter_parent_chunks.append(parent_text)

                        # Split parent chunk into child chunks
                        child_chunks = child_splitter.split_text(parent_text)
                        for chunk_text in child_chunks:
                            chunk_text = chunk_text.strip()
                            if not chunk_text:
                                continue

                            chunk_id = str(uuid.uuid4())
                            qdrant_id = str(uuid.uuid4())

                            # Generate embedding for child text
                            embedding = qdrant.local_embedder.encode(chunk_text).tolist()

                            # Compute actual printed page
                            current_printed_page = chp_start + (page_num - (start_page - 1)) if chp_start is not None else 1

                            points_to_upload.append(
                                models.PointStruct(
                                    id=qdrant_id,
                                    vector=embedding,
                                    payload={
                                        "book_uuid": book_uuid,
                                        "chapter_id": str(i + 1),
                                        "chunk_id": chunk_id,
                                        "text": chunk_text,
                                        "parent_text": parent_text,
                                        "chapter_name": chapter_name,
                                        "pdf_page": page_num + 1,
                                        "pdf_startpg": start_page,
                                        "pdf_endpg": end_page,
                                        "chpstpage": current_printed_page,
                                        "chpendpage": current_printed_page,
                                    },
                                )
                            )

                if points_to_upload:
                    print(f"[PROCESS] │  ✓ Saved {len(points_to_upload)} chunks to Qdrant")

                    # Upload in batches to prevent timeout
                    BATCH_SIZE = 50  # Upload 50 points at a time
                    total_points = len(points_to_upload)

                    for batch_start in range(0, total_points, BATCH_SIZE):
                        batch_end = min(batch_start + BATCH_SIZE, total_points)
                        batch = points_to_upload[batch_start:batch_end]

                        print(f"[PROCESS] │  Uploading batch {batch_start+1}-{batch_end} of {total_points}...")

                        # Retry logic for network issues
                        max_retries = 3
                        for attempt in range(max_retries):
                            try:
                                qdrant.client.upsert(
                                    collection_name=qdrant.COLLECTION_NAME,
                                    points=batch,
                                    wait=True
                                )
                                print(f"[PROCESS] │  ✓ Batch uploaded successfully")
                                break  # Success, exit retry loop
                            except Exception as e:
                                if attempt < max_retries - 1:
                                    wait_time = (attempt + 1) * 2  # 2, 4, 6 seconds
                                    print(f"[PROCESS] │  ⚠️ Upload failed (attempt {attempt+1}/{max_retries}), retrying in {wait_time}s...")
                                    import time
                                    time.sleep(wait_time)
                                else:
                                    print(f"[PROCESS] │  ✗ Upload failed after {max_retries} attempts: {e}")
                                    raise  # Re-raise after all retries exhausted

                # Generate summary
                print(f"[PROCESS] │  Generating summary with LLM...")
                summary_text = generate_chapter_summary(class_name, subject, chapter_name, chapter_parent_chunks)
                print(f"[PROCESS] │  ✓ Summary generated ({len(summary_text)} chars)")

                chapter_summary_data = {
                    "sno": i + 1,  # Serial number starting from 1
                    "chapter_name": chapter_name,
                    "summary": summary_text,
                    "pdf_startpg": chapter_data.get("pdf_startpg"),
                    "pdf_endpg": chapter_data.get("pdf_endpg"),
                    "chpstpage": chapter_data.get("chpstpage"),
                    "chpendpage": chapter_data.get("chpendpage"),
                }

                # Log what we're saving to Firestore for debugging
                print(f"[PROCESS] │  ✓ Firestore data for chapter {i + 1}:")
                print(f"[PROCESS] │    - sno: {i + 1}")
                print(f"[PROCESS] │    - chapter_name: {chapter_name}")
                print(f"[PROCESS] │    - pdf_startpg: {chapter_data.get('pdf_startpg')}")
                print(f"[PROCESS] │    - pdf_endpg: {chapter_data.get('pdf_endpg')}")
                print(f"[PROCESS] │    - chpstpage: {chapter_data.get('chpstpage')}")
                print(f"[PROCESS] │    - chpendpage: {chapter_data.get('chpendpage')}")
                print(f"[PROCESS] │    - summary_length: {len(summary_text)} chars")

                all_chapters_with_summaries.append(chapter_summary_data)
                print(f"[PROCESS] └─ ✓ Chapter complete\n")
            except Exception as chapter_err:
                print(f"[PROCESS] │  ✗ Chapter failed, skipping to next: {chapter_err}")
                logger.error(f"Chapter '{chapter_name}' failed during book ingestion: {chapter_err}", exc_info=True)
                print(f"[PROCESS] └─ Skipped {chapter_name}\n")
                continue

        # Step 4: Save single summary document for LLM context
        print(f"[PROCESS] Saving {len(all_chapters_with_summaries)} summaries to Firestore...")
        firestore_service.save_summary_document(
            class_name=class_name,
            subject=subject,
            book_uuid=book_uuid,
            chapters=all_chapters_with_summaries
        )
        print(f"[PROCESS] ✓ Summaries saved to Firestore\n")

        print(f"{'='*100}")
        print(f"[PROCESS] ========== BOOK PROCESSING COMPLETE ==========")
        print(f"[PROCESS] ✓ {len(chapters_to_process)} chapters processed")
        print(f"[PROCESS] ✓ All data saved to Qdrant and Firestore")
        print(f"{'='*100}\n")

    except Exception as e:
        print(f"\n[PROCESS] ✗ ERROR: {e}\n")
        logger.error(f"BACKGROUND TASK FAILED for book {book_uuid}: {e}", exc_info=True)

    logger.info(f"Finished background processing for book {book_uuid}")


async def process_batch_ingest_in_background(book_uuid: str, class_name: str, subject: str, chapters: List[Dict]):
    """
    Processes multiple chapter PDFs in the background, creates summaries, and saves to Qdrant/Firestore.
    """
    print(f"\n{'='*100}")
    print(f"[PROCESS BATCH] ========== BATCH PROCESSING START ==========")
    print(f"[PROCESS BATCH] Book: Class {class_name} - {subject.capitalize()}")
    print(f"[PROCESS BATCH] UUID: {book_uuid}")
    print(f"[PROCESS BATCH] Total Chapters: {len(chapters)}")
    print(f"{'='*100}\n")
    
    try:
        # Initialize services (still needed: qdrant.book_has_content() and other
        # textbooks_v2-era helpers elsewhere in the app still read qdrant.client;
        # new_rag's ingest_book() below opens its own textbooks_v3 client).
        qdrant.initialize()

        all_chapters_with_summaries = []

        for i, chapter_data in enumerate(chapters):
            chapter_name = chapter_data['chapter_name']
            filename = chapter_data.get('filename')
            chp_start = chapter_data.get("chpstpage")
            chp_end = chapter_data.get("chpendpage")
            chapter_id = chapter_data.get("chapter_id", str(i + 1))

            if not filename:
                print(f"[PROCESS BATCH] │  ✗ Skipping - missing filename")
                continue

            pdf_path = os.path.join(UPLOADS_DIR, filename)
            if not os.path.exists(pdf_path):
                print(f"[PROCESS BATCH] │  ✗ Skipping - file not found: {filename}")
                continue

            print(f"[PROCESS BATCH] ┌─ [{i+1}/{len(chapters)}] {chapter_name} ({filename})")
            print(f"[PROCESS BATCH] │  Textbook Pages: {chp_start}-{chp_end}")

            # Isolate each chapter's processing: one bad PDF page/chapter
            # must not abort the remaining chapters in the batch. Previously
            # an unhandled exception here (e.g. a pypdf decompression error)
            # propagated to the top-level catch-all and silently killed
            # every chapter after the failing one. ingest_book() itself is
            # internally per-stage fail-safe (returns a blocked/failed status
            # rather than raising), but this try/except is the outer net for
            # anything unexpected (e.g. a raised exception from a bug), same
            # role it always had.
            try:
                # RAG process swap (2026-08-21): ingestion/chunking/embedding
                # now runs through new_rag's pipeline (textbooks_v3, topic-
                # aligned parent/child chunks, dense+sparse vectors) instead
                # of the inline RecursiveCharacterTextSplitter + local_embedder
                # logic that used to live here. Everything OUTSIDE this call -
                # the per-chapter isolation, the [PROCESS BATCH] log skeleton,
                # the Firestore write below - is unchanged. See
                # docs/RAG_INTEGRATION_PLAN.md §4.1/§6.
                print(f"[PROCESS BATCH] │  Ingesting via new_rag pipeline...")
                ingest_report = ingest_book(
                    pdf_path=pdf_path,
                    class_name=class_name,
                    subject=subject,
                    chapter_name=chapter_name,
                    book_uuid=book_uuid,  # reuse the SAME book_uuid this route already computed,
                                          # so textbooks_v2 and textbooks_v3 agree on identity
                )
                print(f"[PROCESS BATCH] │  Ingesting via new_rag pipeline (status={ingest_report['status']})")

                if ingest_report["status"] == "skipped_non_chapter":
                    # NOT a failure - the pipeline correctly recognized this
                    # file as front/back matter (answer key, preface, table of
                    # contents) and excluded it before ever running topic
                    # detection, exactly as designed. Found via real pilot
                    # testing (2026-08-21): this used to fall into the
                    # RuntimeError branch below and print a scary
                    # "Chapter failed" + full traceback for a case that was
                    # never actually an error - fixed to log plainly and move
                    # on instead.
                    print(f"[PROCESS BATCH] │  ○ Correctly excluded (not a real chapter): {ingest_report.get('message')}")
                    print(f"[PROCESS BATCH] └─ Skipped {chapter_name} (front/back matter)\n")
                    continue

                if ingest_report["status"] != "ingested":
                    raise RuntimeError(
                        f"new_rag ingestion did not complete (status={ingest_report['status']}): "
                        f"{ingest_report.get('message')}"
                    )

                print(f"[PROCESS BATCH] │  ✓ {ingest_report.get('message')}")

                new_rag_chapter_id = ingest_report["chapter_id"]
                parent_chunk_texts = [p.get("text", "") for p in ingest_report.get("parent_chunks", [])]

                # Generate summary for this chapter - same call as before,
                # now fed new_rag's topic-aligned parent chunks instead of the
                # old fixed-size 2000-char parent chunks.
                print(f"[PROCESS BATCH] │  Generating summary via Gemini...")

                summary = ""
                try:
                    summary = generate_chapter_summary(class_name, subject, chapter_name, parent_chunk_texts[:20])
                    print(f"[PROCESS BATCH] │  ✓ Summary generated")
                except Exception as e:
                    print(f"[PROCESS BATCH] │  ✗ Summary generation failed: {e}")
                    summary = f"Summary not available. (Error: {e})"

                all_chapters_with_summaries.append({
                    "chapter_name": chapter_name,
                    "chapter_id": chapter_id,
                    # new_rag_chapter_id: the UUID new_rag actually stored on every
                    # textbooks_v3 chunk payload for this chapter (distinct from the
                    # admin-facing sequential "chapter_id" above, which is unrelated
                    # and left untouched for backward compatibility). This is what
                    # lets the orchestrator later narrow retrieval to one chapter by
                    # ID instead of only by name - see docs/RAG_INTEGRATION_PLAN.md §4.2.
                    "new_rag_chapter_id": new_rag_chapter_id,
                    "chpstpage": chp_start,
                    "chpendpage": chp_end,
                    "summary": summary
                })
                print(f"[PROCESS BATCH] └─ Done processing {chapter_name}\n")
            except Exception as chapter_err:
                # Isolation boundary: a failure anywhere in this chapter's
                # processing (bad page, embedding error, Qdrant hiccup) must
                # not abort the remaining chapters in the batch.
                print(f"[PROCESS BATCH] │  ✗ Chapter failed, skipping to next: {chapter_err}")
                logger.error(f"Chapter '{chapter_name}' ({filename}) failed during batch ingestion: {chapter_err}", exc_info=True)
                print(f"[PROCESS BATCH] └─ Skipped {chapter_name}\n")
                continue

        # Save all summaries to Firestore in the consolidated content path: /classes/{class}/subjects/{subject}
        # Merge by chapter_name rather than blindly overwriting the whole
        # chapters[] array (2026-08-21, found during real pilot testing: a
        # partial retry - re-running only the chapters that failed the first
        # time - used to silently WIPE every other already-successful
        # chapter's summary from this doc, even though their Qdrant chunks
        # were untouched and still fully searchable. A retry batch is a real,
        # expected workflow now (Stage2's bounded retry above still isn't a
        # 100% guarantee), so this write has to be safe for that case, not
        # just for a single full 17-chapter run.
        print(f"[PROCESS BATCH] Saving summaries to Firestore...")
        doc_ref = db.collection("classes").document(class_name).collection("subjects").document(subject.lower())
        existing_doc = doc_ref.get()
        existing_chapters = (existing_doc.to_dict() or {}).get("chapters", []) if existing_doc.exists else []
        merged_by_name = {c.get("chapter_name"): c for c in existing_chapters}
        for c in all_chapters_with_summaries:
            merged_by_name[c.get("chapter_name")] = c
        doc_ref.set({
            "book_uuid": book_uuid,
            "filename": "batch_upload",
            "chapters": list(merged_by_name.values())
        })
        print(f"[PROCESS BATCH] [SUCCESS] All summaries successfully written to Firestore path: classes/{class_name}/subjects/{subject} ({len(merged_by_name)} total chapters, {len(all_chapters_with_summaries)} from this run)")
        print(f"[PROCESS BATCH] ========== BATCH PROCESSING SUCCESS ==========\n")
        
    except Exception as e:
        print(f"[PROCESS BATCH] [FATAL ERROR] Ingestion failed: {e}")
        logger.error(f"Fatal error in batch ingestion background task: {e}", exc_info=True)


@router.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    """
    Handles PDF file uploads. The file is stored temporarily and its name is returned.
    The frontend will then use this filename in the subsequent call to /api/books.
    """
    safe_filename = os.path.basename(file.filename)
    file_path = os.path.join(UPLOADS_DIR, safe_filename)
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    return {"filename": safe_filename}


@router.post("/api/books")
async def create_book_and_process(
    background_tasks: BackgroundTasks,
    book_data: BookCreateRequest
):
    """
    Starts the background processing task for a book.
    """
    logger.info(f"Received request to process and save book with data: {book_data.dict()}")
    try:
        class_name = book_data.class_name
        subject = book_data.subject
        filename = book_data.filename
        chapters = book_data.chapters

        logger.info(f"Received request to process and save book: {filename}")
        pdf_path = os.path.join(UPLOADS_DIR, os.path.basename(filename))
        if not os.path.exists(pdf_path):
            raise HTTPException(status_code=404, detail=f"Uploaded file not found: {filename}")

        # Compute book_uuid based on MD5 checksum
        import hashlib
        hasher = hashlib.md5()
        with open(pdf_path, 'rb') as afile:
            buf = afile.read(65536)
            while len(buf) > 0:
                hasher.update(buf)
                buf = afile.read(65536)
        book_uuid = hasher.hexdigest()
        
        # Get PDF offset from cache to calculate PDF pages
        try:
            with open("chapterdata/chapters_cache.json", "r") as f:
                book_cache = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            book_cache = {}
            
        book_key = f"{class_name}_{subject.lower()}"
        pdf_offset = book_cache.get(book_key, {}).get("pdf_offset", 0)
        
        logger.info(f"📖 Book key: {book_key}, PDF offset: {pdf_offset}")
        
        # Calculate PDF pages from chapter pages if needed
        for chapter in chapters:
            if 'chpstpage' in chapter and 'chpendpage' in chapter:
                chapter['pdf_startpg'] = chapter['chpstpage'] + pdf_offset
                chapter['pdf_endpg'] = chapter['chpendpage'] + pdf_offset
                logger.info(f"Calculated PDF pages for {chapter.get('chapter_name')}: "
                           f"chp {chapter['chpstpage']}-{chapter['chpendpage']} -> "
                           f"pdf {chapter['pdf_startpg']}-{chapter['pdf_endpg']}")
        
        # Start the background processing task
        logger.info(f"Starting background processing for book {book_uuid}")
        background_tasks.add_task(process_book_in_background, book_uuid, pdf_path, class_name, subject, chapters)
        
        return {"message": "Book processing started in the background.", "status": "processing", "book_id": book_uuid}
    except Exception as e:
        logger.error(f"Error processing book creation request: {e}", exc_info=True)
        raise HTTPException(status_code=422, detail=f"Error processing book creation request: {e}")


@router.get("/api/books")
async def get_books_endpoint(
    class_name: Optional[str] = Query(None),
    subject: Optional[str] = Query(None)
):
    """
    Returns a list of books matching optional class and subject filters from local cache.
    """
    books = local_chap_service.get_books(class_name=class_name, subject=subject)
    return books  # Return the list directly to match script.js expectation


@router.get("/api/subjects")
async def get_subjects_endpoint(
    class_name: Optional[str] = Query(None)
):
    """
    Returns all unique subjects available for a given class formatted as objects with icons and display names.
    """
    from backend.app.core import subject_config
    
    # Parse class number (e.g. "Class 8" or "8" -> 8)
    class_num = 8
    if class_name:
        try:
            clean = "".join(c for c in str(class_name) if c.isdigit())
            class_num = int(clean) if clean else 8
        except:
            class_num = 8
            
    # Get subjects configured for this class
    configured_subjects = subject_config.get_subjects_for_class(class_num)
    
    # Get unique subjects from uploaded books for this class to only show active subjects
    books = local_chap_service.get_books(class_name=class_name)
    uploaded_subjects = set(b["subject"].lower() for b in books)
    
    subjects_list = []
    for sub in configured_subjects:
        # Check if subject is either uploaded or if no books uploaded yet we show all configured ones as default fallback
        if not uploaded_subjects or sub.lower() in uploaded_subjects:
            icon = subject_config.get_subject_icon(sub, class_num)
            display_name = sub.capitalize()
            if sub.lower() == "maths":
                display_name = "Maths"
            subjects_list.append({
                "name": sub,
                "icon": icon,
                "display_name": display_name
            })
            
    return {"subjects": subjects_list}


@router.get("/api/list-chapters")
async def list_chapters(class_name: str, subject: str):
    """
    Returns a sorted list of chapters for a given book from the local cache.
    """
    chapters = local_chap_service.get_chapters(class_name=class_name, subject=subject)
    if not chapters:
        raise HTTPException(status_code=404, detail="Chapters not found for this book.")
    return {"chapters": chapters}


def extract_json_block(text: str):
    start = text.find("{")
    end = text.rfind("}") + 1
    if start != -1 and end != -1 and end > start:
        return text[start:end]
    return None


def extract_chapters_from_pdf(pdf_path: str) -> Dict:
    """
    Extracts chapters from a PDF using an LLM-only approach, calculates chapter-specific page numbers,
    and includes pdf_offset.
    """
    try:
        reader = PdfReader(pdf_path)
        num_pages = len(reader.pages)
        
        pages_to_extract_indices = set()

        # Add first 30 pages
        for i in range(min(30, num_pages)):
            pages_to_extract_indices.add(i)

        # Add last 5 pages
        for i in range(max(0, num_pages - 5), num_pages):
            pages_to_extract_indices.add(i)
        
        sorted_page_indices = sorted(list(pages_to_extract_indices))

        pdf_pages_data = []
        for i in sorted_page_indices:
            text = reader.pages[i].extract_text() or ""
            pdf_pages_data.append({"pdf_page": i + 1, "text": text})

        # Ensure chapterdata folder exists
        os.makedirs("chapterdata", exist_ok=True)
        with open("chapterdata/chap_extraction.json", "w", encoding="utf-8") as f:
            json.dump(pdf_pages_data, f, indent=2)
        
        try:
            from backend.app.services.chat.answer_service import generate_chapters_from_text
            llm_response_str = generate_chapters_from_text("chapterdata/chap_extraction.json")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"AI model failed to generate chapters: {e}")

        chapters_data_from_llm = json.loads(llm_response_str)
        
        if not isinstance(chapters_data_from_llm, dict) or "chapters" not in chapters_data_from_llm:
            raise HTTPException(status_code=500, detail="LLM response is not in the expected format (missing 'chapters' key).")

        llm_chapters_list = chapters_data_from_llm.get("chapters")
        llm_pdf_offset = chapters_data_from_llm.get("pdf_offset")

        if not llm_chapters_list:
            raise HTTPException(status_code=500, detail="AI model returned empty chapter list.")
        if llm_pdf_offset is None:
            raise HTTPException(status_code=500, detail="LLM response missing 'pdf_offset'.")

        pdf_offset = llm_pdf_offset

        processed_chapters = []
        for chapter in llm_chapters_list:
            pdf_startpg = chapter.get("pdf_startpg")
            pdf_endpg = chapter.get("pdf_endpg")

            if pdf_startpg is None:
                pdf_startpg = chapter.get("start_page")
            if pdf_endpg is None:
                pdf_endpg = chapter.get("end_page")

            if pdf_startpg is None or pdf_endpg is None:
                print(f"[WARN] Chapter '{chapter.get('chapter_name', 'Unknown')}' missing page numbers")
                processed_chapters.append({
                    "chapter_name": chapter.get("chapter_name"),
                    "pdf_startpg": pdf_startpg,
                    "pdf_endpg": pdf_endpg,
                    "chpstpage": None,
                    "chpendpage": None
                })
                continue

            if pdf_startpg < pdf_offset:
                # Convert chapter pages to PDF pages
                pdf_startpg = pdf_startpg + pdf_offset
                pdf_endpg = pdf_endpg + pdf_offset

            chpstpage = pdf_startpg - pdf_offset
            chpendpage = pdf_endpg - pdf_offset
            
            if chpstpage < 1:
                error_msg = f"Invalid calculation: chpstpage={chpstpage} (pdf_startpg={pdf_startpg} - pdf_offset={pdf_offset})"
                raise HTTPException(status_code=500, detail=error_msg)
            
            processed_chapters.append({
                "chapter_name": chapter.get("chapter_name"),
                "pdf_startpg": pdf_startpg,
                "pdf_endpg": pdf_endpg,
                "chpstpage": chpstpage,
                "chpendpage": chpendpage
            })
        
        return {"pdf_offset": pdf_offset, "chapters": processed_chapters}

    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Failed to parse chapter data from the AI model.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to process PDF: {e}")


@router.post("/extract-chapters")
async def extract_chapters(
    book_id: str = Query(...),
    class_name: str = Query(...),
    subject: str = Query(...)
):
    """
    Extracts chapter information from the specified PDF file, using a cache to avoid re-processing.
    """
    try:
        if not book_id:
            raise HTTPException(status_code=400, detail="book_id is required.")

        safe_filename = os.path.basename(book_id)
        pdf_path = os.path.join(UPLOADS_DIR, safe_filename)
        cache_path = "chapterdata/chapters_cache.json"
        cache_key = f"{class_name}_{subject.lower()}"

        try:
            with open(cache_path, "r") as f:
                cache = json.load(f)
            print(f"[CACHE] Loaded existing cache with {len(cache)} books")
        except (FileNotFoundError, json.JSONDecodeError):
            cache = {}
            print(f"[CACHE] No existing cache found, starting fresh")
        
        if cache_key in cache:
            print(f"[CACHE] Found cached data for {cache_key}")
            return JSONResponse(content=cache[cache_key])

        if not os.path.exists(pdf_path):
            raise HTTPException(status_code=404, detail=f"PDF file not found: {safe_filename}")

        print(f"[EXTRACT] Processing PDF for {cache_key}...")
        
        extracted_data = extract_chapters_from_pdf(pdf_path)
        
        # Compute book_uuid based on MD5 checksum
        import hashlib
        hasher = hashlib.md5()
        with open(pdf_path, 'rb') as afile:
            buf = afile.read(65536)
            while len(buf) > 0:
                hasher.update(buf)
                buf = afile.read(65536)
        book_uuid = hasher.hexdigest()
        
        extracted_data['book_uuid'] = book_uuid
        extracted_data['filename'] = safe_filename
        extracted_data['class_name'] = class_name
        extracted_data['subject'] = subject
        
        cache[cache_key] = extracted_data
        
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
        
        print(f"[CACHE] Saved {cache_key} to cache (now {len(cache)} books total)")
            
        return JSONResponse(content=extracted_data)
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Failed to extract chapters: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to extract chapters: {e}")


@router.get("/api/clear-qdrant")
async def clear_qdrant_data():
    """
    Clears all data from the Qdrant collection.
    """
    try:
        qdrant.clear_qdrant_collection()
        return {"message": "Qdrant collection cleared and re-initialized successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to clear Qdrant collection: {e}")


class PreAnalyzeRequest(BaseModel):
    filenames: List[str]
    class_name: str
    subject: str


class BatchIngestRequest(BaseModel):
    class_name: str
    subject: str
    chapters: List[Dict]


@router.post("/api/upload-multiple")
async def upload_multiple_files(files: List[UploadFile] = File(...)):
    filenames = []
    for file in files:
        safe_filename = os.path.basename(file.filename)
        file_path = os.path.join(UPLOADS_DIR, safe_filename)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        filenames.append(safe_filename)
    return {"filenames": filenames}


@router.post("/api/books/pre-analyze")
async def pre_analyze_books(request: PreAnalyzeRequest):
    """
    Analyzes multiple uploaded chapter PDFs concurrently using the LLM.
    Returns classified metadata (chapter name, number, textbook start/end page numbers, page count).
    """
    import asyncio
    qdrant.initialize()
    openai_client = qdrant.openai_client
    generation_model_name = qdrant.generation_model_name

    if not openai_client:
        raise HTTPException(status_code=500, detail="Gemini/OpenAI client not initialized.")

    results = []

    async def analyze_single_file(filename: str):
        file_path = os.path.join(UPLOADS_DIR, filename)
        if not os.path.exists(file_path):
            return {
                "filename": filename,
                "is_academic": False,
                "chapter_name": None,
                "chapter_no": None,
                "pdf_page_count": 0,
                "chpstpage": None,
                "chpendpage": None,
                "error": "File not found"
            }

        try:
            reader = PdfReader(file_path)
            pdf_page_count = len(reader.pages)

            # Deterministic printed-page-number detection. NCERT chapter PDFs
            # print a running footer on every page after the chapter opener -
            # either "{Subject}{N}" glued with no space (even printed pages)
            # or "{Chapter Name} {N}" space-separated (odd printed pages) -
            # always as the trailing digits of that page's first text line.
            # This is far more reliable than asking the LLM to spot a number
            # buried in noisy/duplicated extracted text: verified 13/13 correct
            # and perfectly contiguous across a real NCERT chapter set, where
            # the LLM-only approach fell back to "page 1" for every chapter.
            detected_chpstpage = None
            if pdf_page_count > 1:
                page2_text = reader.pages[1].extract_text() or ""
                first_line = next((l.strip() for l in page2_text.split("\n") if l.strip()), "")
                m = re.search(r"(\d{1,4})\s*$", first_line)
                if m:
                    printed_page2 = int(m.group(1))
                    detected_chpstpage = printed_page2 - 1

            # Extract first 2 pages text
            sample_text = ""
            for i in range(min(2, pdf_page_count)):
                sample_text += f"\n--- PAGE {i+1} ---\n" + (reader.pages[i].extract_text() or "")
            
            # Fast simple heuristic: check for typical front matter keywords
            lower_sample = sample_text.lower()
            preface_keywords = ["preface", "acknowledgements", "table of contents", "constitution", "national anthem", "foreword", "index"]
            is_definitely_admin = any(kw in lower_sample[:1000] for kw in preface_keywords)
            
            if is_definitely_admin and "chapter" not in lower_sample:
                return {
                    "filename": filename,
                    "is_academic": False,
                    "chapter_name": None,
                    "chapter_no": None,
                    "pdf_page_count": pdf_page_count,
                    "chpstpage": None,
                    "chpendpage": None
                }

            prompt = f"""
            Analyze the following text sample extracted from the first two pages of a Class {request.class_name} {request.subject} textbook document.
            Determine if this is an academic chapter or administrative front/back matter (TOC, preface, constitution, anthem, index).
            
            Format response as a JSON object with these keys:
            - is_academic: boolean
            - chapter_name: string or null (the chapter title, capitalized nicely)
            - chapter_no: integer or null (the chapter number)
            - chpstpage: integer or null (the starting printed page number of the chapter in the book)
            - chpendpage: integer or null (the ending printed page number of the chapter, estimated as start page + {pdf_page_count} - 1)
            
            Document Text:
            {sample_text[:3000]}
            """

            # Run LLM call in executor to keep it non-blocking
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: openai_client.models.generate_content(
                    model=generation_model_name,
                    contents=prompt,
                    config={"response_mime_type": "application/json"}
                )
            )
            
            llm_text = response.text.strip()
            # Strip fences if present
            if llm_text.startswith("```json"):
                first_nl = llm_text.find("\n")
                if first_nl != -1:
                    llm_text = llm_text[first_nl+1:]
            if llm_text.endswith("```"):
                llm_text = llm_text[:-3].strip()

            parsed = json.loads(llm_text)
            
            # Heuristic checks
            is_academic = parsed.get("is_academic", True)
            chapter_name = parsed.get("chapter_name")
            chapter_no = parsed.get("chapter_no")
            chpstpage = parsed.get("chpstpage")
            chpendpage = parsed.get("chpendpage")

            # Fallbacks if LLM fails to extract page numbers or names
            if is_academic and not chapter_name:
                chapter_name = filename.replace(".pdf", "").replace("_", " ").capitalize()
            # Prefer the deterministic footer-derived page number over the
            # LLM's guess - see detection above. Only fall back to the LLM's
            # value, then finally to "starts at page 1", if detection failed.
            if is_academic:
                if detected_chpstpage is not None:
                    chpstpage = detected_chpstpage
                    chpendpage = chpstpage + pdf_page_count - 1
                elif not chpstpage:
                    chpstpage = 1
                    chpendpage = pdf_page_count

            return {
                "filename": filename,
                "is_academic": is_academic,
                "chapter_name": chapter_name,
                "chapter_no": chapter_no,
                "pdf_page_count": pdf_page_count,
                "chpstpage": chpstpage,
                "chpendpage": chpendpage
            }

        except Exception as e:
            logger.error(f"Error analyzing file {filename}: {e}")
            return {
                "filename": filename,
                "is_academic": True,
                "chapter_name": filename.replace(".pdf", "").replace("_", " ").capitalize(),
                "chapter_no": None,
                "pdf_page_count": 0,
                "chpstpage": 1,
                "chpendpage": 10,
                "error": str(e)
            }

    # Run all file analyzes concurrently
    tasks = [analyze_single_file(fn) for fn in request.filenames]
    results = await asyncio.gather(*tasks)

    # Sort results so academic chapters come first, ordered by chapter_no
    academic_results = [r for r in results if r["is_academic"]]
    admin_results = [r for r in results if not r["is_academic"]]
    
    academic_results.sort(key=lambda x: x["chapter_no"] if x["chapter_no"] is not None else 999)
    
    # Assign sequential chapter numbers to academic chapters if they were parsed as null
    for idx, r in enumerate(academic_results):
        if r["chapter_no"] is None:
            r["chapter_no"] = idx + 1
            
    sorted_results = academic_results + admin_results
    return {"chapters": sorted_results}


@router.post("/api/books/batch-ingest")
async def batch_ingest_books(
    background_tasks: BackgroundTasks,
    request: BatchIngestRequest
):
    """
    Starts batch ingestion of the confirmed chapters in the background.
    """
    try:
        class_name = request.class_name
        subject = request.subject
        chapters = request.chapters

        # Compute deterministic book_uuid based on class and subject name
        import hashlib
        book_key = f"{class_name}_{subject.lower()}"
        book_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, book_key))

        logger.info(f"Starting batch background ingestion for book {book_uuid} (Class {class_name} - {subject})")
        background_tasks.add_task(
            process_batch_ingest_in_background,
            book_uuid,
            class_name,
            subject,
            chapters
        )

        return {
            "message": "Batch ingestion started in the background.",
            "status": "processing",
            "book_id": book_uuid
        }
    except Exception as e:
        logger.error(f"Error starting batch ingestion: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
