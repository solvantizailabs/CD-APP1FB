"""
image_test / generate_image_lesson.py

ONE script, one question, one run: retrieves for a question (the same real
retrieval the app already uses, unchanged), shows you exactly what chunks
came back, and - if a diagram chunk among them is genuinely relevant -
builds and renders a real, playable HyperFrames video using that image.
If nothing relevant came back, it says so and stops; just rerun this same
command with a different question, no separate "finder" tool involved.

This is deliberately NOT a call into chat.py or
visual_learning_service.generate_visual_lesson_stream(): it mirrors that
same real pipeline (retrieval -> LLM storyboard -> Node engine compile)
step by step, but does its own retrieval call and its own LLM call so it
can pass image_candidates into get_visual_lesson_prompt() and widen the
allowed template set to include 'image_scene' for just this call - without
ever touching hyperframes_engine/shared/template-registry.json's 'status'
field (the single source of truth the real app's LLM prompt also reads),
which would turn 'image_scene' on for real production traffic immediately.
The orchestrator (master_orchestrator_prompt.txt) is not involved either -
same as a real video request already skips reusing its draft (see chat.py
~line 685's own comment) - the storyboard prompt itself, though, is the
SAME get_visual_lesson_prompt() function production uses, just called with
one extra optional argument. See image_test/README.md for the full
rationale.

Steps:
  1. GET /api/retrieve (HTTP, real production retrieval) for the question.
  2. Print every chunk that came back (text and diagram alike) - this is
     the "finalize the chunks" step, visible, not hidden in a JSON file.
  3. If no diagram chunk clears --min-score (MEDIUM_THRESHOLD, mirrors
     hybrid_retriever.py's own relevance bar), stop here with a clear
     message - rerun the same command with a different question.
  4. Otherwise: build the SAME production prompt (get_visual_lesson_prompt)
     with image_candidates populated - the one place this script's
     behavior differs from a real /api/smart_query video request.
  5. Call the real LLM client, parse, run the real registry repair pass
     (repair_scene_templates, with extra_ids=['image_scene']).
  6. Optionally synthesize real narration audio (--with-audio; off by
     default to avoid TTS cost on every iteration - SARVAM_API_KEY is
     already configured in .env, so this works when passed).
  7. Compile through the real Node engine (hyperframes_engine_bridge.
     compile_hyperframes_html_fast) - the exact function production calls.
  8. Save retrieved_chunks.json, storyboard_raw.json (LLM's own output),
     storyboard.json (post-repair, what actually got compiled), and
     lesson.json (full package) alongside the compiled index.html under
     image_test/outputs/<lesson_id>/.

Requires the application server already running (for step 1) and is run
from the repo root with `backend` importable (steps 4-7 use real backend
modules directly, unlike the HTTP-only terminal_test/ scripts, because no
public endpoint exposes "build a storyboard with these extra template
options" - and deliberately shouldn't, since that capability is test-only).

Usage:
    python image_test/generate_image_lesson.py "What is soil erosion?"
    python image_test/generate_image_lesson.py "What is soil erosion?" --with-audio
    python image_test/generate_image_lesson.py --class 10 --subject social "..."
"""
import argparse
import asyncio
import copy
import json
import os
import shutil
import sys
import uuid
from datetime import datetime, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

MEDIUM_THRESHOLD = -5.0  # mirrors hybrid_retriever.py - see find_image_questions.py docstring
OUTPUTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")


def prompt(text: str) -> str:
    try:
        return input(text).strip()
    except EOFError:
        return ""


def list_all_books(base_url: str) -> list:
    resp = httpx.get(f"{base_url}/api/books/all", timeout=15)
    resp.raise_for_status()
    return resp.json().get("books", [])


def choose_book(base_url: str, class_name: str = None, subject: str = None) -> dict:
    if class_name and subject:
        return {"class_name": str(class_name), "subject": subject}
    books = list_all_books(base_url)
    if not books:
        print("No ingested books found.")
        sys.exit(1)
    print("\nAvailable books:")
    for i, b in enumerate(books, start=1):
        print(f"  {i}. Class {b['class_name']} - {b['subject']} ({b.get('chapter_count', '?')} chapter(s))")
    while True:
        choice = prompt(f"\nSelect a book [1-{len(books)}]: ")
        if choice.isdigit() and 1 <= int(choice) <= len(books):
            return books[int(choice) - 1]
        print("Invalid choice, try again.")


def retrieve(base_url: str, query: str, class_name: str, subject: str) -> dict:
    resp = httpx.get(
        f"{base_url}/api/retrieve",
        params={"query": query, "class_name": class_name, "subject": subject},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


def print_retrieved_chunks(result: dict) -> None:
    """Shows every chunk retrieval actually returned - the visible
    'finalize the chunks' step, so you can see why a question did or
    didn't qualify without opening a JSON file."""
    chunks = result.get("chunks", [])
    print(f"\n{'-'*70}")
    print(f"RETRIEVED CHUNKS (confidence_tier={result.get('confidence_tier')}, "
          f"{len(chunks)} final chunk(s))")
    print(f"{'-'*70}")
    for i, c in enumerate(chunks, 1):
        ctype = c.get("chunk_type")
        topic = c.get("topic_name")
        score = c.get("score")
        if ctype == "diagram":
            print(f"  [{i}] DIAGRAM | score={score} | topic={topic!r}")
            print(f"      image_url: {c.get('structured_content')}")
            print(f"      caption:   {(c.get('text') or '')[:100]!r}")
        else:
            snippet = (c.get("text") or c.get("content") or "")[:100]
            print(f"  [{i}] text | score={score} | topic={topic!r} | {snippet!r}")
    print(f"{'-'*70}")


def build_context_and_candidates(result: dict, min_score: float):
    """Mirrors visual_learning_service.py's own context-building (top text
    chunks joined for LLM context) plus the diagram candidates this test
    adds on top - both drawn from the SAME retrieval result, not a second
    call or a different code path."""
    chunks = result.get("chunks", [])
    text_parts = [c.get("text") or c.get("content") or "" for c in chunks if c.get("chunk_type") != "diagram"]
    context = "\n\n---\n\n".join(p for p in text_parts[:5] if p)

    image_candidates = []
    for c in chunks:
        if c.get("chunk_type") != "diagram":
            continue
        score = c.get("score")
        if score is None or score < min_score:
            continue
        image_candidates.append({
            "image_url": c.get("structured_content"),
            "caption": c.get("text") or c.get("content") or "",
            "topic": c.get("topic_name") or "",
        })
    return context, image_candidates


async def main_async(args):
    from backend.app.services.visual_learning.visual_lesson_prompt import get_visual_lesson_prompt
    from backend.app.services.visual_learning.visual_learning_service import clean_and_parse_json
    from backend.app.services.visual_learning.template_registry import repair_scene_templates
    from backend.app.services.llm.openai_client import create_client
    from backend.app.services.visual_learning.hyperframes_engine_bridge import compile_hyperframes_html_fast

    book = choose_book(args.base_url, args.class_name, args.subject)
    class_name, subject = book["class_name"], book["subject"]
    question = args.question or prompt("\nEnter your question: ")
    if not question:
        print("No question given, exiting.")
        return

    print(f"\n[1/6] Retrieving for: {question!r} (Class {class_name} {subject})")
    result = retrieve(args.base_url, question, class_name, subject)
    print_retrieved_chunks(result)

    context, image_candidates = build_context_and_candidates(result, args.min_score)
    if not image_candidates:
        print(f"\n[STOP] No diagram chunk cleared min_score={args.min_score} for this question - "
              f"see the chunks above (no DIAGRAM lines, or their scores were too low).")
        print("       Try a different, more specific question about a diagram-covered topic in this "
              "chapter, and rerun this same command - no separate step needed.")
        return
    print(f"\n[2/8] {len(image_candidates)} image candidate(s) qualify (score >= {args.min_score}) - "
          f"offering them to the storyboard LLM:")
    for c in image_candidates:
        print(f"         - topic={c['topic']!r} caption={c['caption'][:70]!r}")

    print(f"\n[3/8] Building storyboard prompt (image_scene offered, {len(image_candidates)} candidate(s))...")
    llm_prompt = get_visual_lesson_prompt(class_name, subject, question, context, image_candidates=image_candidates)

    # Vision: send the actual candidate images alongside the text prompt, not
    # just their captions - so the LLM judges scene fit from what the image
    # really shows, the same multimodal content-block format Stage 3
    # grounding (ground_text_narration) already uses for the text-answer
    # path (see openai_client.py's _messages() docstring). Each image is
    # preceded by a text label so the model can tell which URL a given
    # picture corresponds to when it copies one into template_data.image_url.
    llm_contents = [llm_prompt]
    for i, cand in enumerate(image_candidates, 1):
        llm_contents.append({
            "type": "text",
            "text": f"Image candidate {i} (topic: {cand['topic']!r}, url: {cand['image_url']}):",
        })
        llm_contents.append({"type": "image_url", "image_url": {"url": cand["image_url"]}})

    client = create_client()
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    print(f"[4/8] Calling LLM ({model}) for storyboard, WITH vision on {len(image_candidates)} image(s)...")
    response = client.models.generate_content(
        model=model,
        contents=llm_contents,
        config={"response_mime_type": "application/json", "temperature": 0.2},
    )
    blueprint = clean_and_parse_json(response.text.strip())
    raw_clips = blueprint.get("clips", blueprint.get("scenes", []))
    for idx, item in enumerate(raw_clips, 1):
        if isinstance(item, dict):
            item["scene_no"] = idx
    # deepcopy, not list() - repair_scene_templates() below mutates each clip
    # dict IN PLACE, and list(raw_clips) only copies the outer list, leaving
    # storyboard_raw.json pointing at the same dict objects. Confirmed live:
    # this silently corrupted "raw" debug output to already reflect
    # POST-repair state, which looked like a repair-pass bug (steps not
    # populated on replay) that was actually just this copy bug feeding
    # already-mutated data back in.
    storyboard_raw = copy.deepcopy(raw_clips)

    print(f"[5/8] Repairing/validating {len(raw_clips)} scene(s) against the registry (+image_scene)...")
    clips = repair_scene_templates(raw_clips, log=print, extra_ids=["image_scene"])
    image_scenes = [c for c in clips if c.get("template_id") == "image_scene"]
    if image_scenes:
        print(f"       image_scene used in {len(image_scenes)} scene(s):")
        for s in image_scenes:
            print(f"         scene_no={s.get('scene_no')} image_url={s.get('template_data', {}).get('image_url')}")
    else:
        print("       [WARN] LLM did not use image_scene for any scene despite qualifying candidates "
              "(allowed by the prompt's own rules if it judged none matched a scene's concept).")

    lesson_id = f"imgtest_{uuid.uuid4().hex[:8]}"
    output_dir = os.path.join(OUTPUTS_DIR, lesson_id)
    os.makedirs(output_dir, exist_ok=True)
    # render_dir MUST be the same folder generate_slide_audio() writes its local
    # .wav files to (hardcoded inside that function as uploads/visual_lessons/
    # <lesson_id> - not configurable) - this is also exactly what production's
    # own generate_visual_lesson_stream() uses for both audio AND compile, for
    # the same reason. The Node compiler reads the real local .wav file to get
    # each scene's EXACT duration for GSAP timeline pacing; if compiled from a
    # different folder (image_test/outputs/<id>, as this script used to do), it
    # can't find the file and silently falls back to a rough word-count
    # estimate per scene instead - real, confirmed cause of audio/video timing
    # drift ("latency between each TTS") when the two folders diverged.
    render_dir = os.path.join(PROJECT_ROOT, "uploads", "visual_lessons", lesson_id)
    os.makedirs(render_dir, exist_ok=True)

    if args.with_audio:
        print("[6/8] Synthesizing narration audio (--with-audio)...")
        from backend.app.services.visual_learning.visual_audio_generator import generate_slide_audio
        audio_urls = await generate_slide_audio(clips, lesson_id)
        for idx, scene in enumerate(clips):
            if idx < len(audio_urls):
                scene["audio_url"] = audio_urls[idx]
    else:
        print("[6/8] Skipping audio (pass --with-audio to synthesize real narration)")
        for scene in clips:
            scene.setdefault("audio_url", "")

    lesson_package = {
        "lesson_id": lesson_id,
        "lesson_title": blueprint.get("lesson_title", f"Image Test: {question}"),
        "layout_mode": blueprint.get("layout_mode", "timeline"),
        "theme": blueprint.get("theme", "indigo"),
        "global_assets": blueprint.get("global_assets", []),
        "connections": blueprint.get("connections", []),
        "scenes": clips,
    }

    with open(os.path.join(output_dir, "retrieved_chunks.json"), "w", encoding="utf-8") as f:
        json.dump({"question": question, "class_name": class_name, "subject": subject,
                    "min_score": args.min_score, "retrieval_result": result}, f, indent=2, ensure_ascii=False)
    with open(os.path.join(output_dir, "storyboard_raw.json"), "w", encoding="utf-8") as f:
        json.dump({"blueprint": blueprint, "raw_clips": storyboard_raw}, f, indent=2, ensure_ascii=False)
    with open(os.path.join(output_dir, "storyboard.json"), "w", encoding="utf-8") as f:
        json.dump(clips, f, indent=2, ensure_ascii=False)
    # lesson.json must exist in render_dir (compile_hyperframes_html_fast reads it
    # from there) - also saved into output_dir below, after compiling, alongside
    # the compiled index.html, so every artifact for this run ends up in one place.
    with open(os.path.join(render_dir, "lesson.json"), "w", encoding="utf-8") as f:
        json.dump(lesson_package, f, indent=2, ensure_ascii=False)

    print(f"[7/8] Compiling through the real HyperFrames Node engine...")
    compile_result = await compile_hyperframes_html_fast(lesson_id, render_dir)

    # Mirror the real compiled artifacts (index.html, shared/, lesson.json) into
    # this run's own output_dir too, so everything for this test lives in one
    # folder as requested - the render itself still happened in render_dir
    # (required for correct audio timing), this is just a convenience copy.
    for item in os.listdir(render_dir):
        s_path = os.path.join(render_dir, item)
        d_path = os.path.join(output_dir, item)
        if os.path.isfile(s_path):
            shutil.copy2(s_path, d_path)
        elif os.path.isdir(s_path):
            shutil.copytree(s_path, d_path, dirs_exist_ok=True)

    index_path = os.path.join(output_dir, "index.html")
    print(f"\n{'='*70}")
    if compile_result and os.path.exists(index_path):
        print(f"DONE - engine={compile_result.get('engine')} degraded_reason={compile_result.get('degraded_reason')}")
        # The Supabase-hosted copy (compile_result['cloud_url']) is a backup
        # artifact only - Supabase Storage sandboxes HTML (blocks all script
        # execution) so it can NEVER actually play the composition, only the
        # app-server route can. Confirmed live: navigating to the Supabase URL
        # throws "Blocked script execution ... sandboxed" in the console.
        # Only ever offer the working URL here to avoid repeating that mistake.
        watch_url = f"{args.base_url}{compile_result['url']}" if compile_result.get("url") else None
        if watch_url:
            print(f"\nOpening in your browser now: {watch_url}")
            if not args.no_open:
                import webbrowser
                webbrowser.open(watch_url)
            else:
                print("(--no-open passed, not launching automatically)")
        else:
            print(f"\n[WARN] No app-server URL available (server route missing from compile result) - "
                  f"open the local file directly instead: {index_path}")
        if compile_result.get("cloud_url"):
            print(f"Cloud backup (download only, will NOT play - Supabase sandboxes HTML): {compile_result['cloud_url']}")
    else:
        print("[ERROR] Compilation did not produce index.html - check the Node compiler output above.")
    print(f"Saved: retrieved_chunks.json, storyboard_raw.json, storyboard.json, lesson.json")
    print(f"  -> {output_dir}")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("question", nargs="?", default=None)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--class", dest="class_name", default=None)
    parser.add_argument("--subject", default=None)
    parser.add_argument("--min-score", type=float, default=MEDIUM_THRESHOLD)
    parser.add_argument("--with-audio", action="store_true", help="Synthesize real narration audio (real TTS cost)")
    parser.add_argument("--no-open", action="store_true", help="Don't prompt to auto-open the video in a browser when done")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
