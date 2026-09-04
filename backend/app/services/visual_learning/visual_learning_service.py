import json
import uuid
import logging
import asyncio
import os
import re
from backend.app.services.retrieval import qdrant_service as qdrant
from backend.app.services.retrieval import new_rag_adapter
from backend.app.services.visual_learning.visual_lesson_prompt import get_visual_lesson_prompt
from backend.app.services.visual_learning.asset_retrieval_service import retrieve_asset_url
from backend.app.services.visual_learning.visual_audio_generator import generate_slide_audio

logger = logging.getLogger(__name__)

def _scene_template_data_is_empty(template_data) -> bool:
    """A scene has no usable visual content if template_data is missing/empty
    or every value in it is falsy (empty string/list/dict/None)."""
    if not isinstance(template_data, dict) or not template_data:
        return True
    return not any(v for v in template_data.values() if v not in (None, "", [], {}))

def storyboard_content_is_empty(clips: list) -> bool:
    """
    True if the storyboard has no real content to render - i.e. template_data
    came back empty for all or nearly all scenes. Seen occasionally when the
    LLM returns malformed JSON that clean_and_parse_json's repair pipeline
    salvages structurally but loses scene content from, or when the LLM
    itself returns placeholder-only scenes. Shipping this silently produces
    a lesson that plays audio/titles but shows nothing.
    """
    if not clips:
        return True
    empty_count = sum(1 for c in clips if _scene_template_data_is_empty(c.get("template_data")))
    if len(clips) <= 2:
        return empty_count == len(clips)
    return empty_count >= len(clips) - 1

def clean_and_parse_json(response_text: str) -> dict:
    """
    Resilient JSON parser that handles code blocks, unescaped text,
    missing object closing braces, trailing commas, and malformed LLM output.
    """
    text = response_text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()
        
    # Attempt standard parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning(f"[VisualLearning JSON Cleaner] Initial json.loads failed ({e}). Attempting sanitization...")

    # Apply multi-stage JSON repair pipeline
    cleaned = text
    # Remove single-line C++ style comments
    cleaned = re.sub(r'//.*', '', cleaned)

    # Fix missing object closing brace before scene boundaries
    cleaned = re.sub(r'(\"template_data\"\s*:\s*\{[\s\S]*?\})\s*,\s*(\{\s*\"scene_no\")', r'\1}\n,\n\2', cleaned)
    cleaned = re.sub(r'(\}\s*\n\s*),\s*(\n\s*\{)', r'\1}\n,\2', cleaned)
    # Fix trailing commas before closing braces/brackets
    cleaned = re.sub(r',\s*([\}\]])', r'\1', cleaned)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Extract outermost JSON object using regex
    match = re.search(r'(\{[\s\S]*\})', text)
    if match:
        extracted = match.group(1).strip()
        extracted = re.sub(r'//.*', '', extracted)
        extracted = re.sub(r'(\"template_data\"\s*:\s*\{[\s\S]*?\})\s*,\s*(\{\s*\"scene_no\")', r'\1}\n,\n\2', extracted)
        extracted = re.sub(r',\s*([\}\]])', r'\1', extracted)
        
        # Auto-close unclosed braces/brackets if truncated
        open_braces = extracted.count('{') - extracted.count('}')
        open_brackets = extracted.count('[') - extracted.count(']')
        if open_brackets > 0:
            extracted += ']' * open_brackets
        if open_braces > 0:
            extracted += '}' * open_braces

        try:
            return json.loads(extracted)
        except json.JSONDecodeError:
            pass

    # Final attempt: line filter
    cleaned_lines = []
    for line in text.splitlines():
        trimmed = line.strip()
        if not trimmed:
            continue
        if any(trimmed.startswith(c) for c in ['{', '}', '[', ']', '"', ',', ':', '//', '1', '2', '3', '4', '5', '6', '7', '8', '9', '0', 'true', 'false', 'null']):
            cleaned_lines.append(line)

    cleaned_text = "\n".join(cleaned_lines)
    cleaned_text = re.sub(r'(\"template_data\"\s*:\s*\{[\s\S]*?\})\s*,\s*(\{\s*\"scene_no\")', r'\1}\n,\n\2', cleaned_text)
    cleaned_text = re.sub(r',\s*([\}\]])', r'\1', cleaned_text)
    
    return json.loads(cleaned_text)

def _build_video_personalization_block(student_profile: dict = None) -> str:
    """
    SS6.4 gap fix: the video/storyboard pipeline previously received no
    personalization signal at all (response_style/quadrant/escalation/
    per-student history) - only the text QUICK_ANSWER path did. This gives
    it the two highest-value signals (delivery style, and per-student
    memory continuity - the actual bug this whole project started from)
    without duplicating the full directive block from
    master_orchestrator_prompt.txt.
    """
    if not student_profile:
        return ""
    lines = []
    style = student_profile.get("response_style")
    if style:
        style_hint = {
            "storytelling": "Frame the lesson's narration as a short story/analogy where possible.",
            "direct": "Keep scene narration direct and to the point, minimal preamble.",
            "detailed": "Break explanations into explicit, clearly sequenced steps.",
        }.get(style, "")
        if style_hint:
            lines.append(f"- **Student's Preferred Style ({style})**: {style_hint}")
    history = student_profile.get("per_student_history") or []
    if history:
        prior = "; ".join(
            (h.get("reformulated_question") or h.get("question") or "")[:80] for h in history[:2]
        )
        lines.append(
            f"- **This Student's Prior Related Learning**: They previously asked about: {prior}. "
            f"If relevant, build on that instead of re-explaining it from scratch."
        )
    if not lines:
        return ""
    return "\n### PERSONALIZATION CONTEXT (personalized_learning.md SS6.4):\n" + "\n".join(lines) + "\n"


async def generate_visual_lesson_stream(query: str, book_uuid: str, class_name: str, subject: str,
                                         precomputed_storyboard: dict = None, student_profile: dict = None,
                                         precomputed_context: str = None):
    """
    Main pipeline to generate a visual lesson storyboard.
    Streams progress states synchronized with frontend UI steps, compiles Hyperframes composition,
    and returns completed lesson ready event.
    """
    lesson_id = None
    if precomputed_storyboard and precomputed_storyboard.get("lesson_id"):
        lesson_id = precomputed_storyboard.get("lesson_id")
    if not lesson_id:
        lesson_id = f"vl_{uuid.uuid4().hex[:8]}"
    print("\n======================================================================")
    print(f"[PIPELINE DEBUG] ENTER VisualLearning")
    print(f"   Query: '{query}' | Lesson ID: {lesson_id}")
    print("======================================================================\n")
    
    def _normalize_and_audit_clips(raw_clips: list, lesson_title: str) -> list:
        """Robust Clip Normalization (Guarantees dict object structure for every
        scene) + Template Selection Audit & Variety Validation Pass, driven
        entirely by hyperframes_engine/shared/template-registry.json (via
        template_registry.py) so this never needs editing when a template is
        added/removed/re-enabled - only the registry does."""
        clips = []
        for idx, item in enumerate(raw_clips, 1):
            if isinstance(item, dict):
                item["scene_no"] = idx
                clips.append(item)
            elif isinstance(item, str):
                clips.append({
                    "scene_no": idx,
                    "purpose": item,
                    "template_id": "title_slide" if idx == 1 else "concept_diagram",
                    "teacher_script": item,
                    "template_data": {"title": f"Scene {idx}", "subtitle": item}
                })

        from backend.app.services.visual_learning.template_registry import (
            get_active_template_ids,
            repair_scene_templates,
            apply_curated_diagrams,
            force_curated_diagram_scene,
            apply_primitive_diagrams,
            force_paired_organ_primitive,
            force_enclosure_primitive,
            force_node_network_primitive,
            force_branching_primitive,
        )
        valid_templates = get_active_template_ids()

        print("\n----------------------------------------------------------------------")
        print("[STORYBOARD AUDIT] LLM Template Selection & Reasoning Analysis:")
        print(f"   Lesson Title: {lesson_title}")
        print(f"   Total Scenes: {len(clips)}")
        print("----------------------------------------------------------------------")

        for idx, clip in enumerate(clips, 1):
            tid = clip.get("template_id", "concept_diagram")
            reasoning = clip.get("template_selection_reasoning", "No explicit reasoning provided.")

            is_valid = tid in valid_templates
            status_icon = "[OK]" if is_valid else "[FALLBACK]"
            print(f"   Scene {idx}: [{tid}] {status_icon}")
            print(f"           Reasoning: {reasoning[:90]}...")

        repair_scene_templates(clips, log=print)
        apply_curated_diagrams(clips, log=print)
        force_curated_diagram_scene(clips, log=print)
        apply_primitive_diagrams(clips, log=print)
        force_paired_organ_primitive(clips, log=print)
        force_enclosure_primitive(clips, log=print)
        force_node_network_primitive(clips, log=print)
        force_branching_primitive(clips, log=print)

        empty_count = sum(1 for c in clips if _scene_template_data_is_empty(c.get("template_data")))
        print(f"   [CONTENT AUDIT] {empty_count}/{len(clips)} scenes have empty template_data")

        for idx, clip in enumerate(clips, 1):
            if clip.get("template_id") == "illustrated_scene":
                # Curated/primitive-generated content is code-verified correct
                # regardless of element count (e.g. a container_flow with one
                # inflow and one outflow is genuinely complete at 3 elements)
                # - the sparse-scene floor only means something for the LLM's
                # own freehand elements, where a low count really did mean a
                # lazy/incomplete scene.
                if clip.get("_curated_diagram_id") or clip.get("_primitive_shape"):
                    continue
                n_elements = len((clip.get("template_data") or {}).get("elements", []))
                if n_elements < 4:
                    logger.warning(f"[HYPERFRAMES_ILLUSTRATED_SCENE_SPARSE] lesson='{lesson_title}' scene={idx} elements={n_elements} (spec floor=4)")
                    print(f"   [CONTENT AUDIT] Scene {idx}: illustrated_scene has only {n_elements} elements (spec floor is 4) - sparse scene")

        print("----------------------------------------------------------------------\n")

        return clips

    try:
        if precomputed_storyboard:
            yield f"data: {json.dumps({'type': 'progress', 'step': 'understanding_topic', 'status': 'complete', 'message': 'Using designed storyboard blueprint.'})}\n\n"
            await asyncio.sleep(0.1)
            yield f"data: {json.dumps({'type': 'progress', 'step': 'designing_lesson', 'status': 'complete', 'message': 'Storyboard animations configured.'})}\n\n"
            await asyncio.sleep(0.1)
            blueprint = precomputed_storyboard
            raw_clips = blueprint.get("clips", blueprint.get("scenes", []))
            global_assets = blueprint.get("global_assets", [])
            connections = blueprint.get("connections", [])
            layout_mode = blueprint.get("layout_mode", "timeline")
            theme = blueprint.get("theme", "indigo")

            try:
                clips = _normalize_and_audit_clips(raw_clips, blueprint.get('lesson_title', 'Untitled'))
            except Exception as e:
                logger.error(f"[VisualLearning] Failed to normalize precomputed storyboard: {e}")
                raise ValueError(f"Failed to parse storyboard from precomputed blueprint: {e}")

            if storyboard_content_is_empty(clips):
                logger.warning(f"[VisualLearning] Precomputed storyboard for lesson_id={lesson_id} has no usable visual content - proceeding anyway (no LLM call to retry here).")
        else:
            # Step 1: Retrieve context from book using hybrid search
            yield f"data: {json.dumps({'type': 'progress', 'step': 'understanding_topic', 'status': 'in_progress', 'message': 'Retrieving relevant textbook context...'})}\n\n"
            await asyncio.sleep(0.05)

            # RAG process swap (2026-08-21, docs/RAG_INTEGRATION_PLAN.md §4.2):
            # was qdrant.hybrid_search() (textbooks_v2) - found and fixed as a
            # real gap the original swap missed (this file wasn't in the
            # original call-site list, only test_runner.py/chat.py's /api/query
            # were). video_book_uuid passed in by chat.py is the SAME
            # resolved_book_uuid new_rag ingestion now writes under (see step 1's
            # book_uuid override param), so no book_uuid mapping issue here.
            context = ""
            if precomputed_context:
                # Reuse the question_pipeline's own Stage 4/5 RAG fetch (chat.py
                # passes it through) instead of re-running hybrid_search_v2 - see
                # chat_adapter.py's "retrieval_context" field for why: this used
                # to run the entire retrieval (incl. the CLIP image-vector pass)
                # a second time for the identical query/book_uuid, which is what
                # doubled memory use on video requests and caused a real OOM.
                context = precomputed_context
                logger.info(f"[VisualLearning] Reusing precomputed retrieval context ({len(context)} chars) - skipping duplicate hybrid search.")
            else:
                try:
                    retrieval_result = new_rag_adapter.hybrid_search_v2(
                        query=query,
                        book_uuid=book_uuid,
                        class_name=class_name,
                        subject=subject,
                    )
                    score_payload_pairs = retrieval_result["score_payload_pairs"]
                    if score_payload_pairs:
                        context = "\n\n---\n\n".join(
                            payload.get("text", "") for _score, payload in score_payload_pairs[:5]
                        )
                        logger.info(
                            f"[VisualLearning] Retrieved {len(score_payload_pairs)} chunks for context "
                            f"(confidence_tier={retrieval_result.get('confidence_tier')})."
                        )
                    else:
                        logger.warning("[VisualLearning] No chunks retrieved. Using query context only.")
                except Exception as e:
                    logger.error(f"[VisualLearning] Hybrid search failed: {e}")

            yield f"data: {json.dumps({'type': 'progress', 'step': 'understanding_topic', 'status': 'complete', 'message': 'Textbook context analyzed.'})}\n\n"

            # Step 2: Design lesson storyboard blueprint with the LLM. Retried
            # once (same prompt/context, fresh completion) if the result comes
            # back with no usable visual content - see storyboard_content_is_empty.
            # This guards against the occasional LLM/JSON-repair hiccup that
            # otherwise ships a lesson that plays but shows nothing.
            yield f"data: {json.dumps({'type': 'progress', 'step': 'designing_lesson', 'status': 'in_progress', 'message': 'Creating storyboard and scene animations...'})}\n\n"
            await asyncio.sleep(0.05)

            personalization_block = _build_video_personalization_block(student_profile)
            prompt = get_visual_lesson_prompt(class_name, subject, query, context, personalization_block)
            client = qdrant.openai_client

            if not client:
                raise RuntimeError("OpenAI Client is not initialized in qdrant_service.")

            candidate_models = [
                os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            ]
            candidate_models = list(dict.fromkeys(candidate_models))

            MAX_LLM_ATTEMPTS = 2
            blueprint = None
            clips = None

            for llm_attempt in range(1, MAX_LLM_ATTEMPTS + 1):
                response_text = None
                last_error = None

                for target_model in candidate_models:
                    try:
                        logger.info(f"[VisualLearning] Generating storyboard with '{target_model}' (attempt {llm_attempt}/{MAX_LLM_ATTEMPTS})...")
                        gen_config = {
                            "response_mime_type": "application/json",
                            "temperature": 0.2
                        }
                        response = client.models.generate_content(
                            model=target_model,
                            contents=prompt,
                            config=gen_config
                        )
                        if response and response.text:
                            response_text = response.text.strip()
                            logger.info(f"[VisualLearning] Successfully received response from '{target_model}' (length: {len(response_text)})")
                            break
                    except Exception as m_err:
                        last_error = m_err
                        logger.warning(f"[VisualLearning] Model '{target_model}' failed: {m_err}. Trying fallback model...")
                        await asyncio.sleep(1.0)

                if not response_text:
                    raise RuntimeError(f"Failed to generate storyboard with {target_model}: {last_error}")

                try:
                    blueprint = clean_and_parse_json(response_text)
                    raw_clips = blueprint.get("clips", blueprint.get("scenes", []))
                    global_assets = blueprint.get("global_assets", [])
                    connections = blueprint.get("connections", [])
                    layout_mode = blueprint.get("layout_mode", "timeline")
                    theme = blueprint.get("theme", "indigo")
                except Exception as e:
                    logger.error(f"[VisualLearning] Failed to clean/parse JSON storyboard: {e}")
                    raise

                try:
                    clips = _normalize_and_audit_clips(raw_clips, blueprint.get('lesson_title', 'Untitled'))
                except Exception as e:
                    logger.error(f"[VisualLearning] Failed to parse storyboard JSON. Raw response:\n{response_text[:500]}...\nError: {e}")
                    raise ValueError(f"Failed to parse storyboard JSON from LLM response: {e}")

                if storyboard_content_is_empty(clips):
                    if llm_attempt < MAX_LLM_ATTEMPTS:
                        logger.warning(f"[VisualLearning] Storyboard for lesson_id={lesson_id} came back with no usable content on attempt {llm_attempt}. Retrying LLM generation...")
                        print(f"   [CONTENT AUDIT] Empty storyboard detected - retrying generation (attempt {llm_attempt + 1}/{MAX_LLM_ATTEMPTS})")
                        continue
                    else:
                        logger.error(f"[VisualLearning] Storyboard for lesson_id={lesson_id} still empty after {MAX_LLM_ATTEMPTS} attempts.")
                        raise ValueError("The AI could not generate visual content for this topic after multiple attempts. Please try rephrasing your question.")
                break

        yield f"data: {json.dumps({'type': 'progress', 'step': 'designing_lesson', 'status': 'complete', 'message': f'Storyboard generated with {len(clips)} dynamic scenes.'})}\n\n"

        # Checkpoint: the storyboard (scenes + teacher_script) is finalized, but
        # audio hasn't been generated yet. Callers can use this to stream each
        # scene's teacher_script as the visible text answer right now - no
        # audio_url attached yet, so no separate TTS call is triggered for it.
        # The audio generated below (Step 4) is the ONLY TTS pass for this
        # lesson, and is reused for both the video and (via caching) any
        # future replay of this same text.
        yield f"data: {json.dumps({'type': 'storyboard_ready', 'lesson_title': blueprint.get('lesson_title'), 'scenes': clips})}\n\n"

        # Step 3: Retrieve Animated Scene Assets
        yield f"data: {json.dumps({'type': 'progress', 'step': 'generating_visuals', 'status': 'in_progress', 'message': 'Retrieving animated scene templates and visual assets...'})}\n\n"
        await asyncio.sleep(0.05)
        yield f"data: {json.dumps({'type': 'progress', 'step': 'generating_visuals', 'status': 'complete', 'message': 'Scene visual templates assembled.'})}\n\n"

        # Step 4: Synthesize Voice Narration Audio
        processed_scenes = clips
        total_clips = len(processed_scenes)
        yield f"data: {json.dumps({'type': 'progress', 'step': 'creating_narration', 'status': 'in_progress', 'message': f'Synthesizing AI voiceover narration (0/{total_clips})...'})}\n\n"
        await asyncio.sleep(0.1)
        
        progress_queue = asyncio.Queue()
        scenes_by_no = {s.get("scene_no"): s for s in processed_scenes}
        # Scenes are synthesized concurrently and their TTS calls can finish
        # in any order (confirmed live: a real 5-scene lesson completed
        # 1, 3, 4, 2, 5 - not 1-2-3-4-5). Streaming scene_audio_ready in raw
        # completion order would narrate the story out of sequence (scene 3's
        # narration before scene 2's). sorted_scene_nos/next_flush_idx/
        # pending_ready buffer completed scenes and only flush them once
        # every earlier scene in the story has also been flushed, so
        # scene_audio_ready always streams in strict narrative order
        # regardless of which one's TTS happened to finish first.
        sorted_scene_nos = sorted(scenes_by_no.keys())
        next_flush_idx = 0
        pending_ready = {}

        async def _on_audio_progress(slide_no: int, total_slides: int, audio_url: str, duration_ms: int = None):
            await progress_queue.put((slide_no, total_slides, audio_url, duration_ms))

        try:
            audio_task = asyncio.create_task(
                generate_slide_audio(clips, lesson_id, progress_callback=_on_audio_progress)
            )

            completed_count = 0
            while completed_count < total_clips and not audio_task.done():
                try:
                    s_no, total_s, s_audio_url, s_duration_ms = await asyncio.wait_for(progress_queue.get(), timeout=1.5)
                    completed_count += 1
                    scene = scenes_by_no.get(s_no)
                    if scene is not None and s_audio_url:
                        scene["audio_url"] = s_audio_url
                        if s_duration_ms is not None:
                            scene["tts_duration_ms"] = s_duration_ms
                        pending_ready[s_no] = scene
                        # Flush every scene that's now ready, in strict
                        # narrative order, starting from the earliest one
                        # still owed - not the raw completion order above.
                        while next_flush_idx < len(sorted_scene_nos) and sorted_scene_nos[next_flush_idx] in pending_ready:
                            ready_no = sorted_scene_nos[next_flush_idx]
                            ready_scene = pending_ready.pop(ready_no)
                            yield f"data: {json.dumps({'type': 'scene_audio_ready', 'scene': ready_scene})}\n\n"
                            next_flush_idx += 1
                except asyncio.TimeoutError:
                    pass

            audio_urls = await audio_task
            for idx, scene in enumerate(processed_scenes):
                if idx < len(audio_urls) and not scene.get("audio_url"):
                    scene["audio_url"] = audio_urls[idx]

            # Flush anything still buffered (e.g. an earlier scene never got
            # a real audio_url so later, already-ready scenes were stuck
            # waiting behind it) - stream what we have in order rather than
            # silently dropping it into only the final batched audio_ready.
            for ready_no in sorted_scene_nos[next_flush_idx:]:
                if ready_no in pending_ready:
                    yield f"data: {json.dumps({'type': 'scene_audio_ready', 'scene': pending_ready[ready_no]})}\n\n"
        except Exception as audio_err:
            logger.warning(f"[VisualLearning] Batch audio generation notice: {audio_err}")
            for scene in processed_scenes:
                if "audio_url" not in scene:
                    scene["audio_url"] = ""
            
        yield f"data: {json.dumps({'type': 'progress', 'step': 'creating_narration', 'status': 'complete', 'message': 'Voiceovers & narration ready.'})}\n\n"

        # Checkpoint: every scene now has its real (single-generation) audio_url.
        # Callers can stream each scene's teacher_script + this audio_url together
        # right now, well before the video itself finishes compiling - this is
        # the same real, single Sarvam pass that will also drive the video, not
        # a separate synthesis.
        yield f"data: {json.dumps({'type': 'audio_ready', 'lesson_title': blueprint.get('lesson_title'), 'scenes': processed_scenes})}\n\n"

        # Step 5: Compile Hyperframes Rendering Engine
        yield f"data: {json.dumps({'type': 'progress', 'step': 'hyperframes_engine', 'status': 'in_progress', 'message': 'Compiling Hyperframes 60fps HTML video composition...'})}\n\n"
        await asyncio.sleep(0.05)
        
        # Correctly calculate absolute PROJECT_ROOT (4 parent directory levels up from backend/app/services/visual_learning)
        MAIN_DIR = os.path.dirname(os.path.abspath(__file__))
        PROJECT_ROOT = os.path.abspath(os.path.join(MAIN_DIR, "..", "..", "..", ".."))
        output_dir = os.path.join(PROJECT_ROOT, "uploads", "visual_lessons", lesson_id)
        try:
            os.makedirs(output_dir, exist_ok=True)
            test_file = os.path.join(output_dir, ".write_test")
            with open(test_file, "w") as f:
                f.write("1")
            os.remove(test_file)
        except Exception:
            output_dir = os.path.join("/tmp", "uploads", "visual_lessons", lesson_id)
            os.makedirs(output_dir, exist_ok=True)
        
        lesson_package = {
            "lesson_id": lesson_id,
            "lesson_title": blueprint.get("lesson_title", f"Visual Lesson: {query}"),
            "layout_mode": layout_mode,
            "theme": theme,
            "global_assets": global_assets,
            "connections": connections,
            "scenes": processed_scenes
        }
        
        # Write lesson.json to root uploads storage directory
        lesson_json_path = os.path.join(output_dir, "lesson.json")
        with open(lesson_json_path, "w", encoding="utf-8") as f:
            json.dump(lesson_package, f, indent=2)
            
        # Upload storyboard lesson.json to Supabase Cloud Storage
        from backend.app.core.supabase_storage import upload_file_to_supabase
        cloud_lesson_json_url = upload_file_to_supabase(lesson_json_path, f"{lesson_id}/lesson.json")
        if cloud_lesson_json_url:
            lesson_package["storyboard_json_url"] = cloud_lesson_json_url
            logger.info(f"[Supabase Storage] Storyboard lesson.json uploaded -> {cloud_lesson_json_url}")
            
            # Re-write lesson.json with the embedded storyboard_json_url
            with open(lesson_json_path, "w", encoding="utf-8") as f:
                json.dump(lesson_package, f, indent=2)
            
        # Trigger engine compilation via the standalone Hyperframes service over
        # HTTP - Hyperframes runs on its own Render service now, so it has no
        # access to this process's local disk and can't be called in-process
        # anymore. We send the lesson data itself, not a local folder path.
        compiled_url = None
        render_engine = None
        degraded_reason = None
        hyperframes_url = os.getenv("HYPERFRAMES_SERVICE_URL", "").rstrip("/")
        if not hyperframes_url:
            logger.error(f"[HYPERFRAMES_ENGINE_DEGRADED] reason=missing_service_url lesson_id={lesson_id}")
        else:
            try:
                import httpx
                async with httpx.AsyncClient(timeout=60.0) as client:
                    resp = await client.post(
                        f"{hyperframes_url}/compile",
                        json={"lesson_id": lesson_id, "lesson_package": lesson_package},
                    )
                    resp.raise_for_status()
                    compile_result = resp.json()
                if compile_result:
                    # Deliberately NOT compile_result["cloud_url"] here - that's
                    # the raw Supabase URL, and Supabase serves public HTML
                    # objects as Content-Type: text/plain with a locked-down
                    # CSP sandbox (its own anti-abuse default), so the browser
                    # would receive it as inert text instead of a live page.
                    # "url" is the relative /uploads/... path, served by THIS
                    # app's own /uploads/{file_path} route (main.py) - that
                    # route already fetches the Supabase copy server-side and
                    # re-serves it with correct headers when it's not on local
                    # disk (built for surviving a redeploy; the split just
                    # makes that the normal case instead of the edge case).
                    compiled_url = compile_result.get("url")
                    render_engine = compile_result.get("engine")
                    degraded_reason = compile_result.get("degraded_reason")
            except Exception as compile_err:
                logger.error(f"[HYPERFRAMES_ENGINE_DEGRADED] reason=service_call_failed lesson_id={lesson_id} error={compile_err}")
                compiled_url = None

        if not compiled_url:
            logger.warning(f"[VisualLearning] No compiled video URL for lesson {lesson_id}. Falling back to client-side slide renderer.")
            render_engine = None

        if render_engine == "python_fallback":
            logger.warning(
                f"[VisualLearning] Lesson {lesson_id} served via python_fallback engine "
                f"(degraded_reason={degraded_reason}) - animation will be minimal."
            )

        # Attach URLs explicitly for Hyperframes player mounting
        lesson_package["html_url"] = compiled_url
        lesson_package["interactive_url"] = compiled_url
        lesson_package["video_url"] = None
        lesson_package["render_engine"] = render_engine
        lesson_package["render_degraded_reason"] = degraded_reason

        # Persist render_engine/degraded_reason to disk so it's queryable per-lesson
        # without needing to grep logs (logs rotate; this file doesn't).
        try:
            with open(lesson_json_path, "w", encoding="utf-8") as f:
                json.dump(lesson_package, f, indent=2)
        except Exception as e:
            logger.warning(f"[VisualLearning] Could not persist render_engine to lesson.json: {e}")

        yield f"data: {json.dumps({'type': 'progress', 'step': 'hyperframes_engine', 'status': 'complete', 'message': 'Hyperframes compilation complete.'})}\n\n"
        
        # Step 6: Launching Media Player & Emit lesson_ready Event
        yield f"data: {json.dumps({'type': 'progress', 'step': 'launching_lesson', 'status': 'in_progress', 'message': 'Launching media player...'})}\n\n"
        await asyncio.sleep(0.05)
        yield f"data: {json.dumps({'type': 'progress', 'step': 'launching_lesson', 'status': 'complete', 'message': 'Lesson ready!'})}\n\n"

        # Final Event Payload matching frontend handleSSEEvent contract
        ready_payload = {
            "type": "lesson_ready",
            "lesson_id": lesson_id,
            "lesson_title": lesson_package["lesson_title"],
            "interactive_url": compiled_url,
            "html_url": compiled_url,
            "video_url": None,
            "scene_count": len(processed_scenes),
            "lesson": lesson_package,
            "lesson_package": lesson_package
        }

        try:
            print("\n======================================================================")
            print(f"ðŸŽ‰ [RENDER LOG] [VISUAL LEARNING PIPELINE SUCCESS]")
            print(f"   Lesson ID: {lesson_id}")
            print(f"   Title: '{lesson_package['lesson_title']}'")
            print(f"   Scenes: {len(processed_scenes)}")
            print(f"   Compiled Player URL: {compiled_url}")
            print("======================================================================\n")
        except Exception:
            print(f"[RENDER LOG] [VISUAL LEARNING PIPELINE SUCCESS] Lesson ID: {lesson_id} | URL: {compiled_url}")

        yield f"data: {json.dumps(ready_payload)}\n\n"
        
    except Exception as e:
        logger.error(f"[VisualLearning] Failed to stream visual lesson storyboard: {e}", exc_info=True)
        try:
            print(f"\nâŒ [RENDER LOG] [VISUAL LEARNING PIPELINE ERROR]: {e}\n")
        except Exception:
            print(f"[RENDER LOG] [VISUAL LEARNING PIPELINE ERROR]: {e}")
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

