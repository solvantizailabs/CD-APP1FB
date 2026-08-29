# terminal_test

Terminal tools for exercising the real application - ingestion, retrieval,
and full answer generation - without the UI. All three scripts talk **only**
to the application's HTTP endpoints, never to internal Python modules
(chunking, embedding, retrieval logic, the orchestrator, personalization,
storage clients). That's the whole point: whatever changes underneath -
chunking strategy, embedding model, Qdrant swapped for a different vector
DB, Supabase swapped for a different storage backend, the orchestrator and
personalized-learning layers rebuilt entirely - these scripts keep working
unmodified, as long as the endpoint contracts below don't change. That
makes `answer_generation.py` in particular the standing integration test
for the orchestrator/personalization work specifically, not just a
snapshot of how it behaves today.

All three require the application server to already be running (`uvicorn
backend.app.main:app --host 0.0.0.0 --port 8000`, or whatever the current
launch config uses) - they are HTTP clients, not standalone processes.

---

## `ingestion.py` - upload and ingest a book from the terminal

Uploads one or more chapter PDFs (a single chapter, or a whole multi-chapter
book) through the exact same endpoints the UI's upload flow uses, and prints
the full result - no browser, no manual clicking through the review table.

**Endpoints used:**
1. `POST /api/upload-multiple` - uploads the PDF file(s)
2. `POST /api/books/pre-analyze` - classifies each file, builds the chapter list
3. `POST /api/books/batch-ingest` - starts real ingestion in the background
4. `GET /api/books/status` - polls until the background job finishes

**Usage:**
```
python terminal_test/ingestion.py --files "path/to/chapter1.pdf" "path/to/chapter2.pdf" \
    --class 10 --subject social
```
```
python terminal_test/ingestion.py --files "path/to/single_chapter.pdf" --class 10 --subject maths
```

Options: `--base-url` (default `http://127.0.0.1:8000`), `--poll-interval`
(default 10s), `--poll-timeout` (default 900s).

**Output:** a per-file report - `chapter_name` the pipeline actually
detected, `new_rag_chapter_id`, and status (`ingested` / `failed` /
`skipped_non_chapter`, with the reason for either).

**Note on `GET /api/books/status`:** this endpoint didn't exist before these
scripts - `batch-ingest` is fire-and-forget (kicks off a background task,
returns immediately). It was added as a real, permanent feature: a Firestore
job-progress record (`classes/{class}/subjects/{subject}/ingestion_jobs/{book_uuid}`)
that `process_batch_ingest_in_background` writes to as it works, so *any*
caller - this script, a future UI polling indicator, another service - can
ask "is this actually done yet" without reading local disk or importing
pipeline internals.

---

## `retrieval.py` - ask a question, see every retrieved chunk

Interactive terminal flow: optionally identify yourself by email (to see
only books for your class), pick a book, ask a question, see exactly what
the retriever returned - text chunks and diagram chunks (with their image
URL and caption) alike. Nothing else: no generation, no orchestrator, no
answer text. Loops so you can ask multiple questions against the same book
without restarting.

**Endpoints used:**
1. `GET /api/students/lookup?email=` - resolves a student's class from their email
2. `GET /api/subjects?class_name=` - lists configured subjects for a class
3. `GET /api/books/all` - lists every book (class + subject) that actually
   has ingested content, across every class - used both to filter (2) down
   to real books and as the full list when no email is given
4. `GET /api/retrieve?query=&class_name=&subject=` - runs retrieval only
   (the same production call the live app uses internally -
   `new_rag_adapter.hybrid_search_v2`, fusion + dedup + rerank + confidence
   tiering + parent escalation + the image-vector widening) and returns
   every resulting chunk, with none of the orchestrator/generation/TTS
   overhead a real answer would normally cost

**Usage:**
```
python terminal_test/retrieval.py
```
Then follow the prompts:
```
Enter your student email address (press Enter to skip):
Available books:
  1. Class 10 - social (1 chapter(s))
  2. Class 6 - maths (10 chapter(s))
  ...
Select a book [1-4]: 1
Enter your question (or press Enter to quit): What are the major soil types found in India?
```

**Output per chunk:** `chunk_type` (text vs. diagram), topic, rerank score,
and either the text content or - for a diagram - its real image URL and
caption. Also prints the overall retrieval status (`confidence_tier`,
whether a retry happened, whether parent-escalation triggered).

**Note on the three new endpoints (`students/lookup`, `books/all`,
`retrieve`):** none of these existed before this script needed them - each
was a real, permanent gap, not a workaround built just for this tool:
- `students/lookup` bridges "I only have an email" to the existing
  uid-keyed student profile (`users/{uid}` in Firestore already stores
  `email`), which nothing before this could do.
- `books/all` is the only way to answer "what books exist across every
  class" - `/api/books` reads a legacy local cache that isn't guaranteed to
  reflect real ingested content; `books/all` queries Firestore directly
  (via a `collection_group("subjects")` query, since `classes/{class}` is
  an implicit document with no field data of its own - a plain
  `collection("classes").stream()` silently misses everything).
- `retrieve` is the only endpoint that runs retrieval without also running
  the full orchestrator/generation/TTS flow that `/api/smart_query` always
  does.

---

## `answer_generation.py` - the real user.html flow, end to end

Replicates the actual logged-in student experience exactly: log in with an
email, ask a question, get the real generated answer - through
`/api/smart_query`, the *exact* endpoint the browser UI calls. Nothing here
is simulated - orchestrator classification, personalization (per-student
memory, escalation, quadrant), RAG retrieval, grounding, and generation all
run for real, unchanged. Loops so you can hold a multi-turn conversation
(session continuity carries across turns, same as the browser).

**Endpoints used:**
1. `GET /api/students/lookup?email=` - resolves the student's uid + class
2. `GET /api/subjects` + `GET /api/books/all` - resolves which subject to
   query (see "why no book-browsing list" below)
3. `GET /api/smart_query` - the real, full answer endpoint (SSE stream)
4. `GET /api/retrieve` - re-run retrieval for the same question, only to
   surface what fed the answer (see note below - `smart_query` never sends
   this detail to the browser either)

**Usage:**
```
python terminal_test/answer_generation.py
```
```
Enter your student email address: terminal_test@example.com
Welcome, Terminal Test Student (Class 10, uid=terminal_test_student)
Subject (auto-selected, only one available for Class 10): social

Ask a question (or press Enter to quit): What are the major soil types found in India?

ORCHESTRATOR ROUTING: classification=GENERAL_KNOWLEDGE | subject=None | chapter=None | format=QUICK_ANSWER

ANSWER:
India has several major soil types...

----------------------------------------------------------------------
RETRIEVAL DETAIL (what fed this answer - 2 chunk(s), confidence=HIGH, escalated=True)
----------------------------------------------------------------------
  [1] concept | 'Classification of Soils' | Classification of Soils...
  [2] diagram | 'Classification of Soils' | image: https://.../p8_4_Im2.jpg
```

**Why login is mandatory here** (unlike `retrieval.py`, which allows
skipping it): this script exists to test the real, *personalized*
per-student experience - class resolution, per-student memory, escalation,
quadrant - all of which need a real uid. There's no anonymous path, by
design. It uses the `mock-token-{uid}` dev bypass already built into
`auth_middleware.py` (same pattern `test_personalization_cli.py` already
uses) to identify an already-registered student, rather than performing
real Firebase password sign-in - this script authenticates as a known
student, it doesn't implement login.

**Why there's no book-browsing list**: the real UI doesn't show one either
- a logged-in student's class determines what's available, and the
question itself is what the orchestrator uses to resolve subject/chapter.
This script auto-selects the subject when a class has exactly one ingested
book (true for all current test data) and only prompts when a class
genuinely has more than one - `/api/smart_query` still requires an explicit
`class_name`/`subject`/`book_uuid` per call, and no endpoint exists (or
should exist) that resolves a book from question text alone before
retrieval has even run.

**Why `/api/retrieve` gets called a second time**: `/api/smart_query`'s SSE
stream sends the browser the final answer text and a routing summary
(`intent`), but never the retrieved-chunk list - that detail is written to
a per-query debug JSON on Supabase and linked from a Firestore
`user_queries` doc instead, never surfaced over SSE at all. Re-running
`/api/retrieve` with the identical question is more robust than
reconstructing that Supabase record directly (which would couple this
script to a Firestore/Supabase document shape instead of a stable
endpoint), and reuses the same proven display logic `retrieval.py` already
has.

**A real finding from the first live run, worth knowing before you rely on
this tool**: that exact example above happened for real. The orchestrator
classified a genuinely textbook-covered question as `GENERAL_KNOWLEDGE`
(`subject=None`, `chapter=None`) and skipped RAG/grounding/images entirely
- while the `/api/retrieve` call right below it, for the identical
question, found the correct content with HIGH confidence. The generated
answer is a generic, ungrounded one (it lists soil categories that don't
match the textbook's own framing), even though the real content was sitting
right there, retrievable. This is a prompt-layer classification issue (see
the two competing rules in `master_orchestrator_prompt.txt` - content
matching vs. trivia-style wording), not a retrieval or embedding bug, and
it's out of scope for the embedding/retrieval work this repo of scripts was
built to validate - but it's exactly the kind of thing `answer_generation.py`
exists to catch, and it caught it on its first real run, on two independent
questions now. Worth keeping in mind when reading any answer this script
produces: a `GENERAL_KNOWLEDGE` classification means nothing built in the
RAG/image pipeline touched that answer at all, regardless of whether the
content was actually covered.

---

## Why endpoint-only, not direct function calls

All three scripts could have imported `ingest_book()`, `hybrid_retriever.retrieve()`,
or `run_orchestrator_pipeline()` directly and skipped the HTTP layer
entirely - that would also work, and would even be a little faster. The
reason they don't: importing internals couples the test to *today's*
implementation. The moment chunking, the embedding model, the storage
backend, or the orchestrator/personalization logic changes, a script built
that way needs to change too, and worse, it stops testing what the real
application actually does end-to-end (auth middleware, request validation,
background task wiring, error handling at the API boundary - all of it).
Going through the same endpoints the UI uses means these scripts test the
real, whole system every time, and never need to change just because
something changed underneath - including, deliberately, the orchestrator
and personalized-learning rework still ahead.
