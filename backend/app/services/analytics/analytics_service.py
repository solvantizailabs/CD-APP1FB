"""
Analytics Service for CHADUVU-GURU
Handles all analytics logging and aggregation to Firestore.
"""

from google.cloud import firestore
from backend.app.core.firebase.firebase_init import db
import logging
from datetime import datetime, timezone, timedelta
import pytz
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)

def same_local_day(ts_old, ts_new, timezone_str="Asia/Kolkata"):
    tz = pytz.timezone(timezone_str)
    return ts_old.astimezone(tz).date() == ts_new.astimezone(tz).date()


def _sanitize_for_firestore(value):
    """
    Firestore rejects an array that directly contains another array (arrays
    of dicts/scalars are fine; dicts containing arrays are fine) - confirmed
    root cause of a real data-loss bug where an entire query doc failed to
    save because one video scene's template_data had a "connections" field
    shaped like [[0,1],[1,2]] (coordinate pairs for a diagram template).
    Recursively rewrites any such nested list into a list of {"_list": [...]}
    wrapper dicts so the write never fails on this, for this template or any
    future one with the same shape.
    """
    if isinstance(value, list):
        return [
            {"_list": _sanitize_for_firestore(item)} if isinstance(item, list) else _sanitize_for_firestore(item)
            for item in value
        ]
    elif isinstance(value, dict):
        return {k: _sanitize_for_firestore(v) for k, v in value.items()}
    return value

# ============================================
# PHASE 1: FIRESTORE COLLECTION SCHEMAS
# ============================================

"""
Collections created by this service:

1. user_queries - Individual query logs
   Document: auto-generated ID
   Fields: uid, class, subject, chapter_id, chapter_name, query, 
           reformulated_query, mode, llm_action, timestamp, 
           answer_length, ai_difficulty_score

2. user_stats - Aggregated user statistics
   Document ID: {uid}
   Fields: total_queries, last_active, streak, subjects_count,
           chapters_count, weekly_activity, average_difficulty

3. chapter_stats - Chapter analytics
   Document ID: {class}_{subject}_{chapter_id}
   Fields: class, subject, chapter_id, chapter_name, total_queries,
           unique_students, avg_difficulty, last_asked

4. student_mistakes - Learning patterns
   Document ID: {uid}
   Fields: patterns, confusion_topics, recommended_tasks

5. saved_notes - My Bag feature
   Document ID: {uid}
   Fields: notes (array of {title, content, createdAt})
"""

# ============================================
# CORE ANALYTICS FUNCTIONS
# ============================================

def log_query(
    uid: str,
    class_name: str,
    subject: str,
    chapter_id: Optional[int],
    chapter_name: Optional[str],
    query: str,
    reformulated_query: str,
    mode: str,
    llm_action: str,
    answer_length: int,
    format_decision: Optional[str] = None,
    ai_difficulty_score: Optional[float] = None,
    query_json_url: Optional[str] = None,
    llm_response: Optional[str] = None,
    retrieved_sources: Optional[List[Dict]] = None,
    storyboard_data: Optional[Dict] = None,
    video_url: Optional[str] = None,
    audio_url: Optional[str] = None
) -> str:
    """
    Logs a single user query and full LLM response details to the users/{uid}/queries collection.
    """
    logger.info(f"[ANALYTICS] Logging query for user {uid} in subject {subject}")
    try:
        # Parse class to integer
        class_int = 0
        try:
            if class_name:
                class_int = int(str(class_name).replace("Class", "").replace("class", "").strip())
        except (ValueError, AttributeError):
            logger.warning(f"Could not parse class: {class_name}, defaulting to 0")

        # Ensure subject is a string. "all" is the frontend's generic
        # chat-mode filter value, not a real classification - try to resolve
        # a real subject from the chapter registry / keyword heuristic before
        # saving, so this query doesn't need read-time guessing later (see
        # backend/app/core/subject_classifier.py for how history.py handles
        # the queries that were already saved this way before this existed).
        safe_subject = subject.lower().strip() if isinstance(subject, str) else "unknown"
        if safe_subject in ("all", "unknown", ""):
            try:
                from backend.app.core.subject_classifier import resolve_subject
                safe_subject = resolve_subject(class_name, chapter_name, query, safe_subject)
            except Exception as classify_err:
                logger.warning(f"Subject resolution fallback failed, keeping '{safe_subject}': {classify_err}")
        
        # Generate chronological and readable document ID
        timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        doc_id = f"{timestamp_str}_class{class_int}"
        
        doc_ref = db.collection("users").document(uid).collection("queries").document(doc_id)

        query_data = {
            "query": query,
            "reformulated_query": reformulated_query,
            "llm_response": llm_response,
            "class": class_int,
            "subject": safe_subject,
            "chapter_id": chapter_id if chapter_id is not None else 0,
            "chapter_name": chapter_name or "Unknown",
            "mode": mode,
            "llm_action": llm_action,
            # §9.2.C (docs/RAG_INTEGRATION_PLAN.md): previously only reachable
            # via the linked query_json_url JSON, not visible from a glance at
            # the Firestore console itself - now a first-class field alongside
            # llm_action/classification, same visibility level.
            "format_decision": format_decision,
            "timestamp": firestore.SERVER_TIMESTAMP,
            "answer_length": answer_length,
            "retrieved_sources": retrieved_sources or [],
            "storyboard_data": _sanitize_for_firestore(storyboard_data) if storyboard_data else storyboard_data,
            "video_url": video_url,
            "audio_url": audio_url
        }

        if ai_difficulty_score is not None:
            query_data["ai_difficulty_score"] = ai_difficulty_score

        if query_json_url:
            query_data["query_json_url"] = query_json_url

        try:
            doc_ref.set(query_data)
        except Exception as write_err:
            # The sanitizer above handles the known failure mode (nested
            # arrays inside a scene's template_data - see
            # _sanitize_for_firestore). This is a last-resort safety net for
            # anything else Firestore might reject about storyboard_data in
            # the future: retry WITHOUT it rather than silently losing the
            # entire question. A missing storyboard_data means "the video's
            # own scene metadata isn't in this doc" (video_url/audio_url are
            # untouched, so playback and replay are unaffected) - it does not
            # mean the question was never asked or answered.
            logger.error(f"❌ Firestore write failed with storyboard_data included, retrying without it: {write_err}")
            query_data["storyboard_data"] = None
            doc_ref.set(query_data)
            logger.warning(f"⚠️ Query logged WITHOUT storyboard_data (see error above): {doc_ref.id}")

        logger.info(f"✅ Query logged to user queries subcollection: {doc_ref.id}")
        return doc_ref.id

    except Exception as e:
        logger.error(f"❌ Failed to log query: {e}")
        raise


def rebuild_user_analytics_from_queries(uid: str) -> Dict:
    """
    Rebuild complete user analytics from the users/{uid}/queries subcollection.
    This is the single source of truth for dashboard statistics.
    
    Computes:
    - total_queries: Total count of all queries
    - subjects_count: Dict mapping subject -> count
    - subjects_explored: Number of unique subjects
    - chapters_count: Dict mapping chapter_key -> count
    - daily_activity: Dict mapping "YYYY-MM-DD" -> count
    - weekly_activity: Last 7 days ordered oldest→newest
    - last_active: ISO timestamp string of most recent query
    - streak: Consecutive days ending today
    - longest_streak: Longest historical consecutive streak
    
    Args:
        uid: User ID
    
    Returns:
        Dictionary with aggregated analytics
    """
    logger.info(f"[REBUILD ANALYTICS] Starting for uid: {uid}")
    try:
        # Fetch all queries for this user
        queries_ref = db.collection("users").document(uid).collection("queries").stream()
        
        # Initialize aggregators
        total_queries = 0
        subjects_count = {}
        chapters_count = {}
        daily_activity = {}
        timestamps = []
        
        for query_doc in queries_ref:
            data = query_doc.to_dict()
            total_queries += 1
            
            # Subject counting
            subject = data.get("subject", "unknown")
            subjects_count[subject] = subjects_count.get(subject, 0) + 1
            
            # Chapter counting  
            class_val = data.get("class", 0)
            chapter_id = data.get("chapter_id", 0)
            chapter_key = f"{class_val}_{subject}_{chapter_id}"
            chapters_count[chapter_key] = chapters_count.get(chapter_key, 0) + 1
            
            # Daily activity and timestamp tracking
            timestamp = data.get("timestamp")
            if timestamp:
                # Convert to UTC datetime
                if hasattr(timestamp, 'strftime'):
                    ts_dt = timestamp
                else:
                    ts_dt = datetime.fromisoformat(str(timestamp))
                
                # Store for streak calculation
                timestamps.append(ts_dt)
                
                # Daily activity in UTC
                date_str = ts_dt.strftime("%Y-%m-%d")
                daily_activity[date_str] = daily_activity.get(date_str, 0) + 1
        
        # Subjects explored
        subjects_explored = len(subjects_count)
        
        # Last active
        last_active = None
        if timestamps:
            timestamps.sort(reverse=True)
            last_active = timestamps[0].isoformat()
        
        # Weekly activity - last 7 days
        today = datetime.now(timezone.utc)
        weekly_activity = {}
        for i in range(6, -1, -1):  # 6 days ago to today
            date = today - timedelta(days=i)
            date_str = date.strftime("%Y-%m-%d")
            weekly_activity[date_str] = daily_activity.get(date_str, 0)
        
        # Streak calculation
        streak = 0
        longest_streak = 0
        
        if timestamps:
            # Get unique dates sorted descending
            unique_dates = sorted(set(ts.date() for ts in timestamps), reverse=True)
            today_date = datetime.now(timezone.utc).date()
            
            # Calculate current streak
            temp_streak = 0
            for i, date in enumerate(unique_dates):
                if i == 0:
                    # Check if most recent is today or yesterday
                    diff = (today_date - date).days
                    if diff <= 1:
                        temp_streak = 1
                    else:
                        break  # No current streak
                else:
                    prev_date = unique_dates[i - 1]
                    if (prev_date - date).days == 1:
                        temp_streak += 1
                    else:
                        break
            
            streak = temp_streak
            
            # Calculate longest streak
            current_run = 1
            for i in range(1, len(unique_dates)):
                if (unique_dates[i - 1] - unique_dates[i]).days == 1:
                    current_run += 1
                    longest_streak = max(longest_streak, current_run)
                else:
                    current_run = 1
            
            longest_streak = max(longest_streak, streak, 1)
        
        result = {
            "total_queries": total_queries,
            "subjects_count": subjects_count,
            "subjects_explored": subjects_explored,
            "chapters_count": chapters_count,
            "daily_activity": daily_activity,
            "weekly_activity": weekly_activity,
            "last_active": last_active,
            "streak": streak,
            "longest_streak": longest_streak
        }
        
        logger.info(f"✅ Rebuilt analytics for {uid}: {total_queries} queries, {subjects_explored} subjects, {streak}-day streak")
        return result
        
    except Exception as e:
        logger.error(f"❌ Failed to rebuild analytics for {uid}: {e}", exc_info=True)
        # Return empty analytics on error
        return {
            "total_queries": 0,
            "subjects_count": {},
            "subjects_explored": 0,
            "chapters_count": {},
            "daily_activity": {},
            "weekly_activity": {},
            "last_active": None,
            "streak": 0,
            "longest_streak": 0
        }


def rebuild_and_cache_user(uid: str) -> Dict:
    """
    Rebuild analytics and write back to user_stats using merge=True.
    This caches the computed results for faster access.
    
    Args:
        uid: User ID
    
    Returns:
        Rebuilt analytics dictionary
    """
    logger.info(f"[CACHE REBUILD] Rebuilding and caching for uid: {uid}")
    try:
        summary = rebuild_user_analytics_from_queries(uid)
        
        # Write to user_stats with merge
        doc_ref = db.collection("user_stats").document(uid)
        doc_ref.set(summary, merge=True)
        
        logger.info(f"✅ Cached analytics for {uid}")
        return summary
        
    except Exception as e:
        logger.error(f"❌ Failed to cache analytics for {uid}: {e}")
        raise


def update_user_stats(
    uid: str,
    subject: str,
    chapter_id: Optional[int],
    class_name: str
) -> None:
    """
    Updates aggregated user statistics with atomic operations.
    NOW ONLY INCREMENTS total_queries - does NOT overwrite daily_activity.
    
    Args:
        uid: User ID
        subject: Subject name
        chapter_id: Chapter ID (optional)
        class_name: Class name
    """
    try:
        # Parse class to integer
        class_int = 0
        try:
            if class_name:
                class_int = int(str(class_name).replace("Class", "").replace("class", "").strip())
        except (ValueError, AttributeError):
            pass

        stats_doc_id = f"{uid}_{class_int}"
        doc_ref = db.collection("user_stats").document(stats_doc_id)
        
        # ONLY increment total_queries, do NOT touch daily_activity
        update_data = {
            "total_queries": firestore.Increment(1),
            "last_active": firestore.SERVER_TIMESTAMP
        }
        
        doc_ref.set(update_data, merge=True)
        logger.info(f"✅ Incremented total_queries for {uid}")
        
    except Exception as e:
        logger.error(f"❌ Failed to update user stats: {e}", exc_info=True)
        # Don't raise - analytics failure shouldn't break query flow


def update_chapter_stats(
    class_name: str,
    subject: str,
    chapter_id: int,
    chapter_name: str,
    uid: str
) -> None:
    """
    Updates chapter-level analytics with atomic operations.
    """
    try:
        class_int = 0
        try:
            if class_name:
                class_int = int(str(class_name).replace("Class", "").replace("class", "").strip())
        except (ValueError, AttributeError):
            pass

        safe_subject = subject.lower() if isinstance(subject, str) else "unknown"
        
        # Centralized Content Path: classes/{class}/subjects/{subject}/stats/{chapter_id}
        doc_ref = db.collection("classes").document(str(class_int))\
                    .collection("subjects").document(safe_subject)\
                    .collection("stats").document(str(chapter_id))
        
        doc = doc_ref.get()
        
        if not doc.exists:
            doc_ref.set({
                "class": class_int,
                "subject": safe_subject,
                "chapter_id": chapter_id,
                "chapter_name": chapter_name,
                "total_queries": 1,
                "unique_students": [uid],
                "avg_difficulty": 0.0,
                "last_asked": firestore.SERVER_TIMESTAMP
            })
            logger.info(f"✅ Created new chapter stats for {class_int}_{safe_subject}_{chapter_id}")
        else:
            update_data = {
                "total_queries": firestore.Increment(1),
                "unique_students": firestore.ArrayUnion([uid]),
                "last_asked": firestore.SERVER_TIMESTAMP
            }
            doc_ref.update(update_data)
            logger.info(f"✅ Updated chapter stats for {class_int}_{safe_subject}_{chapter_id}")
            
    except Exception as e:
        logger.error(f"❌ Failed to update chapter stats: {e}")
        raise


def update_mistake_patterns(
    uid: str,
    patterns: Optional[List[str]] = None,
    confusion_topics: Optional[List[str]] = None,
    recommended_tasks: Optional[List[str]] = None
) -> None:
    """
    Updates student mistake patterns and learning recommendations.
    """
    try:
        # Centralized User Path: users/{uid}/mistakes/mistakes_doc
        doc_ref = db.collection("users").document(uid).collection("mistakes").document("mistakes_doc")
        
        update_data = {}
        
        if patterns:
            update_data["patterns"] = firestore.ArrayUnion(patterns)
        
        if confusion_topics:
            update_data["confusion_topics"] = firestore.ArrayUnion(confusion_topics)
        
        if recommended_tasks:
            update_data["recommended_tasks"] = firestore.ArrayUnion(recommended_tasks)
        
        if update_data:
            doc_ref.set(update_data, merge=True)
            logger.info(f"✅ Mistake patterns updated for {uid}")
        
    except Exception as e:
        logger.error(f"❌ Failed to update mistake patterns for {uid}: {e}")
        raise


# ============================================
# NOTES MANAGEMENT (MY BAG FEATURE)
# ============================================

def add_note(uid: str, title: str, content: str) -> None:
    """
    Adds a note to user's saved notes.
    
    Args:
        uid: User ID
        title: Note title
        content: Note content
    """
    try:
        doc_ref = db.collection("saved_notes").document(uid)
        
        note = {
            "title": title,
            "content": content,
            "createdAt": firestore.SERVER_TIMESTAMP
        }
        
        doc_ref.set({
            "notes": firestore.ArrayUnion([note])
        }, merge=True)
        
        logger.info(f"✅ Note added for user {uid}: {title}")
        
    except Exception as e:
        logger.error(f"❌ Failed to add note for {uid}: {e}")
        raise


def get_notes(uid: str) -> List[Dict]:
    """
    Retrieves all notes for a user.
    
    Args:
        uid: User ID
    
    Returns:
        List of note dictionaries
    """
    try:
        doc_ref = db.collection("saved_notes").document(uid)
        doc = doc_ref.get()
        
        if doc.exists:
            data = doc.to_dict()
            notes = data.get("notes", [])
            logger.info(f"✅ Retrieved {len(notes)} notes for user {uid}")
            return notes
        else:
            logger.info(f"No notes found for user {uid}")
            return []
            
    except Exception as e:
        logger.error(f"❌ Failed to get notes for {uid}: {e}")
        raise


def delete_note(uid: str, note_index: int) -> None:
    """
    Deletes a note by index.
    
    Args:
        uid: User ID
        note_index: Index of note to delete (0-based)
    """
    try:
        doc_ref = db.collection("saved_notes").document(uid)
        doc = doc_ref.get()
        
        if doc.exists:
            data = doc.to_dict()
            notes = data.get("notes", [])
            
            if 0 <= note_index < len(notes):
                notes.pop(note_index)
                doc_ref.update({"notes": notes})
                logger.info(f"✅ Note {note_index} deleted for user {uid}")
            else:
                logger.warning(f"Invalid note index {note_index} for user {uid}")
        else:
            logger.warning(f"No notes document found for user {uid}")
            
    except Exception as e:
        logger.error(f"❌ Failed to delete note for {uid}: {e}")
        raise


# ============================================
# HELPER FUNCTIONS
# ============================================

def get_user_stats(uid: str) -> Optional[Dict]:
    """
    Retrieves user statistics.
    """
    try:
        doc_ref = db.collection("users").document(uid).collection("stats").document("stats_doc")
        doc = doc_ref.get()
        
        if doc.exists:
            return doc.to_dict()
        else:
            return None
            
    except Exception as e:
        logger.error(f"❌ Failed to get user stats for {uid}: {e}")
        return None


def get_chapter_stats(class_name: str, subject: str, chapter_id: int) -> Optional[Dict]:
    """
    Retrieves chapter statistics.
    """
    try:
        class_int = int(class_name.replace("Class", "").replace("class", "").strip())
        doc_ref = db.collection("classes").document(str(class_int))\
                    .collection("subjects").document(subject.lower())\
                    .collection("stats").document(str(chapter_id))
        doc = doc_ref.get()
        
        if doc.exists:
            return doc.to_dict()
        else:
            return None
            
    except Exception as e:
        logger.error(f"❌ Failed to get chapter stats: {e}")
        return None


logger.info("✅ Analytics service loaded successfully")
