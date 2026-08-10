"""
Smart session management with full conversational context.
Maintains complete conversation history with automatic topic segmentation.
"""
from datetime import datetime
from typing import Dict, List, Optional
import time
import uuid

from backend.app.core.redis_service import redis_service


class SmartSessionManager:
    """
    Manages conversation sessions with full context and topic awareness.
    Each session is tied to a specific book (book_uuid).
    Uses Redis for persistent session storage.
    """
    
    def __init__(self):
        self.ttl = 86400  # 24 hours session timeout for Redis

    def get_or_create_session(self, book_uuid: str, session_id: Optional[str] = None) -> dict:
        """
        Get existing session from Redis or create a new one.
        
        Args:
            book_uuid: UUID of the current book
            session_id: Optional existing session ID
        
        Returns:
            Session dictionary with full history and topic tracking
        """
        if session_id:
            session = redis_service.get_session(session_id)
            if session:
                # Validate book match - if book changed, create new session
                if session["book_uuid"] != book_uuid:
                    print(f"[SESSION] Book changed from {session['book_uuid'][:16]}... to {book_uuid[:16]}...")
                    print(f"[SESSION] Creating new session for new book")
                    return self.get_or_create_session(book_uuid, None)
                
                print(f"[SESSION] Reusing existing session from Redis: {session_id}")
                return session

        # Create new session if no ID provided or session doesn't exist in Redis
        session_id = f"{book_uuid}_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        
        # Initialize first topic
        first_topic_id = f"topic_{uuid.uuid4().hex[:8]}"
        
        session = {
            "session_id": session_id,
            "book_uuid": book_uuid,
            "full_history": [],
            "active_context_window": [],
            "topics": [
                {
                    "topic_id": first_topic_id,
                    "topic_name": "Initial Topic",
                    "started_at": datetime.now().isoformat(),
                    "turns": []
                }
            ],
            "current_topic_id": first_topic_id,
            "current_topic_chunks": None,
            "created_at": datetime.now().isoformat(),
            "last_updated": datetime.now().isoformat(),
        }
        redis_service.save_session(session_id, session, ttl=self.ttl)
        print(f"[SESSION] Created new session in Redis: {session_id}")
        return session

    def start_new_topic(self, session_id: str, topic_name: str) -> str:
        """
        Start a new topic in the conversation.
        
        Args:
            session_id: Session ID
            topic_name: Human-readable name for the new topic
        
        Returns:
            New topic ID or None if session not found
        """
        session = redis_service.get_session(session_id)
        if not session:
            print(f"[SESSION] Warning: Session {session_id} not found in Redis")
            return None
        
        # Create new topic
        new_topic_id = f"topic_{uuid.uuid4().hex[:8]}"
        new_topic = {
            "topic_id": new_topic_id,
            "topic_name": topic_name,
            "started_at": datetime.now().isoformat(),
            "turns": []
        }
        
        session["topics"].append(new_topic)
        session["current_topic_id"] = new_topic_id
        session["active_context_window"] = []
        session["current_topic_chunks"] = None
        session["current_topic_basic_streak"] = 0
        session["current_topic_streak_anchor"] = None
        session["last_updated"] = datetime.now().isoformat()
        
        redis_service.save_session(session_id, session, ttl=self.ttl)
        print(f"[SESSION] Started new topic: {topic_name} (ID: {new_topic_id})")
        return new_topic_id

    def add_turn(self, session_id: str, turn_data: dict):
        """
        Add a conversation turn to the current topic and update session statistics.
        
        Args:
            session_id: Session ID
            turn_data: Turn data including query, answer, intent_type, tier, etc.
        """
        session = redis_service.get_session(session_id)
        if not session:
            print(f"[SESSION] Warning: Session {session_id} not found in Redis")
            return
        
        # Initialize statistics if not present (NEW)
        if "statistics" not in session:
            session["statistics"] = {
                "total_turns": 0,
                "cache_hits": 0,
                "cache_misses": 0,
                "tier_distribution": {},
                "created_at": session.get("created_at")
            }
        
        # Update statistics (NEW)
        session["statistics"]["total_turns"] += 1
        
        intent_type = turn_data.get("intent_type")
        if intent_type == "USE_CACHED_CONTEXT":
            session["statistics"]["cache_hits"] += 1
        elif intent_type == "RETRIEVE_NEW_CONTEXT":
            session["statistics"]["cache_misses"] += 1
        
        # Track tier distribution (NEW)
        tier = turn_data.get("tier", "unknown")
        if tier not in session["statistics"]["tier_distribution"]:
            session["statistics"]["tier_distribution"][tier] = 0
        session["statistics"]["tier_distribution"][tier] += 1
        
        turn_data["turn"] = len(session["full_history"]) + 1
        turn_data["topic_id"] = session["current_topic_id"]
        turn_data["timestamp"] = datetime.now().isoformat()

        # Stuck/repeat-question escalation (SS6.3): track a streak of basic
        # questions on the SAME topic so the orchestrator can escalate its
        # explanation strategy on the next turn instead of repeating itself.
        # "Same topic" is decided by the caller (chat.py, via
        # qdrant_service.text_similarity against get_streak_anchor()'s
        # return value) and passed in as is_same_topic_as_streak - this
        # method itself has no embedding access, it just tracks the result.
        # A basic-phrased question on an UNRELATED topic starts a fresh
        # streak of 1 rather than either continuing or fully resetting to 0,
        # since it may itself become the start of a new repeat pattern.
        if turn_data.get("is_basic_question"):
            same_topic = turn_data.get("is_same_topic_as_streak", True)
            if same_topic and session.get("current_topic_streak_anchor"):
                session["current_topic_basic_streak"] = session.get("current_topic_basic_streak", 0) + 1
            else:
                session["current_topic_basic_streak"] = 1
                session["current_topic_streak_anchor"] = turn_data.get("query")
        else:
            session["current_topic_basic_streak"] = 0
            session["current_topic_streak_anchor"] = None

        session["full_history"].append(turn_data)
        session["active_context_window"].append(turn_data)
        
        for topic in session["topics"]:
            if topic["topic_id"] == session["current_topic_id"]:
                topic["turns"].append(turn_data["turn"])
                break
        
        session["last_updated"] = datetime.now().isoformat()
        
        redis_service.save_session(session_id, session, ttl=self.ttl)
        print(f"[SESSION] Added turn {turn_data['turn']} to session {session_id}")

    def update_topic_chunks(self, session_id: str, chunks: list):
        """
        Cache retrieved chunks for the current topic in Redis.
        
        Args:
            session_id: Session ID
            chunks: Retrieved text chunks from Qdrant
        """
        session = redis_service.get_session(session_id)
        if not session:
            return
        
        session["current_topic_chunks"] = chunks
        session["last_updated"] = datetime.now().isoformat()
        redis_service.save_session(session_id, session, ttl=self.ttl)
        print(f"[SESSION] Cached {len(chunks)} chunks for session {session_id}")

    def get_window(self, session_id: str) -> List[dict]:
        """
        Get active context window from Redis.
        """
        session = redis_service.get_session(session_id)
        return session.get("active_context_window", []) if session else []

    def get_full_history(self, session_id: str) -> List[dict]:
        """
        Get complete conversation history from Redis.
        """
        session = redis_service.get_session(session_id)
        return session.get("full_history", []) if session else []

    def get_current_topic_chunks(self, session_id: str) -> Optional[list]:
        """
        Get cached chunks for current topic from Redis.
        """
        session = redis_service.get_session(session_id)
        return session.get("current_topic_chunks") if session else None

    def get_escalation_level(self, session_id: str) -> int:
        """
        How many consecutive basic-question repeats have happened on the
        current topic (SS6.3). 0 = no escalation needed. Read BEFORE add_turn
        is called for the incoming question, since it reflects the streak
        going into this turn, not including it.
        """
        session = redis_service.get_session(session_id)
        if not session:
            return 0
        return session.get("current_topic_basic_streak", 0)

    def get_streak_anchor(self, session_id: str) -> Optional[str]:
        """
        The question text that started the CURRENT repeat-question streak
        (SS6.3), or None if there's no active streak. The caller compares a
        new basic-phrased question against this via semantic similarity to
        decide whether it continues the streak or starts a new one.
        """
        session = redis_service.get_session(session_id)
        if not session:
            return None
        return session.get("current_topic_streak_anchor")

    def get_turn(self, session_id: str, turn_number: int) -> Optional[dict]:
        """
        Get a specific turn from full history in Redis.
        """
        full_history = self.get_full_history(session_id)
        for turn in full_history:
            if turn["turn"] == turn_number:
                return turn
        return None

# Global session manager instance
session_manager = SmartSessionManager()
