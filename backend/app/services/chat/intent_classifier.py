"""
Intelligent conversation action classifier.
Determines the next best action for the system to take based on conversational context.
Enhanced with semantic similarity scoring for intelligent cache reuse.
"""
from typing import List, Dict, Optional
import json
from backend.app.prompts import templates


def determine_next_action(
    current_query: str,
    conversation_window: List[dict],
    openai_client,
    generation_model_name: str,
    embedder=None,
    is_clicked_followup: bool = False,  # NEW: Flag for pre-generated follow-up clicks
    last_action: str = None  # NEW: Previous action for context awareness
) -> dict:
    """
    Determine the next action using 5-TIER intelligent routing:
    
    TIER 1: Clicked follow-ups (absolute priority - always use cache)
    TIER 2: Empty conversation (must retrieve)
    TIER 3: Meta-conversational queries (answer from history)
    TIER 4: Semantic similarity analysis (smart cache/retrieval)
    TIER 5: LLM fallback for edge cases
    
    Args:
        current_query: User's current query
        conversation_window: List of recent conversation turns
        openai_client: OpenAI client for classification
        generation_model_name: Model name to use for generation
        embedder: unused (kept for call-site compatibility; similarity tier removed)
        is_clicked_followup: True if user clicked a pre-generated follow-up
        last_action: Previous action taken (for context)
    
    Returns:
        {
            "action": "USE_CACHED_CONTEXT" | "RETRIEVE_NEW_CONTEXT" | "ANSWER_FROM_HISTORY",
            "reason": str,
            "similarity_score": float,
            "tier": str,
            "new_topic_name": str (optional)
        }
    """
    
    # === TIER 1: ABSOLUTE PRIORITY FOR CLICKED FOLLOW-UPS ===
    if is_clicked_followup:
        # If parent query used cache, follow-up definitely can too
        if last_action == "USE_CACHED_CONTEXT":
            # print(f"[TIER 1] âš¡ Clicked follow-up + cached parent â†’ Guaranteed cache reuse")
            return {
                "action": "USE_CACHED_CONTEXT",
                "reason": "Pre-generated follow-up with cached parent context",
                "similarity_score": 1.0,
                "tier": "ABSOLUTE_PRIORITY",
                "confidence": "GUARANTEED"
            }
        
        # Even if parent did retrieval, follow-ups are generated from those chunks
        # print(f"[TIER 1] âœ“ Clicked follow-up â†’ Strong cache preference")
        return {
            "action": "USE_CACHED_CONTEXT",
            "reason": "Pre-generated follow-ups are contextually guaranteed to be related",
            "similarity_score": 0.95,
            "tier": "STRONG_PREFERENCE",
            "confidence": "HIGH"
        }
    
    # Similarity thresholds for cache decisions
    HIGH_SIMILARITY_THRESHOLD = 0.75  # Very similar â†’ use cache
    MEDIUM_SIMILARITY_THRESHOLD = 0.50  # Somewhat similar â†’ ask LLM
    
    # === TIER 2: EMPTY CONVERSATION ===
    if not conversation_window:
        # print(f"[TIER 2] ðŸ†• First query â†’ Retrieval required")
        return {
            "action": "RETRIEVE_NEW_CONTEXT",
            "new_topic_name": current_query[:50],  # Use query as initial topic name
            "reason": "First query in conversation - no context available",
            "similarity_score": 0.0,
            "tier": "INITIAL_QUERY"
        }

    # === TIER 3: META-CONVERSATIONAL QUERIES ===
    # Queries about the conversation itself, not about the topic
    meta_patterns = [
        "what was", "what did", "earlier", "previous", "before",
        "remind me", "first question", "last", "summarize", "review"
    ]
    query_lower = current_query.lower()
    if any(pattern in query_lower for pattern in meta_patterns):
        # print(f"[TIER 3] ðŸ’¬ Meta-conversational query detected â†’ Answer from history")
        return {
            "action": "ANSWER_FROM_HISTORY",
            "new_topic_name": None,
            "reason": "User asking about previous conversation content",
            "similarity_score": 0.0,
            "tier": "META_QUERY"
        }

    # === TIER 4: SEMANTIC SIMILARITY ANALYSIS ===
    # Embedder-based similarity scoring was removed along with sentence-transformers
    # (2026-09-02 Render 512Mi OOM fix) - it never actually worked with the current
    # OpenAIEmbedderWrapper/FastEmbedWrapper embedders anyway (encode() doesn't accept
    # convert_to_tensor), so this tier always fell through to 0.0 already.
    max_similarity = 0.0
    similarity_scores = []
    if max_similarity >= HIGH_SIMILARITY_THRESHOLD:
        # print(f"[TIER 4] âš¡ High similarity ({max_similarity:.3f}) â†’ Cache reuse")
        return {
            "action": "USE_CACHED_CONTEXT",
            "new_topic_name": None,
            "reason": f"High semantic similarity ({max_similarity:.2f}) with recent queries - using cached context for speed",
            "similarity_score": max_similarity,
            "tier": "HIGH_SIMILARITY"
        }
    
    # Low similarity + substantial history â†’ Likely new topic
    # But double-check it's not a meta-query first
    if max_similarity < MEDIUM_SIMILARITY_THRESHOLD and len(conversation_window) >= 2:
        if not any(p in query_lower for p in meta_patterns):
            # print(f"[TIER 4] ðŸ” Low similarity ({max_similarity:.3f}) â†’ New retrieval")
            return {
                "action": "RETRIEVE_NEW_CONTEXT",
                "new_topic_name": current_query[:50],
                "reason": f"Low similarity ({max_similarity:.2f}) suggests topic change",
                "similarity_score": max_similarity,
                "tier": "LOW_SIMILARITY"
            }


    # === TIER 5: LLM FALLBACK FOR EDGE CASES ===
    # Medium similarity (0.50-0.75) or uncertain cases
    # print(f"[TIER 5] ðŸ¤– LLM classifier for edge case (similarity: {max_similarity:.3f})")
    
    # Build a summary of the last few turns for the LLM prompt.
    context_summary = ""
    for turn in conversation_window[-3:]: # Use last 3 turns
        answer_preview = turn.get('answer', 'No answer was given.')[:200]
        if len(turn.get('answer', '')) > 200:
            answer_preview += "..."
        context_summary += f"Q: {turn['query']}\nA: {answer_preview}\n\n"

    # Include similarity score in the prompt for better LLM decision-making
    similarity_context = ""
    if max_similarity > 0:
        similarity_context = f"\n## Semantic Similarity Analysis:\nThe current query has a semantic similarity score of {max_similarity:.2f} with recent queries (0.0 = completely different, 1.0 = identical).\n"

    prompt = templates.DETERMINE_NEXT_ACTION_PROMPT.format(
        context_summary=context_summary,
        similarity_context=similarity_context,
        current_query=current_query
    )


    try:
        response = openai_client.models.generate_content(
            model=generation_model_name,
            contents=prompt
        )

        # Safety check: Ensure the response has content.
        if not response.parts:
            finish_reason = response.candidates[0].finish_reason if response.candidates else "Unknown"
            print(f"[ACTION_CLASSIFIER] LLM returned an empty response. Finish Reason: {finish_reason}. Defaulting to new retrieval.")
            return {
                "action": "RETRIEVE_NEW_CONTEXT",
                "new_topic_name": current_query[:50],
                "reason": f"LLM response was empty or blocked (finish reason: {finish_reason}).",
                "similarity_score": max_similarity
            }

        response_text = response.text.strip()
        
        # --- ROBUST JSON EXTRACTION ---
        try:
            # Find the start and end of the JSON object
            start_index = response_text.find('{')
            end_index = response_text.rfind('}') + 1
            
            if start_index == -1 or end_index == 0:
                raise ValueError("No JSON object found in the response.")
            
            # Extract and parse the JSON
            json_text = response_text[start_index:end_index]
            result = json.loads(json_text)
        except (ValueError, json.JSONDecodeError) as json_e:
             # If parsing fails, it's a critical error with the LLM's output.
             print(f"[ACTION_CLASSIFIER] JSON parsing failed: {json_e}")
             print(f"[ACTION_CLASSIFIER] Raw LLM response:\n---\n{response_text}\n---")
             raise ValueError(f"Failed to parse JSON from LLM: {json_e}")

        # Validate the response from the LLM
        if "action" not in result or result["action"] not in ["USE_CACHED_CONTEXT", "RETRIEVE_NEW_CONTEXT", "ANSWER_FROM_HISTORY"]:
             raise ValueError("LLM response missing or has invalid 'action'.")

        # print(f"[LLM CLASSIFIER] âœ“ Action determined: {result['action']}")
        
        return {
            "action": result["action"],
            "new_topic_name": result.get("new_topic_name"),
            "reason": result.get("analysis", "LLM-based action determination."),
            "similarity_score": max_similarity,
            "tier": "LLM_FALLBACK"
        }

    except Exception as e:
        print(f"[TIER 5] Error: LLM classification error: {e}")
        # Safe fallback: default to safe retrieval
        return {
            "action": "RETRIEVE_NEW_CONTEXT",
            "new_topic_name": current_query[:50],
            "reason": f"Classification error - defaulting to safe retrieval (error: {str(e)[:100]})",
            "similarity_score": max_similarity,
            "tier": "ERROR_FALLBACK"
        }
