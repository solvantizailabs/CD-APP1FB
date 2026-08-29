# image_test

Isolated test harness for exercising HyperFrames' `image_scene` template
with real, retrieved textbook images - the real photos/diagrams your image
pipeline (`docs/IMAGE_PIPELINE_PLAN.md`) already extracts, embeds, and
stores in Supabase.

**This never touches the real user-facing flow.** `image_scene` stays
`"status": "banned"` in `hyperframes_engine/shared/template-registry.json`
- the single source of truth both this test and the real app's
`/api/smart_query` -> `visual_learning_service.py` path read from. A real
student asking for a video, through the real app, from the UI or any
deployed instance, will never see `image_scene` offered to the LLM. This
folder only widens the template choice set for its own, separate LLM calls
via an `extra_ids` parameter threaded through `template_registry.py` and
`get_visual_lesson_prompt()` - both default to `None`/unused, so every
production call is byte-identical to before this existed.

## Does this use the same prompt/retrieval/orchestrator as a real video?

- **Retrieval**: yes, exactly - `GET /api/retrieve`, the same
  `new_rag_adapter.hybrid_search_v2()` call the real app uses internally.
- **Storyboard prompt**: yes, the SAME function -
  `get_visual_lesson_prompt()` in `visual_lesson_prompt.py` - not a forked
  copy. It just accepts one new optional argument, `image_candidates`,
  which only this test populates. When it's `None` (every real call), the
  generated prompt text is identical to today's.
- **Orchestrator**: no, and this is true for real video generation too -
  read `chat.py` (~line 682) and its own comment: once the orchestrator
  classifies a request as a video, it deliberately does NOT reuse the
  orchestrator's own draft; `generate_visual_lesson_stream()` does its own
  independent retrieval + LLM call for the storyboard. This test mirrors
  that same downstream half of the real pipeline; it just skips the
  upstream "is this a video request" classification, since here we already
  know the answer.
- **Final render**: yes, the same Node engine
  (`hyperframes_engine_bridge.compile_hyperframes_html_fast`,
  `run-storyboard.js`) production uses for real - not a mock.

## Requirements

- The app server running (`uvicorn backend.app.main:app`) - step 1 below
  calls its HTTP API.
- Run from the repo root, with `backend` importable - steps 2 onward
  import real backend modules directly (there is deliberately no public
  endpoint for "build a storyboard with extra template options"; that
  capability should stay test-only, not become part of the app's API
  surface).

## Usage

One script, one question, one run - no separate "find a question first"
step. Just ask:

```bash
python image_test/generate_image_lesson.py "What is soil erosion?"
python image_test/generate_image_lesson.py "What is soil erosion?" --with-audio
```

It retrieves for the question (same real `/api/retrieve` call, unchanged),
prints every chunk that came back so you can see exactly what was
retrieved, and:

- **If a diagram chunk among them clears `MEDIUM_THRESHOLD`** (-5.0, the
  same relevance bar `hybrid_retriever.py` already uses internally - not a
  new number invented for this test): it builds the storyboard with that
  image and renders the video. Forcing a mismatched image (e.g. a
  respiratory-system photo on a digestive-system question) isn't a valid
  test of image handling, so this gate isn't optional - see
  `template_registry.py`'s own `match_diagram_with_score` for why this repo
  already treats "only attach an image that actually scored as relevant" as
  a hard rule elsewhere.
- **If nothing qualifies**: it prints the chunks it did find, says so
  clearly, and stops. Just rerun the same command with a different, more
  specific question about a diagram-covered topic - no separate tool, no
  extra file.

`--with-audio` opts into real Sarvam TTS narration (real cost per run,
`SARVAM_API_KEY` is already configured in `.env`) - off by default so you
can iterate for free; without it the video plays silently.

Prints a local `index.html` path - open it directly in a browser to watch
the video.

## What's saved per run

Under `image_test/outputs/<lesson_id>/` (lesson ids are prefixed
`imgtest_` so they're easy to tell apart from real lessons, including in
the shared Supabase bucket the compile step also uploads to):

- `retrieved_chunks.json` - the full `/api/retrieve` response for the
  question (every chunk, scores, confidence tier) - the retrieval log.
- `storyboard_raw.json` - exactly what the LLM returned, before the
  registry repair pass.
- `storyboard.json` - the final, repaired scene list actually compiled
  into the video (what to inspect if a scene looks wrong).
- `lesson.json` - the full lesson package (matches the shape a real
  lesson's `lesson.json` has).
- `index.html` - the compiled, playable video.

## Known limitation carried over from the engine itself

`image_scene`'s `template_data.annotations` field is documented in the
registry but is dead code - `Renderer.js` creates an empty SVG group for it
and nothing populates it. The prompt (`_build_image_candidates_block` in
`visual_lesson_prompt.py`) tells the LLM to fold caption meaning into
`teacher_script` narration instead of relying on it. Zoom/pan motion
(`animation_style: "simple_zoom"` or `zoom_targets`) is real and does work
- that part of the engine was already fully built, just switched off.
