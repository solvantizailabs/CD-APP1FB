# new-flow → main merge checklist (RAG / chunking / embedding / retrieval)

Branch: `new-flow` (6 commits ahead of `main`, top: `3de69ba`)
Diff: 101 files, +18328 / -1063

## 1. New RAG engine — `backend/app/services/new_rag/` (all net-new, additive)

- **Ingestion** (`ingestion/pdf_parser.py`, `structure_parser.py`, `chunker.py`, `validator.py`)
  - Deterministic per-page text extraction (fixes old page-number footer bug)
  - LLM topic segmentation with anchor-heading byte-level verification (`text_matching.py`-style tolerant matcher)
  - Topic-aligned parent/child chunking (~400-token children, ~3000-token parent soft ceiling) — replaces old fixed-size 2000/400 & 400/100 char splitter that ignored semantic boundaries
  - Phase 1 additions (committed in `3de69ba`): `section`, `learning_objective`, `pdf_page` now flow through chunk metadata (previously only captured shallow page numbers)
  - Validation gates block-and-route-to-review instead of silently degrading
- **Embeddings** (`embeddings/embedding_service.py`, `image_embedding_service.py`, `embedding_batch.py`)
  - OpenAI `text-embedding-3-small` kept as sole production embedder
  - New image-embedding path for diagram/table chunks (description-based)
- **Indexing** (`indexing/qdrant_indexer.py`, `image_indexer.py`, `keyword_indexer.py`)
  - New Qdrant collection `textbooks_v3` with **native sparse vectors** (replaces per-book local-disk BM25 pickles — confirmed non-durable on Render)
- **Retrieval** (`retrieval/hybrid_retriever.py`, `semantic_retriever.py`, `keyword_retriever.py`, `metadata_filter.py`)
  - Hybrid dense+sparse fusion + **cross-encoder reranking** (new — old flow had no rerank step after RRF)
  - Adaptive retrieval depth via parent-escalation, bounded single-retry on low confidence
  - Explicit `confidence_tier` contract (replaces raw 0–1 RRF score thresholds that don't apply to the new logit scale)
- **Pipeline orchestration** (`pipeline/rag_pipeline.py::ingest_book()`) — single entry point for ingestion, takes a `book_uuid` override param so it matches the app's existing deterministic UUID scheme
- **Supabase artifact mirroring** (`supabase_artifacts.py`) — raw pages, topic manifest, chapter overview, chunks, diagram images/captions, tables, status, book index all persisted to the `book-processing` bucket (durability fix — local disk doesn't survive a Render redeploy)
- **Evaluation harness** (`evaluation/run_eval.py`, `test_dataset.json`, `precision_recall.py`) — new, didn't exist before; 10 real test cases seeded
- CLI + backfill/wipe utility scripts (`cli.py`, `backfill_*.py`, `wipe_book_data.py`) for standalone testing/maintenance

## 2. Live app wiring (this is a real cutover, not a dormant module)

| File | Change |
|---|---|
| `backend/app/api/routes/books.py` | `process_book_in_background()` (old single-book path, inline splitter+embed+upsert) **deleted**; `process_batch_ingest_in_background()` now calls `new_rag.ingest_book()` directly. Added Firestore `ingestion_jobs` doc for job-status polling. |
| `backend/app/api/routes/chat.py` | `/api/smart_query`'s retrieval call swapped from `qdrant.hybrid_search()` (textbooks_v2) to `new_rag_adapter.hybrid_search_v2()` (textbooks_v3). Dead `/api/query` / `query_engine()` endpoint removed, replaced by an internal-only `retrieve_only()` helper. Per-query debug record extended with `retrieval`, `retrieved_chunks`, `context_sent_to_llm`, `grounding` blocks. |
| `backend/app/orchestrator_test/test_runner.py` | Same retrieval swap. New `resolve_chapter_id_for_chapter()` maps orchestrator's `matched_chapter` name → new_rag's `chapter_id` UUID. New `ground_text_narration()` — a second, small, fail-open LLM call that grounds `text_narration` against retrieved chunks (runs before the existing restyle pass), gated on CURRICULUM + HIGH/MEDIUM confidence. All `book_has_content()` checks now query `textbooks_v3` via `new_rag_adapter`. |
| `backend/app/services/visual_learning/visual_learning_service.py` | **Real bug found and fixed during this branch's own work**: video-lesson generation was still calling old `qdrant.hybrid_search()` (missed in the original swap) — now uses `new_rag_adapter.hybrid_search_v2()` too. Confirms textbooks_v2 has **zero remaining live callers** after this branch. |
| `backend/app/services/retrieval/new_rag_adapter.py` | **New** — the adapter layer (`hybrid_search_v2()`, `book_has_content()`) all three call sites above route through. |
| `backend/app/services/retrieval/qdrant_service.py` | Dead code deleted: `process_and_embed_book()` (unused duplicate ingestion path, mismatched schema), `get_chapter_names()`, `get_chapters_for_book()` (read payload keys live ingestion never wrote). `hybrid_search()` (textbooks_v2) itself is left in place but has no remaining callers. |
| `backend/app/core/firestore_service.py` | `save_to_global_query_cache()` gained `query_json_url` param so cache-hit turns still resolve to the originating turn's full debug JSON. |
| `backend/app/services/analytics/analytics_service.py` | `log_query()` gained `format_decision` as a first-class Firestore field (previously only inside the linked debug JSON). |
| `backend/app/services/llm/openai_client.py` | `_messages()` now accepts a multimodal content-block list (dict items) alongside the existing plain-string path — additive, no existing caller affected. |
| `backend/app/api/routes/profile.py` | New `/api/students/lookup` (email → uid bridge). |
| `public/script.js` | Dead single-PDF-upload admin flow removed (confirmed unreachable — `setupAdminPage()` always posts to `/api/upload-multiple`). |

## 3. Standalone / not-yet-wired-in work also on this branch

- `backend/app/services/question_pipeline/` — Question Understanding & Routing layer (intent classification, routing, generation). **Not called from live `/api/smart_query` or `test_runner.py`** — exists standalone with its own `test_harness.py`. Per memory: deliberately staged (`question_pipeline` gets hardened in shadow mode before any cutover), not a merge blocker but also delivers nothing live yet.
- `terminal_test/` — CLI harness (ingestion.py, retrieval.py, answer_generation.py) for exercising new_rag end-to-end outside the live app; ground-truth files for several chapters (jemh106/113/114, jesc101).
- `hyperframes_engine/Candidate_test.md`, `INTERNAL_evaluation_notes.md` — evaluation notes, not code.

## 4. NOT part of this RAG work (uncommitted, unrelated) — do not conflate with the merge

Currently modified/untracked in the working tree, all image/visual-lesson pipeline work, unrelated to RAG:
- `backend/app/services/visual_learning/hyperframes_engine_bridge.py`
- `backend/app/services/visual_learning/template_registry.py`
- `backend/app/services/visual_learning/visual_lesson_prompt.py`
- `hyperframes_engine/templates/ImageScene.js`
- `image_test/` (untracked)
- `terminal_test/check_learning_objectives.py`, `inspect_empty_learning_objectives.py`, `spot_check_class10_maths.py`, ground-truth `.txt` files, one `outputs/retrieval/*.json` (untracked)

These need their own decision (commit separately / stash / discard) before or after the RAG merge — they're not evaluated in this checklist.

## 5. Known open items — verify before/soon after merging to main

- [ ] **Duplicate-on-re-ingest bug**: re-ingesting an already-live chapter creates a duplicate Qdrant point (fresh random `chapter_id` per run, no delete-before-insert). Needs a decision before re-running ingestion on anything already in production.
- [ ] **Confidence threshold is still a placeholder** (`-5.0`) — not empirically calibrated against a larger question set.
- [ ] Confirm `OPENAI_API_KEY` / project billing works in the target deploy environment — this branch's work was blocked earlier by `insufficient_quota` on a `sk-proj-` scoped key (per-project budget gotcha, separate from account-level credit).
- [ ] Confirm Qdrant target instance actually has (or will get) the `textbooks_v3` collection created (dense + sparse) — don't assume it exists on whatever Qdrant instance `main`/prod points at.
- [ ] Confirm Supabase `book-processing` bucket exists/is reachable in the deploy target (not just the dev instance this was verified against).
- [ ] `qdrant_service.py::hybrid_search()` (old textbooks_v2 function) is now dead code with zero callers — candidate for deletion post-merge, not blocking.
- [ ] `get_book_metadata()` in `qdrant_service.py` was flagged as "looks dead" but is actually still live via a WebSocket conversation feature — do NOT delete without re-checking.
- [ ] Already-ingested books currently in `textbooks_v2`/old collection (Class10 maths/science/social) will need **re-ingestion** into `textbooks_v3` post-merge — they won't retroactively appear in the new collection.
- [ ] Run `python -m backend.app.services.new_rag.evaluation.run_eval` against the target environment before/after merge as a retrieval-quality baseline.

## 6. Suggested merge sequence

1. Decide what to do with the 4 uncommitted image-pipeline files (§4) — commit separately or stash; don't merge them silently as part of "the RAG branch."
2. Merge `new-flow` → `main` (or open a PR) once the above uncommitted files are handled.
3. Post-merge, in the deploy target: confirm `textbooks_v3` Qdrant collection + Supabase bucket exist, confirm OpenAI key/quota, run `run_eval.py`.
4. Re-ingest any book/chapter currently only in the old collection.
5. Only after the pilot is proven again on `main`/deploy: do the post-pilot dead-code cleanup (`qdrant_service.hybrid_search()`, any other confirmed-dead helpers).
