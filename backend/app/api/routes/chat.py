import json
import time
import datetime
import logging
import asyncio
from typing import List, Dict, Optional
from pydantic import BaseModel
from fastapi import APIRouter, Query, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import StreamingResponse

from backend.app.services.retrieval import qdrant_service as qdrant
from backend.app.services.chat.answer_service import (
    reformulate_with_llm,
    context_aware_reformulate,
    generate_smart_followups,
    generate_teacher_explanation
)
from backend.app.services.chat.session_service import session_manager
from backend.app.services.chat.intent_classifier import determine_next_action
from backend.app.services.chat.conversation import conversation_manager
from backend.app.services.analytics import analytics_service
from backend.app.services.analytics import enhanced_analytics
from backend.app.prompts import styler as prompt_styler
from backend.app.core.auth_middleware import get_user_id_or_default
from backend.app.core.firebase.firebase_init import db
from backend.app.core import firestore_service
from backend.app.core.firestore_service import check_global_query_cache, save_to_global_query_cache
from backend.app.services.personalization import profile_service
from backend.app.orchestrator_test.test_runner import run_orchestrator_pipeline
from backend.app.services.deployment_logger import save_chat_log_background

def format_text_explanation(text) -> str:
    if not text:
        return ""
    if isinstance(text, list):
        text = "\n\n".join(str(item) for item in text)
    else:
        text = str(text)
    
    import re
    # 1. Add space after punctuation if followed directly by any letter (English or Devanagari)
    text = re.sub(r'([.!?à¥¤])(?=[a-zA-Z\u0900-\u097F])', r'\1 ', text)
    
    # 2. Format sideheadings (bold text followed by a colon) to put the description on a new line
    text = re.sub(r'-\s*\*\*([^*]+)\*\*:\s*', r'- **\1**:<br>', text)
    text = re.sub(r'(^|\n)\s*\*\*([^*]+)\*\*:\s*(?!<br>)', r'\1**\2**:<br>', text)
    
    # 3. Replace inline bullet markers (e.g. ".- **" or " - **") with clean double newlines and bullets
    text = re.sub(r'(?<!\n)(?:\s*\.\s*)-\s*(\*\*)?', r'.\n\n- \1', text)
    text = re.sub(r'(?<!\n)(?:\s+)-\s*(\*\*)?', r'\n\n- \1', text)
    
    # 4. Ensure double newlines before bullets for clean vertical spacing in markdown rendering
    text = re.sub(r'(?<!\n)\n-\s*', r'\n\n- ', text)
    
    return text

logger = logging.getLogger(__name__)

router = APIRouter()

class QueryRequest(BaseModel):
    query: str
    class_name: str
    subject: str
    book_uuid: str

class SummaryRequest(BaseModel):
    class_name: str
    subject: str
    chapter_name: str


class FeedbackRequest(BaseModel):
    query_id: str
    feedback_type: str            # "like" or "dislike"
    feedback_text: Optional[str] = None
    uid: Optional[str] = None


def track_cumulative_analytics(uid: str, query: str, subject: str, chapter_name: str = "Unknown"):
    """
    Track cumulative analytics for persistent dashboard stats.
    """
    from datetime import datetime
    try:
        logger.info(f"[CUMULATIVE ANALYTICS] Starting tracking for uid: {uid}, subject: {subject}, chapter: {chapter_name}")
        doc_ref = db.collection('users').document(uid).collection('user_analytics').document('user_analytics_doc')
        doc = doc_ref.get()
        
        today = datetime.now().strftime('%Y-%m-%d')
        week = datetime.now().strftime('%Y-W%W')
        
        if doc.exists:
            data = doc.to_dict()
        else:
            data = {
                'total_queries_all_time': 0,
                'total_subjects_explored': 0,
                'current_streak': 0,
                'longest_streak': 0,
                'last_activity_date': None,
                'daily_stats': {},
                'weekly_stats': {},
                'subjects_set': [],
                'created_at': datetime.now().isoformat()
            }
        
        data['total_queries_all_time'] = data.get('total_queries_all_time', 0) + 1
        
        if subject and subject.lower() not in [s.lower() for s in data.get('subjects_set', [])]:
            if 'subjects_set' not in data:
                data['subjects_set'] = []
            data['subjects_set'].append(subject.lower())
            data['total_subjects_explored'] = len(data['subjects_set'])
        
        if 'daily_stats' not in data:
            data['daily_stats'] = {}
        
        if today not in data['daily_stats']:
            data['daily_stats'][today] = {
                'queries_count': 0,
                'subjects': [],
                'chapters': []
            }
        
        data['daily_stats'][today]['queries_count'] += 1
        
        if subject and subject.lower() not in [s.lower() for s in data['daily_stats'][today].get('subjects', [])]:
            if 'subjects' not in data['daily_stats'][today]:
                data['daily_stats'][today]['subjects'] = []
            data['daily_stats'][today]['subjects'].append(subject.lower())
        
        if chapter_name and chapter_name not in data['daily_stats'][today].get('chapters', []):
            if 'chapters' not in data['daily_stats'][today]:
                data['daily_stats'][today]['chapters'] = []
            data['daily_stats'][today]['chapters'].append(chapter_name)
        
        if 'weekly_stats' not in data:
            data['weekly_stats'] = {}
            
        if week not in data['weekly_stats']:
            data['weekly_stats'][week] = {
                'queries_count': 0,
                'subjects': [],
                'active_days': []
            }
        
        data['weekly_stats'][week]['queries_count'] += 1
        
        if subject and subject.lower() not in [s.lower() for s in data['weekly_stats'][week].get('subjects', [])]:
            if 'subjects' not in data['weekly_stats'][week]:
                data['weekly_stats'][week]['subjects'] = []
            data['weekly_stats'][week]['subjects'].append(subject.lower())
        
        if today not in data['weekly_stats'][week].get('active_days', []):
            if 'active_days' not in data['weekly_stats'][week]:
                data['weekly_stats'][week]['active_days'] = []
            data['weekly_stats'][week]['active_days'].append(today)
        
        last_date = data.get('last_activity_date')
        if last_date:
            try:
                last = datetime.strptime(last_date, '%Y-%m-%d')
                today_dt = datetime.strptime(today, '%Y-%m-%d')
                diff = (today_dt - last).days
                
                if diff == 1:
                    data['current_streak'] = data.get('current_streak', 0) + 1
                elif diff == 0:
                    pass
                else:
                    data['current_streak'] = 1
            except Exception as e:
                logger.warning(f"[ANALYTICS] Error calculating streak: {e}")
                data['current_streak'] = 1
        else:
            data['current_streak'] = 1
        
        if data.get('current_streak', 0) > data.get('longest_streak', 0):
            data['longest_streak'] = data['current_streak']
        
        data['last_activity_date'] = today
        data['last_updated'] = datetime.now().isoformat()
        
        doc_ref.set(data)
        print(f"[CUMULATIVE ANALYTICS] Tracked for {uid}: Total={data['total_queries_all_time']}, Streak={data['current_streak']}, Subjects={data['total_subjects_explored']}")
        
    except Exception as e:
        logger.error(f"[CUMULATIVE ANALYTICS] Failed to track analytics for {uid}: {e}", exc_info=True)


@router.get("/api/query", tags=["LLM"])
async def query_engine(
    book_uuid: str = Query(...),
    query: str = Query(...),
    class_name: str = Query(...),
    subject: str = Query(...)
):
    """
    Streams the answer in real-time using Server-Sent Events (SSE).
    """
    async def event_generator():
        from backend.app.utils.llm_tracker import request_stats
        request_stats.set({"calls": [], "start_time": time.time(), "query": query})
        start = time.time()
        print(f"\n{'='*80}")
        print(f"[QUERY] New query received at {datetime.datetime.now().strftime('%H:%M:%S')}")
        print(f"[QUERY] User question: {query}")
        print(f"[QUERY] Book: Class {class_name} - {subject.capitalize()}")
        print(f"[QUERY] Book UUID: {book_uuid[:16]}...")
        print(f"{'='*80}\n")
        
        print(f"[FIRESTORE] Loading summaries from cache/Firestore...")
        summary_doc = firestore_service.load_summary_from_firestore(class_name, subject)
        if not summary_doc:
            yield f"data: {json.dumps({'error': 'No content found for this class/subject.'})}\n\n"
            return
        chapters = summary_doc["chapters"]
        print(f"[FIRESTORE] Loaded {len(chapters)} chapters\n")

        if book_uuid == "global" or not book_uuid:
            resolved_uuid = summary_doc.get("book_uuid")
            if resolved_uuid:
                book_uuid = resolved_uuid
                print(f"[QUERY] Mapped global book_uuid to resolved database UUID: {book_uuid}")
        
        try:
            reform = reformulate_with_llm(
                raw_query=query,
                class_name=class_name,
                subject=subject,
                chapters=chapters
            )
            
            reformulated_query = reform.get("reformulated_query", query)
            classification = reform.get("classification", "general")
            chapter_ranking = reform.get("chapter_ranking", [])
            
            print("\n" + "="*40)
            print("RAW USER QUESTION:")
            print(f"   \"{query}\"")
            print("="*40 + "\n")
            
            print("="*40)
            print("REFORMULATED QUERY:")
            print(f"   \"{reformulated_query}\"")
            print(f"CLASSIFICATION          : {classification}")
            print(f"TOP CHAPTERS IDENTIFIED : {len(chapter_ranking)}")
            print("="*40 + "\n")
            
        except Exception as e:
            print(f"[REFORMULATE] Error: {e}")
            reformulated_query = query
            classification = "general"
            chapter_ranking = chapters[:5]
        
        print(f"[SIMILARITY] Calculating semantic similarity scores for chapters...")
        try:
            from sentence_transformers import util
            query_embedding = qdrant.local_embedder.encode(reformulated_query, convert_to_tensor=True)
            
            scored_chapters = []
            for chapter in chapters:
                summary = chapter.get("summary", "")
                if summary:
                    summary_embedding = qdrant.local_embedder.encode(summary, convert_to_tensor=True)
                    similarity = util.cos_sim(query_embedding, summary_embedding)[0][0].item()
                    
                    chapter_with_score = chapter.copy()
                    chapter_with_score['relevance_score'] = round(similarity, 3)
                    scored_chapters.append(chapter_with_score)
                else:
                    chapter_copy = chapter.copy()
                    chapter_copy['relevance_score'] = 0.0
                    scored_chapters.append(chapter_copy)
            
            scored_chapters.sort(key=lambda x: x['relevance_score'], reverse=True)
            chapter_ranking = scored_chapters[:5]
            
            print(f"[SIMILARITY] Calculated similarity scores for {len(scored_chapters)} chapters")
        except Exception as e:
            print(f"[SIMILARITY] Error calculating similarity: {e}")
            if chapter_ranking:
                for ch in chapter_ranking:
                    if 'score' in ch and 'relevance_score' not in ch:
                        ch['relevance_score'] = ch['score']
                    elif 'relevance_score' not in ch:
                        ch['relevance_score'] = 0.0
        
        print(f"[RETRIEVAL] Performing hybrid search...")
        metadata = qdrant.get_book_metadata(book_uuid)
        
        processed_data = qdrant.reformulate_and_classify_query(
            query=reformulated_query,
            class_name=metadata.get("class_name"),
            subject=metadata.get("subject"),
            chapter_list=[ch["chapter_name"] for ch in chapter_ranking]
        )
        
        keywords = processed_data.get("keywords", [])
        conceptual_score = processed_data.get("conceptual_score", 0.5)
        
        top_chapter_names = [ch["chapter_name"] for ch in chapter_ranking[:5]]
        
        cleaned_keywords = []
        for kw in keywords:
            if isinstance(kw, dict):
                cleaned_keywords.append({"keyword": kw.get("keyword", ""), "importance": kw.get("importance", 0.5)})
            else:
                cleaned_keywords.append({"keyword": str(kw), "importance": 0.5})

        hybrid_results, semantic_results, bm25_results = qdrant.hybrid_search(
            book_uuid=book_uuid,
            query=reformulated_query,
            keywords=cleaned_keywords,
            conceptual_score=conceptual_score,
            metadata_filters={"chapter_names": top_chapter_names}
        )
        
        context = "\n\n---\n\n".join([doc["text"] for score, doc in hybrid_results[:10]])
        
        ans_txt_data = {
            "query": query,
            "reformulated_query": reformulated_query,
            "classification": classification,
            "conceptual_score": conceptual_score,
            "chapter_ranking": chapter_ranking,
            "semantic_results": semantic_results,
            "bm25_results": bm25_results,
            "hybrid_results": hybrid_results,
            "start_time": start
        }
        
        print(f"[LLM] Streaming answer...")
        final_prompt = prompt_styler.get_answer_prompt(
            class_name=class_name,
            subject=subject,
            query=reformulated_query,
            context=context
        )
        
        full_answer = ""
        try:
            response_stream = qdrant.openai_client.models.generate_content_stream(
                model=qdrant.generation_model_name,
                contents=final_prompt
            )
            
            for chunk in response_stream:
                try:
                    if chunk.text:
                        full_answer += chunk.text
                        event_data = json.dumps({
                            "display_text": chunk.text,
                            "read_text": chunk.text 
                        })
                        yield f"data: {event_data}\n\n"
                        await asyncio.sleep(0.01)
                except ValueError:
                    pass
            
            print(f"[LLM] Answer streamed ({len(full_answer)} characters)\n")
        except Exception as e:
            print(f"[LLM] Error generating answer: {e}\n")
            error_msg = "Sorry, I couldn't generate the answer."
            full_answer = error_msg
            event_data = json.dumps({"display_text": error_msg, "read_text": error_msg})
            yield f"data: {event_data}\n\n"
        
        yield "data: [DONE]\n\n"
        
        # Write to ans.txt
        try:
            with open("ans.txt", "w", encoding="utf-8") as f:
                f.write(f"{'='*80}\n")
                f.write(f"QUERY LOG - {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"{'='*80}\n\n")
                f.write(f"1. ORIGINAL QUERY:\n   {ans_txt_data['query']}\n\n")
                f.write(f"2. REFORMULATED QUERY:\n   {ans_txt_data['reformulated_query']}\n")
                f.write(f"   Classification: {ans_txt_data['classification']}\n")
                f.write(f"   Conceptual Score: {ans_txt_data['conceptual_score']:.2f}\n\n")
                f.write(f"3. CHAPTER RANKING:\n")
                for idx, ch in enumerate(ans_txt_data['chapter_ranking'], 1):
                    f.write(f"   {idx}. {ch['chapter_name']} (relevance: {ch.get('relevance_score', 'N/A')})\n")
                f.write(f"\n4. GENERATED ANSWER:\n{full_answer}\n\n")
        except Exception as e:
            print(f"[LOG] âœ— Error writing to ans.txt: {e}\n")
            
        try:
            rag_chunks = []
            if "hybrid_results" in ans_txt_data and ans_txt_data["hybrid_results"]:
                for score, doc in ans_txt_data["hybrid_results"][:5]:
                    rag_chunks.append({
                        "chunk_id": doc.get("chunk_id", "chunk_unknown"),
                        "text": doc.get("text", "")[:200],
                        "score": round(score, 3)
                    })
            save_chat_log_background(
                user_query=query,
                subject=subject,
                mode="text_to_text",
                session_id=None,
                retrieved_rag_chunks=rag_chunks,
                llm_response=full_answer,
                execution_time_ms=int((time.time() - start) * 1000)
            )
        except Exception as log_err:
            logger.error(f"[DeploymentLogger] Failed to log query_engine: {log_err}")

        from backend.app.utils.llm_tracker import print_query_performance_report
        print_query_performance_report()
            
    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/api/smart_query", tags=["LLM"])
async def smart_query_engine(
    request: Request,
    book_uuid: str = Query(...),
    query: str = Query(...),
    class_name: str = Query(...),
    subject: str = Query(...),
    session_id: str = Query(None),
    is_clicked_followup: bool = Query(False),
    tts_model: str = Query("sarvam"),
    tts_speaker: str = Query("ritu"),
    tts_language: str = Query("en-IN")
):
    """
    Smart query endpoint with conversational context, using an action-based routing model.
    """
    # Resolve FastAPI Query default objects if called programmatically
    if hasattr(session_id, "__class__") and session_id.__class__.__name__ == "Query":
        session_id = None
    if hasattr(is_clicked_followup, "__class__") and is_clicked_followup.__class__.__name__ == "Query":
        is_clicked_followup = False
    if hasattr(book_uuid, "__class__") and book_uuid.__class__.__name__ == "Query":
        book_uuid = ""
    if hasattr(query, "__class__") and query.__class__.__name__ == "Query":
        query = ""
    if hasattr(class_name, "__class__") and class_name.__class__.__name__ == "Query":
        class_name = ""
    if hasattr(subject, "__class__") and subject.__class__.__name__ == "Query":
        subject = ""

    async def event_generator():
        from backend.app.utils.llm_tracker import request_stats
        request_stats.set({"calls": [], "start_time": time.time(), "query": query})
        uid = get_user_id_or_default(request)
        start_time = time.time()
        print(f"\n============================================================")
        print(f"USER QUESTION (ORCHESTRATOR PATH): '{query}'")
        print(f"============================================================")

        try:
            # 1. Load summary list for grade mapping
            session = session_manager.get_or_create_session(book_uuid, session_id)

            # CRITICAL FIX (found via real-world log analysis, class10_personalization_test_guide.md):
            # the frontend (public/script.js line ~854) has ALWAYS expected a
            # {'type': 'session', 'session_id': ...} event to persist the
            # session across turns, but this endpoint never sent one - so
            # session_id was always null on every request, and
            # get_or_create_session(book_uuid, None) created a BRAND NEW
            # Redis session on every single turn. This silently broke every
            # session-scoped mechanism (repeat-question escalation SS6.3
            # above all - confirmed via real logs showing escalation_level
            # stuck at 0 across 4 turns that should have escalated 0->1->2->0)
            # for the entire life of the app, not just this project's work.
            yield f"data: {json.dumps({'type': 'session', 'session_id': session['session_id']})}\n\n"

            # Extract requested class number from query params as fallback.
            # Use request.query_params directly (more robust across invocation styles).
            fallback_class = 0
            try:
                raw_class_param = request.query_params.get('class_name') or request.query_params.get('class') or class_name or ''
                print(f"[DEBUG] raw_class_param from request.query_params/class_name: {raw_class_param!r}")
                import re
                digits = re.findall(r'\d+', str(raw_class_param))
                print(f"[DEBUG] extracted digits from raw_class_param: {digits!r}")
                if digits:
                    fallback_class = int(digits[0])
                print(f"[DEBUG] parsed fallback_class: {fallback_class}")
            except Exception:
                # Keep fallback_class as 0 on parse error
                pass

            # Get authenticated student profile from Firestore
            print(f"[DEBUG] uid: {uid!r}, fallback_class (before building student_profile): {fallback_class}")
            student_profile = {
                "uid": uid,
                "email": "anonymous@cg.com",
                "name": "Sonu",
                "class": int(fallback_class) if fallback_class is not None else 0,
                "board": "CBSE",
                "role": "student"
            }
            if uid and uid != "anonymous":
                user_doc = db.collection("users").document(uid).get()
                if user_doc.exists:
                    udata = user_doc.to_dict()
                    # Strip all string fields fetched during auth
                    u_class = udata.get("class")
                    parsed_class = fallback_class
                    if u_class is not None:
                        try:
                            if isinstance(u_class, str):
                                digits = re.findall(r'\d+', u_class)
                                parsed_class = int(digits[0]) if digits else fallback_class
                            else:
                                parsed_class = int(u_class)
                        except Exception:
                            pass

                    student_profile = {
                        "uid": uid,
                        "email": str(udata.get("email", "")).strip(),
                        "name": str(udata.get("name", "Sonu")).strip(),
                        "class": int(parsed_class) if parsed_class is not None else int(fallback_class or 0),
                        "board": str(udata.get("board", "CBSE")).strip(),
                        "role": str(udata.get("role", "student")).strip()
                    }

            else:
                student_profile = {
                    "uid": uid,
                    "email": "anonymous@cg.com",
                    "name": "Sonu",
                    "class": int(fallback_class) if fallback_class is not None else 0,
                    "board": "CBSE",
                    "role": "student"
                }

            # 1b. Personalization context (personalized_learning.md SS6.1-SS6.4):
            # profile preferences/quadrant, this-session repeat-question
            # escalation, and this student's OWN semantically-related prior
            # turns. All best-effort and independent of the global cache below
            # (SS6.5 - the two layers are never merged).
            profile_ctx = profile_service.get_profile_context(uid)
            raw_escalation_level = session_manager.get_escalation_level(session["session_id"])
            student_history_hits = qdrant.retrieve_student_history(uid, query)
            student_profile["response_style"] = profile_ctx.get("response_style")
            student_profile["quadrant"] = profile_ctx.get("quadrant")
            student_profile["per_student_history"] = student_history_hits
            # SS2.1: self-reported tough/easy subjects - previously collected
            # by profile_service.set_preferences() but never actually merged
            # into student_profile or read by the prompt. Gap fix.
            student_profile["tough_subjects"] = profile_ctx.get("tough_subjects", [])
            student_profile["easy_subjects"] = profile_ctx.get("easy_subjects", [])

            # Immediate (this-conversation) context, distinct from the
            # semantic long-term memory above - per_student_history only
            # surfaces when the CURRENT query has topic words of its own to
            # search with, so a content-free follow-up ("can you rethink
            # your answer and confirm?") had nothing to resolve against even
            # one turn later. session_manager already stores every turn's
            # raw query/answer in Redis for this exact session
            # (active_context_window) - just never read back into the
            # prompt before. Truncated to keep prompt growth marginal, same
            # pattern per_student_history's summaries already use.
            recent_window = session_manager.get_window(session["session_id"])
            if recent_window:
                last_turn = recent_window[-1]
                student_profile["immediate_prior_turn"] = {
                    "query": last_turn.get("query"),
                    "answer": (last_turn.get("answer") or "")[:250],
                }

            # SS6.3 fix: a basic-phrased question only continues the repeat
            # streak if it's actually ABOUT the same topic as the question
            # that started the streak - not just "also short/simple". Compare
            # via embedding similarity against the same 0.30 bar used for
            # per-student memory (qdrant.STUDENT_HISTORY_MIN_SCORE), rather
            # than assuming any two basic-phrased questions in a row are
            # related just because neither one triggered a topic change.
            streak_anchor = session_manager.get_streak_anchor(session["session_id"])
            is_same_topic_as_streak = True
            if streak_anchor:
                is_same_topic_as_streak = qdrant.text_similarity(query, streak_anchor) >= qdrant.STUDENT_HISTORY_MIN_SCORE

            # BUG FIX (found by hand-tracing turn-by-turn escalation math while
            # writing a new test guide, before any live report of a failure):
            # raw_escalation_level reflects the streak going INTO this turn -
            # it does not yet know whether THIS turn breaks the streak. Without
            # gating on is_same_topic_as_streak, a genuinely fresh, unrelated
            # question right after a long same-topic streak would still be
            # told "student has repeated this 3 times, escalate strongly" -
            # wrong, since the streak was actually just broken. Effective
            # escalation_level fed to the prompt must be 0 whenever this turn
            # itself is off-topic from the streak, even though the session's
            # internal counter (updated by add_turn, after generation) only
            # resets for the NEXT turn.
            escalation_level = raw_escalation_level if is_same_topic_as_streak else 0
            student_profile["escalation_level"] = escalation_level

            # 2. Check global cache hit
            cached = check_global_query_cache(query, student_profile["class"], subject)

            # Decision-trace log line (personalized_learning.md's "how do I
            # check the logs" ask) - one line per turn showing exactly what
            # personalization state was read and applied.
            print(
                f"[PERSONALIZATION TRACE] uid={uid} preference={profile_ctx.get('response_style')} "
                f"quadrant={profile_ctx.get('quadrant')} escalation_level={escalation_level} "
                f"per_student_hits={len(student_history_hits)} global_cache_hit={'YES' if cached else 'NO'}"
            )

            if cached:
                out = cached["orchestrator_output"]
                interactive_url = cached.get("interactive_url")
                cached_video_scenes = cached.get("video_scenes")

                classification = out.get("classification", "CURRICULUM")
                matched_subject = out.get("matched_subject")
                matched_chapter = out.get("matched_chapter")
                format_decision = out.get("format_decision", "QUICK_ANSWER")
                text_script = out.get("text_narration") or ""

                print(f"[CACHE HIT] Reusing cached query payload. Format: {format_decision}")
                yield f"data: {json.dumps({'type': 'intent', 'intent': classification, 'subject': matched_subject, 'chapter': matched_chapter, 'format': format_decision})}\n\n"

                # Stream pre-cached scene scripts in a structured bulleted layout,
                # reusing each scene's already-generated audio_url - no fresh TTS call.
                if format_decision == "VIDEO_REQUIRED" and cached_video_scenes:
                    scenes = cached_video_scenes
                    for idx, s in enumerate(scenes):
                        title = s.get("template_data", {}).get("title") or s.get("template_data", {}).get("heading") or s.get("purpose") or f"Scene {s.get('scene_no')}"
                        script = s.get("teacher_script") or ""
                        audio_url = s.get("audio_url") or ""
                        
                        # Format as markdown bullet point
                        bullet_text = f"- **{title}**: {script}"
                        bullet_text = format_text_explanation(bullet_text)
                        text_chunk = bullet_text + "\n\n"
                        
                        yield f"data: {json.dumps({'display_text': text_chunk, 'audio_url': audio_url})}\n\n"
                        await asyncio.sleep(0.05)
                else:
                    # STANDARD RAG/QUICK_ANSWER OR NON-VIDEO CACHE HIT FLOW
                    text_script = out.get("text_narration") or ""
                    text_script = format_text_explanation(text_script)
                    import re
                    lines = text_script.split('\n')
                    for l_idx, line in enumerate(lines):
                        if not line.strip():
                            yield "data: " + json.dumps({'display_text': '\n'}) + "\n\n"
                            continue
                        
                        sentences = [s.strip() for s in re.split(r'(?<=[.!?à¥¤])\s+', line) if s.strip()]
                        for s_idx, s in enumerate(sentences):
                            prefix = "\n" if (l_idx > 0 and s_idx == 0) else ""
                            yield f"data: {json.dumps({'display_text': prefix + s + ' '})}\n\n"
                            await asyncio.sleep(0.05)
                
                # If a video lesson is ready, yield metadata details
                if format_decision == "VIDEO_REQUIRED" and interactive_url:
                    yield f"data: {json.dumps({'type': 'progress', 'step': 'launching_lesson', 'status': 'complete', 'message': 'Pre-rendered lesson loaded from cache!'})}\n\n"
                    await asyncio.sleep(0.5)
                    
                    cached_lesson_package = {"scenes": cached_video_scenes} if cached_video_scenes else None
                    ready_payload = {
                        "type": "lesson_ready",
                        "lesson_id": interactive_url.split("/")[-2] if "/" in interactive_url else "cached",
                        "lesson_title": "Cached Lesson",
                        "interactive_url": interactive_url,
                        "html_url": interactive_url,
                        "video_url": None,
                        "scene_count": len(cached_video_scenes) if cached_video_scenes else 0,
                        "lesson": cached_lesson_package,
                        "lesson_package": cached_lesson_package
                    }
                    yield f"data: {json.dumps(ready_payload)}\n\n"
                
                # Log query to Firestore user_queries collection
                _query_doc_id = None
                try:
                    # See the matching comment on the fresh-generation path
                    # below for why this exists: text answers otherwise never
                    # get their audio persisted anywhere.
                    answer_audio_url = None
                    if not interactive_url and text_script and tts_model == "sarvam":
                        from backend.app.services.chat.tts_service import synthesize_and_persist_answer_audio
                        answer_audio_url = await synthesize_and_persist_answer_audio(
                            text=text_script,
                            storage_key=f"{uid}_{int(time.time() * 1000)}",
                            language=tts_language,
                            speaker=tts_speaker,
                        )

                    from backend.app.services.analytics import analytics_service
                    _query_doc_id = analytics_service.log_query(
                        uid=uid,
                        class_name=str(student_profile["class"]),
                        subject=subject,
                        chapter_id=0,
                        chapter_name=matched_chapter or "Unknown",
                        query=query,
                        reformulated_query=out.get("reformulated_query", query),
                        mode="text",
                        llm_action=classification,
                        answer_length=len(text_script),
                        query_json_url=None,
                        llm_response=text_script,
                        retrieved_sources=None,  # Cache hits don't execute a fresh search
                        storyboard_data={"scenes": cached_video_scenes} if cached_video_scenes else None,
                        video_url=interactive_url,
                        audio_url=answer_audio_url
                    )

                    # Rebuild/update user analytics (streaks, counts, etc.)
                    analytics_service.update_user_stats(
                        uid=uid,
                        subject=subject,
                        chapter_id=0,
                        class_name=str(student_profile["class"])
                    )
                except Exception as log_err:
                    logger.error(f"[ANALYTICS] Failed to log query to user_queries on cache hit: {log_err}")

                # SS6.2-SS6.4: even on a cache hit, this student did just
                # receive this answer - record it into their own memory and
                # profile signals so a later follow-up still builds on it,
                # and so repeat-question escalation still tracks correctly.
                try:
                    is_basic = profile_service.is_basic_question(query)
                    session_manager.add_turn(session["session_id"], {
                        "query": query,
                        "reformulated": out.get("reformulated_query", query),
                        "answer": text_script,
                        "intent_type": "USE_CACHED_CONTEXT",
                        "is_basic_question": is_basic,
                        "is_same_topic_as_streak": is_same_topic_as_streak,
                        "timestamp": datetime.datetime.now().isoformat()
                    })
                    profile_service.record_turn_signals(
                        uid, student_profile["class"], query,
                        grade_relative_difficulty=out.get("grade_relative_difficulty")
                    )
                    profile_service.compute_quadrant(uid)
                    qdrant.store_student_turn(
                        uid, query, out.get("reformulated_query", query), text_script,
                        student_profile["class"], matched_subject or subject, topic=matched_chapter
                    )
                except Exception as personalization_err:
                    logger.error(f"[PERSONALIZATION] Failed to update per-student state on cache hit: {personalization_err}")

                # SS7's "where do I see the logs" answer: a durable, consolidated
                # JSONL record of this turn (previously this function was only
                # ever called from the older /api/query endpoint, so the log
                # directory went stale - now also written from the live path).
                save_chat_log_background(
                    user_query=query,
                    subject=matched_subject or subject,
                    mode="smart_query_cache_hit",
                    session_id=session["session_id"],
                    llm_response=text_script,
                    execution_time_ms=int((time.time() - start_time) * 1000),
                    uid=uid,
                    personalization={
                        "preference": profile_ctx.get("response_style"),
                        "quadrant": profile_ctx.get("quadrant"),
                        "escalation_level": escalation_level,
                        "per_student_hits": len(student_history_hits),
                        "global_cache_hit": True,
                        "classification": classification,
                        "format_decision": format_decision,
                    }
                )

                # Yield the Firestore document ID so the frontend can attach feedback to this query
                if _query_doc_id:
                    yield f"data: {json.dumps({'type': 'query_id', 'query_id': _query_doc_id})}\n\n"

                yield "data: [DONE]\n\n"
                return

            # 3. Cache Miss: Run Orchestrator Pipeline
            # Run in thread executor so the async event loop is NOT blocked during LLM calls
            # Propagate ContextVars (tracking context) using copy_context().run to fix 0-stats issue
            print(f"[CACHE MISS] Calling single-pass Orchestrator Agent...")
            print(f"[DEBUG] student_profile before orchestrator call: {student_profile}")
            import contextvars
            ctx = contextvars.copy_context()
            loop = asyncio.get_event_loop()
            report = await loop.run_in_executor(
                None,  # uses the default ThreadPoolExecutor
                ctx.run,
                run_orchestrator_pipeline,
                query,
                student_profile
            )
            out = report.get("orchestrator_output", {})
            # Real, Firestore-validated book_uuid for the orchestrator's matched_subject
            # (empty if this is GENERAL_KNOWLEDGE or the match couldn't be resolved) -
            # see get_valid_subjects_for_grade()/resolved_book_uuid in test_runner.py.
            resolved_book_uuid = report.get("resolved_book_uuid", "")

            classification = out.get("classification", "CURRICULUM")
            matched_subject = out.get("matched_subject")
            matched_chapter = out.get("matched_chapter")
            format_decision = out.get("format_decision", "QUICK_ANSWER")
            text_script = out.get("text_narration") or ""

            # Check Child Safety Refusal
            if not out.get("is_authorized", True):
                refusal = out.get("refusal_reason") or "I cannot answer this query."
                yield f"data: {json.dumps({'type': 'intent', 'intent': 'UNAUTHORIZED', 'format': 'QUICK_ANSWER'})}\n\n"
                words = refusal.split(" ")
                for w in words:
                    yield f"data: {json.dumps({'display_text': w + ' '})}\n\n"
                    await asyncio.sleep(0.01)
                yield "data: [DONE]\n\n"
                return

            # Yield classification intent (this tells frontend about subject/chapter metadata for backgrounds)
            yield f"data: {json.dumps({'type': 'intent', 'intent': classification, 'subject': matched_subject, 'chapter': matched_chapter, 'format': format_decision})}\n\n"

            import re

            # If format decision is video required, compile the video lesson asynchronously in the background
            interactive_url = None
            updated_lesson_package = None
            video_scenes_for_cache = None
            if format_decision == "VIDEO_REQUIRED":
                print("[ORCHESTRATOR] Starting Hyperframes video generation (fixed pipeline, fresh storyboard)...")
                from backend.app.services.visual_learning.visual_learning_service import generate_visual_lesson_stream

                # Deliberately do NOT pass the orchestrator's own draft storyboard
                # (out.get("video_storyboard")) - it comes from a single-pass LLM
                # call with no schema guidance, no icon/template registry, and no
                # retry-if-empty guard, and was the source of blank videos. Let
                # generate_visual_lesson_stream do its own real generation instead.
                #
                # Use the validated, Firestore-resolved book for a real curriculum
                # match. For GENERAL_KNOWLEDGE, never fall back to the open book -
                # generate with no book context at all, rather than searching an
                # unrelated one.
                if classification == "CURRICULUM":
                    video_book_uuid = resolved_book_uuid or book_uuid
                    video_subject = matched_subject or subject
                else:
                    video_book_uuid = ""
                    video_subject = matched_subject or subject or "General Knowledge"

                visual_stream = generate_visual_lesson_stream(
                    query=query,
                    book_uuid=video_book_uuid,
                    class_name=str(student_profile["class"]),
                    subject=video_subject,
                    student_profile=student_profile
                )

                streamed_scene_nos = set()
                narration_parts = []

                async for sse_chunk in visual_stream:
                    # Strip 'data: ' prefix if present and parse
                    raw_data = sse_chunk.strip()
                    if raw_data.startswith("data: "):
                        raw_data = raw_data[6:]

                    try:
                        chunk_json = json.loads(raw_data)

                        # The storyboard (scenes + teacher_script) is ready before
                        # audio/compile finish. Ignore it for display purposes -
                        # we stream each scene's text the moment ITS OWN audio is
                        # ready (scene_audio_ready below), not before, so text and
                        # narration always arrive together.
                        if chunk_json.get("type") == "storyboard_ready":
                            continue

                        # A single scene's audio just finished - stream its text
                        # immediately, in a real-time teacher-reading way, instead
                        # of waiting for every other scene's TTS to also finish.
                        # This is what actually gates when the student sees
                        # anything at all, so per-scene delivery here is the fix
                        # for "why does the text answer wait so long".
                        if chunk_json.get("type") == "scene_audio_ready":
                            s = chunk_json.get("scene") or {}
                            scene_no = s.get("scene_no")
                            if scene_no not in streamed_scene_nos:
                                streamed_scene_nos.add(scene_no)
                                title = s.get("template_data", {}).get("title") or s.get("template_data", {}).get("heading") or s.get("purpose") or f"Scene {scene_no}"
                                script = s.get("teacher_script") or ""
                                audio_url = s.get("audio_url") or ""
                                narration_parts.append(script)
                                bullet_text = f"- **{title}**: {script}"
                                bullet_text = format_text_explanation(bullet_text)
                                text_chunk = bullet_text + "\n\n"
                                yield f"data: {json.dumps({'display_text': text_chunk, 'audio_url': audio_url})}\n\n"
                            continue

                        # Batched completion signal - by now every scene should
                        # already have streamed individually above. Only used to
                        # capture the final scene list (with audio_url attached)
                        # for caching; stream any scene that was somehow missed
                        # above (e.g. a late/slow straggler) rather than silently
                        # dropping its text.
                        if chunk_json.get("type") == "audio_ready":
                            scenes = chunk_json.get("scenes", [])
                            video_scenes_for_cache = scenes
                            for s in scenes:
                                scene_no = s.get("scene_no")
                                if scene_no in streamed_scene_nos:
                                    continue
                                streamed_scene_nos.add(scene_no)
                                title = s.get("template_data", {}).get("title") or s.get("template_data", {}).get("heading") or s.get("purpose") or f"Scene {scene_no}"
                                script = s.get("teacher_script") or ""
                                audio_url = s.get("audio_url") or ""
                                narration_parts.append(script)
                                bullet_text = f"- **{title}**: {script}"
                                bullet_text = format_text_explanation(bullet_text)
                                text_chunk = bullet_text + "\n\n"
                                yield f"data: {json.dumps({'display_text': text_chunk, 'audio_url': audio_url})}\n\n"
                            text_script = " ".join(narration_parts)
                            # Explicit "no more scene audio is coming" signal for
                            # the frontend's streaming TTS queue. Without this the
                            # queue has no way to distinguish "genuinely done" from
                            # "just caught up, next scene's audio hasn't arrived
                            # yet" - confirmed live: the queue draining transiently
                            # between scenes was firing the pipeline's onComplete
                            # after only the first scene played, mounting the video
                            # player prematurely and cutting off the rest of the
                            # narration.
                            yield f"data: {json.dumps({'type': 'all_scene_audio_ready'})}\n\n"
                            continue

                        # Internal pipeline step names ("generating storyboard",
                        # "synthesizing voiceovers", etc.) are deliberately not
                        # forwarded to the UI - the student sees narrated text
                        # arrive scene-by-scene above instead of a process log.
                        if chunk_json.get("type") == "progress":
                            continue

                        # Handle ready payload
                        if chunk_json.get("type") == "lesson_ready":
                            interactive_url = chunk_json.get("interactive_url")
                            updated_lesson_package = chunk_json.get("lesson_package")
                            # Prefer the final compiled scenes (now carrying audio_url)
                            # for caching, if available.
                            if updated_lesson_package and updated_lesson_package.get("scenes"):
                                video_scenes_for_cache = updated_lesson_package.get("scenes")
                            yield f"data: {json.dumps(chunk_json)}\n\n"
                    except Exception as json_err:
                        logger.error(f"[SSE FORWARD] Parse error: {json_err} on raw: {raw_data}")
            else:
                # STANDARD QUICK_ANSWER FLOW - stream the orchestrator's own text_narration
                text_script = format_text_explanation(text_script)
                lines = text_script.split('\n')
                for l_idx, line in enumerate(lines):
                    if not line.strip():
                        yield "data: " + json.dumps({'display_text': '\n'}) + "\n\n"
                        continue

                    sentences = [s.strip() for s in re.split(r'(?<=[.!?à¥¤])\s+', line) if s.strip()]
                    for s_idx, s in enumerate(sentences):
                        prefix = "\n" if (l_idx > 0 and s_idx == 0) else ""
                        yield f"data: {json.dumps({'display_text': prefix + s + ' '})}\n\n"
                        await asyncio.sleep(0.05)

            # Compile query transaction JSON payload for Supabase Cloud Storage
            import uuid
            query_id = f"q_{uuid.uuid4().hex[:8]}"
            
            # Construct unified transaction package
            transaction_payload = {
                "query_id": query_id,
                "session_id": session["session_id"],
                "uid": uid,
                "class": student_profile["class"],
                "subject": subject,
                "query": query,
                "reformulated_query": out.get("reformulated_query", query),
                "classification": classification,
                "format_decision": format_decision,
                "text_narration": text_script,
                "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                "video_storyboard": updated_lesson_package,
                "media_urls": {
                    "interactive_url": interactive_url,
                    "storyboard_json_url": (updated_lesson_package or {}).get("storyboard_json_url") if updated_lesson_package else None
                }
            }
            
            # Create local user history directory
            import os
            ROUTE_DIR = os.path.dirname(os.path.abspath(__file__))
            PROJECT_ROOT = os.path.abspath(os.path.join(ROUTE_DIR, "..", "..", "..", ".."))
            user_history_dir = os.path.join(PROJECT_ROOT, "uploads", "user_history", uid)
            os.makedirs(user_history_dir, exist_ok=True)
            
            transaction_file_path = os.path.join(user_history_dir, f"{query_id}.json")
            query_json_url = None
            try:
                with open(transaction_file_path, "w", encoding="utf-8") as f:
                    json.dump(transaction_payload, f, indent=2, ensure_ascii=False)
                
                # Upload transaction JSON to Supabase Cloud Storage
                from backend.app.core.supabase_storage import upload_file_to_supabase
                query_json_url = upload_file_to_supabase(
                    transaction_file_path,
                    f"user_history/{uid}/{query_id}.json"
                )
                
                if query_json_url:
                    transaction_payload["query_json_url"] = query_json_url
                    # Update local copy with public URL
                    with open(transaction_file_path, "w", encoding="utf-8") as f:
                        json.dump(transaction_payload, f, indent=2, ensure_ascii=False)
            except Exception as store_err:
                logger.error(f"[History Logger] Failed to save/upload transaction JSON: {store_err}")
            finally:
                # Clean up local temporary file to save space
                if os.path.exists(transaction_file_path):
                    try:
                        os.remove(transaction_file_path)
                    except Exception:
                        pass

            # Register compiled query results to the global cache
            save_to_global_query_cache(
                raw_query=query,
                class_name=student_profile["class"],
                subject=subject,
                orchestrator_output=out,
                interactive_url=interactive_url,
                video_scenes=video_scenes_for_cache
            )

            # Save query turn to standard chat session manager
            turn_data = {
                "query": query,
                "reformulated": out.get("reformulated_query", query),
                "answer": text_script,
                "intent_type": classification,
                "is_clicked_followup": is_clicked_followup,
                "is_basic_question": profile_service.is_basic_question(query),
                "is_same_topic_as_streak": is_same_topic_as_streak,
                "timestamp": datetime.datetime.now().isoformat()
            }
            session_manager.add_turn(session["session_id"], turn_data)

            # SS6.2/SS6.4: update this student's running skill/engagement
            # signals and quadrant, and store this turn as retrievable
            # per-student memory for future follow-ups. Best-effort - must
            # never fail the response itself.
            try:
                profile_service.record_turn_signals(
                    uid, student_profile["class"], query,
                    grade_relative_difficulty=out.get("grade_relative_difficulty")
                )
                profile_service.compute_quadrant(uid)
                qdrant.store_student_turn(
                    uid, query, out.get("reformulated_query", query), text_script,
                    student_profile["class"], matched_subject or subject, topic=matched_chapter
                )
            except Exception as personalization_err:
                logger.error(f"[PERSONALIZATION] Failed to update per-student state: {personalization_err}")

            # SS7's "where do I see the logs" answer - see the matching call
            # on the cache-hit path above for why this exists.
            save_chat_log_background(
                user_query=query,
                subject=matched_subject or subject,
                mode="smart_query_fresh",
                session_id=session["session_id"],
                llm_response=text_script,
                execution_time_ms=int((time.time() - start_time) * 1000),
                uid=uid,
                personalization={
                    "preference": profile_ctx.get("response_style"),
                    "quadrant": profile_ctx.get("quadrant"),
                    "escalation_level": escalation_level,
                    "per_student_hits": len(student_history_hits),
                    "global_cache_hit": False,
                    "grade_relative_difficulty": out.get("grade_relative_difficulty"),
                    "classification": classification,
                    "format_decision": format_decision,
                }
            )

            # Log query to Firestore user_queries collection
            _query_doc_id = None
            try:
                # Format RAG chunks for storage
                retrieved_sources = []
                chunks = report.get("retrieved_top10_chunks", [])
                if chunks:
                    for chunk in chunks:
                        retrieved_sources.append({
                            "chunk_id": f"chunk_{chunk.get('chunk_index')}",
                            "chapter_name": chunk.get("chapter_name") or "Unknown",
                            "text": chunk.get("content_snippet", ""),
                            "score": float(chunk.get("score", 0.0)),
                            "page_number": chunk.get("page_number") or 1
                        })

                # Text-only answers (video answers already have real saved
                # narration per-scene, see storyboard_data) never had their
                # audio persisted anywhere - replaying one from history would
                # otherwise mean either silence or a fresh billed TTS call
                # every single time it's revisited. Only "sarvam" has a
                # caching layer to make this cheap; Azure/browser voices have
                # nothing persistable to save (see synthesize_and_persist_answer_audio).
                answer_audio_url = None
                if not interactive_url and text_script and tts_model == "sarvam":
                    from backend.app.services.chat.tts_service import synthesize_and_persist_answer_audio
                    answer_audio_url = await synthesize_and_persist_answer_audio(
                        text=text_script,
                        storage_key=f"{uid}_{int(time.time() * 1000)}",
                        language=tts_language,
                        speaker=tts_speaker,
                    )

                from backend.app.services.analytics import analytics_service
                _query_doc_id = analytics_service.log_query(
                    uid=uid,
                    class_name=str(student_profile["class"]),
                    subject=subject,
                    chapter_id=0,
                    chapter_name=matched_chapter or "Unknown",
                    query=query,
                    reformulated_query=out.get("reformulated_query", query),
                    mode="text",
                    llm_action=classification,
                    answer_length=len(text_script),
                    query_json_url=query_json_url,
                    llm_response=text_script,
                    retrieved_sources=retrieved_sources,
                    storyboard_data=updated_lesson_package,
                    video_url=interactive_url,
                    audio_url=answer_audio_url
                )

                # Rebuild/update user analytics (streaks, counts, etc.)
                analytics_service.update_user_stats(
                    uid=uid,
                    subject=subject,
                    chapter_id=0,
                    class_name=str(student_profile["class"])
                )
            except Exception as log_err:
                logger.error(f"[ANALYTICS] Failed to log query to user_queries: {log_err}")

            # Yield the Firestore document ID so the frontend can attach feedback to this query
            if _query_doc_id:
                yield f"data: {json.dumps({'type': 'query_id', 'query_id': _query_doc_id})}\n\n"

            yield "data: [DONE]\n\n"
            from backend.app.utils.llm_tracker import print_query_performance_report
            print_query_performance_report()

        except Exception as e:
            logger.error(f"[ORCHESTRATE ROUTE ERROR] Failed: {e}", exc_info=True)
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            from backend.app.utils.llm_tracker import print_query_performance_report
            print_query_performance_report()
    
    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/api/session/history", tags=["Session"])
async def get_session_history(session_id: str = Query(...)):
    """
    Returns complete chat history with metadata for a given session.
    """
    from backend.app.core.redis_service import redis_service
    session = redis_service.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    
    total_turns = len(session.get("full_history", []))
    cache_hits = sum(1 for turn in session.get("full_history", []) if turn.get("intent_type") == "USE_CACHED_CONTEXT")
    cache_hit_rate = (cache_hits / total_turns * 100) if total_turns > 0 else 0
    
    return {
        "session_id": session_id,
        "book_uuid": session.get("book_uuid"),
        "created_at": session.get("created_at"),
        "last_updated": session.get("last_updated"),
        "statistics": {
            "total_turns": total_turns,
            "cache_hits": cache_hits,
            "cache_hit_rate": round(cache_hit_rate, 1),
            "total_topics": len(session.get("topics", []))
        },
        "topics": session.get("topics", []),
        "current_topic_id": session.get("current_topic_id"),
        "full_history": session.get("full_history", []),
        "active_context_window": session.get("active_context_window", [])
    }


@router.get("/api/session/chunks", tags=["Session"])
async def get_current_chunks(session_id: str = Query(...)):
    """
    Returns currently cached chunks for the active topic.
    """
    chunks = session_manager.get_current_topic_chunks(session_id)
    if not chunks:
        return {"chunks": [], "total_count": 0, "message": "No cached chunks for current topic"}
    
    formatted_chunks = []
    for score, doc in chunks:
        formatted_chunks.append({
            "relevance_score": round(score, 4),
            "chapter_name": doc.get("chapter_name", "Unknown"),
            "text": doc.get("text", ""),
            "text_length": len(doc.get("text", "")),
            "pdf_pages": f"{doc.get('pdf_startpg', '?')}-{doc.get('pdf_endpg', '?')}",
            "chapter_pages": f"{doc.get('chpstpage', '?')}-{doc.get('chpendpage', '?')}"
        })
    
    return {
        "chunks": formatted_chunks,
        "total_count": len(formatted_chunks),
        "message": f"Currently using {len(formatted_chunks)} cached chunks"
    }


@router.post("/api/summarize")
async def get_summary(request: SummaryRequest):
    """
    Generates a teacher-like explanation for a specific chapter of a book.
    """
    class_name = request.class_name
    subject = request.subject
    chapter_name = request.chapter_name

    summary_doc = firestore_service.load_summary_from_firestore(class_name, subject)
    
    chapter_summary = None
    if summary_doc and "chapters" in summary_doc:
        for chap in summary_doc["chapters"]:
            if chap.get("chapter_name") == chapter_name:
                chapter_summary = chap.get("summary")
                break
    
    if chapter_summary is None or chapter_summary == "":
        raise HTTPException(status_code=404, detail="Summary not found for this chapter or is being generated.")

    explanation = generate_teacher_explanation(
        class_name=class_name,
        subject=subject,
        chapter_name=chapter_name,
        summary_text=chapter_summary
    )
    
    return {"summary": explanation}


@router.websocket("/ws/conversation/{conversation_id}")
async def websocket_conversation(
    websocket: WebSocket,
    conversation_id: str,
    book_uuid: str,
    uid: str = Query(None),
    class_name: str = Query(None),
    subject: str = Query(None)
):
    """
    Handles conversational voice/text WebSockets with dynamic interruption support.
    """
    await conversation_manager.connect(websocket, conversation_id, book_uuid, uid, class_name, subject)
    print(f"[App] WebSocket handler started for conversation_id={conversation_id}, book_uuid={book_uuid}, uid={uid}, class_name={class_name}, subject={subject}")
    try:
        while True:
            data = await websocket.receive_json()
            print(f"[App] Received WS message for {conversation_id}: {str(data)[:200]}")
            
            if data.get("type") == "query":
                print(f"[App] Dispatching 'query' to ConversationManager for {conversation_id}")
                asyncio.create_task(conversation_manager.process_query(conversation_id, data.get("query", "")))
            elif data.get("type") == "interrupt":
                print(f"[App] Received 'interrupt' for {conversation_id}")
                await conversation_manager.interrupt(conversation_id)
    
    except WebSocketDisconnect:
        print(f"[App] WebSocket disconnected for conversation_id={conversation_id}")
    except Exception as exc:
        print(f"[App] WebSocket error for {conversation_id}: {exc}")
    finally:
        conversation_manager.disconnect(conversation_id)


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# STUDENT FEEDBACK ENDPOINT
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.post("/api/feedback")
async def submit_feedback(request: FeedbackRequest):
    """
    Save student feedback (like / dislike + optional voice transcript)
    back to the matching nested user queries Firestore document.
    """
    try:
        from google.cloud import firestore as _fs

        if not request.uid:
            # The old approach here used a Firestore collection-group query
            # filtered by FieldPath.document_id() == a bare string - that's
            # exactly what "__key__ filter value must be a Key" means:
            # Firestore requires a full document reference for that filter on
            # a collection-group query, not a plain ID. The fix is to just
            # look the document up directly (needs uid), not to scan every
            # user's query history for a matching ID (expensive AND still
            # avoidable now that every caller already knows its own uid).
            logger.error(f"[FEEDBACK] Request for query {request.query_id} missing uid - cannot locate document.")
            raise HTTPException(status_code=400, detail="uid is required to save feedback.")

        doc_ref = db.collection("users").document(request.uid).collection("queries").document(request.query_id)
        query_snapshot = doc_ref.get()
        if not query_snapshot.exists:
            logger.error(f"[FEEDBACK] Query document {request.query_id} not found for uid={request.uid}.")
            raise HTTPException(status_code=404, detail="Query document not found.")
        query_data = query_snapshot.to_dict() or {}
        doc_ref.update({
            "feedback": {
                "type": request.feedback_type,
                "text": request.feedback_text or "",
                "timestamp": _fs.SERVER_TIMESTAMP
            }
        })
        logger.info(f"[FEEDBACK] Saved '{request.feedback_type}' for query {request.query_id} at {doc_ref.path}")

        # SS2.1 lists feedback as an engagement signal for the skill x
        # engagement quadrant - previously recorded to Firestore but never
        # read back into personalization. doc path is users/{uid}/queries/{id},
        # so the uid is the queries collection's parent document.
        try:
            feedback_uid = doc_ref.parent.parent.id
            profile_service.record_feedback_signal(feedback_uid, is_positive=(request.feedback_type == "like"))
            profile_service.compute_quadrant(feedback_uid)
        except Exception as fb_signal_err:
            logger.error(f"[FEEDBACK] Failed to update personalization signals: {fb_signal_err}")

        # Beyond the aggregate quadrant signal above, remember WHAT was liked/
        # disliked so a future related question can actually adapt, not just
        # shift tone - see qdrant_service.store_feedback_note. This is a
        # topic-relevant memory point, not a blind counter: it resurfaces via
        # the same semantic search that already powers per_student_history
        # whenever this student asks something related later.
        try:
            qdrant.store_feedback_note(
                uid=request.uid,
                question=query_data.get("query") or "",
                class_name=query_data.get("class"),
                subject=query_data.get("subject"),
                topic=query_data.get("chapter_name"),
                is_positive=(request.feedback_type == "like"),
                reason=request.feedback_text or "",
            )
        except Exception as fb_note_err:
            logger.error(f"[FEEDBACK] Failed to store feedback memory note: {fb_note_err}")

        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[FEEDBACK] Failed to save feedback: {e}")
        raise HTTPException(status_code=500, detail="Failed to save feedback.")

