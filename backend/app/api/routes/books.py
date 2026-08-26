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
from google.cloud import firestore

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

def _ingestion_job_ref(class_name: str, subject: str, book_uuid: str):
    """
    Firestore doc backing GET /api/books/status - a real, durable job-progress
    record (not a workaround), so a terminal/CLI caller (or any future caller)
    can poll a stable endpoint for "is this batch-ingest actually done yet"
    without reading local disk or importing pipeline internals. Lives under
    the same class/subject path as the summary doc, in its own subcollection
    so it never collides with the chapters[] summary document itself.
    """
    return (db.collection("classes").document(class_name)
            .collection("subjects").document(subject.lower())
            .collection("ingestion_jobs").document(book_uuid))


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

    job_ref = _ingestion_job_ref(class_name, subject, book_uuid)
    job_ref.set({
        "status": "processing",
        "book_uuid": book_uuid,
        "class_name": class_name,
        "subject": subject,
        "total_chapters": len(chapters),
        "started_at": firestore.SERVER_TIMESTAMP,
        "results": [],
    })

    try:
        # Initialize services (still needed: qdrant.book_has_content() and other
        # textbooks_v2-era helpers elsewhere in the app still read qdrant.client;
        # new_rag's ingest_book() below opens its own textbooks_v3 client).
        qdrant.initialize()

        all_chapters_with_summaries = []

        for i, chapter_data in enumerate(chapters):
            # Pre-analyze's chapter_name is only ever a shallow, first-2-page
            # guess used to label the pre-upload confirmation table - it must
            # never be passed into ingest_book() as an override, since
            # new_rag's own Stage 2 detection (reading the FULL chapter text)
            # is strictly more reliable at this. `chapter_name` here is
            # reassigned below to the pipeline's own detected title as soon
            # as ingest_book() returns it.
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
                    chapter_name=None,  # let new_rag's own Stage 2 detection name the
                                        # chapter from the full chapter text - the
                                        # pre-analyze guess above must never override it
                    book_uuid=book_uuid,  # reuse the SAME book_uuid this route already computed,
                                          # so textbooks_v2 and textbooks_v3 agree on identity
                )
                print(f"[PROCESS BATCH] │  Ingesting via new_rag pipeline (status={ingest_report['status']})")
                # Adopt the pipeline's own detected name for everything downstream
                # (summary generation, Firestore doc, logging) - it read the full
                # chapter text, the pre-analyze guess above only saw 2 pages.
                chapter_name = ingest_report.get("chapter_name") or chapter_name

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
                    job_ref.update({"results": firestore.ArrayUnion([{
                        "filename": filename, "chapter_name": chapter_name,
                        "status": "skipped_non_chapter", "message": ingest_report.get("message"),
                    }])})
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
                job_ref.update({"results": firestore.ArrayUnion([{
                    "filename": filename, "chapter_name": chapter_name,
                    "status": "ingested", "new_rag_chapter_id": new_rag_chapter_id,
                }])})
            except Exception as chapter_err:
                # Isolation boundary: a failure anywhere in this chapter's
                # processing (bad page, embedding error, Qdrant hiccup) must
                # not abort the remaining chapters in the batch.
                print(f"[PROCESS BATCH] │  ✗ Chapter failed, skipping to next: {chapter_err}")
                logger.error(f"Chapter '{chapter_name}' ({filename}) failed during batch ingestion: {chapter_err}", exc_info=True)
                print(f"[PROCESS BATCH] └─ Skipped {chapter_name}\n")
                job_ref.update({"results": firestore.ArrayUnion([{
                    "filename": filename, "chapter_name": chapter_name,
                    "status": "failed", "error": str(chapter_err),
                }])})
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
        job_ref.update({"status": "completed", "completed_at": firestore.SERVER_TIMESTAMP})

    except Exception as e:
        print(f"[PROCESS BATCH] [FATAL ERROR] Ingestion failed: {e}")
        logger.error(f"Fatal error in batch ingestion background task: {e}", exc_info=True)
        job_ref.update({"status": "failed", "error": str(e), "completed_at": firestore.SERVER_TIMESTAMP})


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

            # Deliberately does NOT ask the LLM to name the chapter here - a
            # 2-page/3000-char sample is not enough context to reliably read
            # the real chapter title (confirmed live: it regularly picked up
            # a subheading or the book's series title instead). Naming is
            # left entirely to new_rag's own Stage 2 detection during
            # ingest_book(), which reads the full chapter text. This call's
            # only job is is_academic/chapter_no/page-range classification
            # for the pre-upload confirmation table.
            prompt = f"""
            Analyze the following text sample extracted from the first two pages of a Class {request.class_name} {request.subject} textbook document.
            Determine if this is an academic chapter or administrative front/back matter (TOC, preface, constitution, anthem, index).

            Format response as a JSON object with these keys:
            - is_academic: boolean
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
            chapter_no = parsed.get("chapter_no")
            chpstpage = parsed.get("chpstpage")
            chpendpage = parsed.get("chpendpage")

            # Placeholder label only, for the pre-upload confirmation table -
            # the real name comes from new_rag's own detection during
            # ingestion (see ingest_book() call above/below), never from here.
            chapter_name = filename.replace(".pdf", "").replace("_", " ").capitalize() if is_academic else None
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


@router.get("/api/books/all")
async def list_all_books():
    """
    Lists every (class, subject) with real ingested content, reading
    Firestore directly - the durable source of truth process_batch_ingest_in_background
    actually writes to (classes/{class}/subjects/{subject}), not the
    /api/books legacy local-cache endpoint (local_chap_service), which isn't
    guaranteed to reflect what's really been ingested. Built for a
    no-login/browse-all flow (e.g. a terminal tool with no student email to
    resolve a class from) - deliberately a real collection-group query, not
    a workaround, since there was no existing way to answer "what books
    exist across every class" at all before this.
    """
    try:
        # collection_group, not collection("classes").stream() - save_summary_document()
        # never .set()s the parent classes/{class} document itself, only the
        # nested subjects/{subject} doc, which makes classes/{class} an
        # "implicit" document (exists only via reference, no field data of
        # its own). Firestore's own docs confirm a plain collection listing
        # skips implicit documents entirely - confirmed live, this returned
        # an empty list against real, present data before switching to
        # collection_group, which queries every "subjects" subcollection
        # across the whole database regardless of parent-document state.
        books = []
        for subject_doc in db.collection_group("subjects").stream():
            data = subject_doc.to_dict() or {}
            chapters = data.get("chapters", [])
            if not chapters:
                continue
            class_name = subject_doc.reference.parent.parent.id
            books.append({
                "class_name": class_name,
                "subject": subject_doc.id,
                "book_uuid": data.get("book_uuid"),
                "chapter_count": len(chapters),
            })
        return {"books": books}
    except Exception as e:
        logger.error(f"Failed to list all books: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/books/status")
async def get_batch_ingest_status(class_name: str, subject: str, book_id: str):
    """
    Polls the progress of a batch-ingest job started via POST /api/books/batch-ingest
    (book_id is the `book_id` that endpoint returned). Reads the Firestore job-progress
    record written by process_batch_ingest_in_background - a real, durable status source,
    not a workaround, so any caller (terminal script, future UI polling, another service)
    can check "is this actually done yet" without depending on local disk or importing
    pipeline internals. Survives a server restart mid-job, unlike an in-memory job map would.

    status: "processing" | "completed" | "failed" | "not_found" (no job with this book_id
    exists yet, or was for a different subject/class - not the same as "processing").
    """
    job_doc = _ingestion_job_ref(class_name, subject, book_id).get()
    if not job_doc.exists:
        return {"status": "not_found", "book_id": book_id}
    return job_doc.to_dict()
