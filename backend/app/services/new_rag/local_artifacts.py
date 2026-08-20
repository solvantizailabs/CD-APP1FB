"""
Local-disk artifact storage for the new_rag standalone TEST tool only.

Locked as local-disk for this test harness specifically, per explicit user
direction during planning: the production design (docs/RAG_REDESIGN_PLAN.md,
section 7) still calls for Supabase Storage - this is deliberately simpler
because it's just for inspecting what the pipeline produced while testing,
not a production storage decision.

Folder layout, redesigned twice now after real use surfaced problems each
time: first from an unreadable flat UUID-named layout, then again after real
testing showed even the per-chapter file *names* didn't communicate the
pipeline's own stage order or separate concerns (chunks/diagrams/tables all
sat as loose sibling files, indistinguishable at a glance from one another
or from the chapter-level status file). Every filename below is now numbered
by pipeline stage, so a plain directory listing already reads top-to-bottom
in the order the pipeline actually ran - no need to open a file to know what
stage produced it:

    outputs/
      Class10_science/                       <- one folder per book, human-readable name
        book_index.json                      <- at-a-glance status of every chapter (name, status, folder)
        chapters/
          jesc101/                           <- one folder per chapter, named after the source PDF
            00_status.json                   <- chapter_name, chapter_id, status, per-stage validation results
            01_raw_pages.json                <- Stage 1: raw extracted text per page
            02_topics_manifest.json          <- Stage 2: detected topic/section structure
            03_chapter_overview.md           <- human-readable reconstruction, headings inserted at the right page
            04_chunks/
              parent_chunks.json             <- one entry per topic - the full text of that topic/section
              child_chunks.json              <- paragraph-sized slices of the parents, actually embedded for retrieval
            05_diagrams/
              captions.json                  <- one entry per diagram: caption, page, enclosing topic, image filename
              images/
                p3_0_Im0.png                  <- the actual extracted image files, viewable directly
            06_tables.json                    <- detected table-candidate text blocks (heuristic only, see pdf_parser.py)
        queries/
          query_report_20260820_100103.json
          context_package_20260820_100103.json

The chapter folder is named after the SOURCE PDF FILENAME (e.g. "jesc101"),
not a random chapter_id - this is available immediately at Stage 1, before
the LLM has even detected the real chapter title in Stage 2, and it is
directly traceable back to the file you uploaded. The real, LLM-detected
chapter name still gets recorded inside 00_status.json and in book_index.json
for reference - both are exposed, chosen deliberately rather than picking one.
Re-ingesting the same source file reuses the same folder rather than
accumulating a new UUID-named folder per attempt, which is what made the old
layout unnavigable after multiple retries on the same chapter.
"""
import json
import os
import re
from typing import Dict, List, Optional

OUTPUT_ROOT = os.path.join(os.path.dirname(__file__), "outputs")


def _sanitize(name: str) -> str:
    name = re.sub(r"[^\w\-]+", "_", str(name)).strip("_")
    return name or "unnamed"


def book_dir(class_name: str, subject: str) -> str:
    folder = _sanitize(f"Class{class_name}_{subject}")
    path = os.path.join(OUTPUT_ROOT, folder)
    os.makedirs(path, exist_ok=True)
    return path


def chapter_dir(class_name: str, subject: str, source_stem: str) -> str:
    path = os.path.join(book_dir(class_name, subject), "chapters", _sanitize(source_stem))
    os.makedirs(path, exist_ok=True)
    return path


def queries_dir(class_name: str, subject: str) -> str:
    path = os.path.join(book_dir(class_name, subject), "queries")
    os.makedirs(path, exist_ok=True)
    return path


def save_json(dir_path: str, filename: str, data) -> str:
    path = os.path.join(dir_path, filename)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return path


def load_json(dir_path: str, filename: str):
    path = os.path.join(dir_path, filename)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_chapter_markdown(dir_path: str, chapter_name: str,
                           raw_pages: List[Dict], topics: List[Dict]) -> str:
    """Human-readable reconstruction, for manual inspection during testing."""
    lines = [f"# {chapter_name}\n"]
    topics_by_start = {t["start_page"]: t["topic_name"] for t in topics}
    for p in raw_pages:
        tp = p.get("textbook_page")
        if tp in topics_by_start:
            lines.append(f"\n## [p.{tp}] {topics_by_start[tp]}\n")
        lines.append(p["text"])
    path = os.path.join(dir_path, "03_chapter_overview.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def save_binary(dir_path: str, relative_path: str, data: bytes) -> str:
    path = os.path.join(dir_path, relative_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def update_book_index(class_name: str, subject: str, source_stem: str, entry: Dict) -> str:
    """
    Updates this book's book_index.json with one chapter's current status -
    the single at-a-glance file for "what happened across every chapter in
    this book", so verifying a multi-chapter upload doesn't require opening
    every chapter folder individually.
    """
    path = os.path.join(book_dir(class_name, subject), "book_index.json")
    index = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            index = json.load(f)
    index[source_stem] = entry
    with open(path, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
    return path
