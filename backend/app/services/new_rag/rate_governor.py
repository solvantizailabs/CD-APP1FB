"""
Centralized rate governor for the new_rag pipeline's OpenAI calls.

Replaces static per-call-site sleeps (a fixed 1s between diagram captions, a
fixed 5s between chapters) with a shared, rolling-window token-budget
tracker. Static sleeps were tuned against one 13-chapter book's volume and
would need re-tuning every time volume changes (more chapters, more
subjects, a dynamic multi-subject run) - this self-adjusts to whatever is
actually sent instead, so it doesn't need re-tuning as volume grows.

OpenAI's TPM (tokens-per-minute) limit is a rolling 60-second window, not a
fixed per-request delay - confirmed live (see retry.py's docstring: "Used
200000/200000" persisting across several retries even though each individual
request was small). This module tracks real spend in that same window and
only waits exactly as long as needed before each call, never more - free
budget means zero wait, tight budget means an exact wait, not a guess.
"""
import logging
import os
import time
from collections import deque
from typing import Deque, Tuple

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 60.0
# Config, not hard-coded - scales with zero code changes if the account's
# rate tier is ever raised, instead of another round of re-tuning sleeps.
TPM_LIMIT = int(os.environ.get("RAG_TPM_LIMIT", "200000"))

_ledger: Deque[Tuple[float, int]] = deque()


def _prune(now: float) -> None:
    while _ledger and now - _ledger[0][0] > WINDOW_SECONDS:
        _ledger.popleft()


def _window_usage(now: float) -> int:
    _prune(now)
    return sum(tokens for _, tokens in _ledger)


def reset() -> None:
    """Clears the ledger. Test-only - production usage should never need this."""
    _ledger.clear()


def reserve(estimated_tokens: int) -> float:
    """
    Blocks (if necessary) until there is room in the rolling 60s window for
    `estimated_tokens`, then records the reservation. Call this immediately
    before firing an LLM/embedding request that's expected to cost roughly
    `estimated_tokens`. Returns the number of seconds actually waited (0.0
    if none was needed) - useful for logging/testing.
    """
    now = time.monotonic()
    used = _window_usage(now)
    waited = 0.0

    if used + estimated_tokens <= TPM_LIMIT:
        _ledger.append((now, estimated_tokens))
        return waited

    while True:
        if not _ledger:
            break
        oldest_time, _ = _ledger[0]
        wait_for = WINDOW_SECONDS - (now - oldest_time)
        wait_for = max(wait_for, 0.05)
        logger.info(
            f"[NEW_RAG][RateGovernor] {used}/{TPM_LIMIT} tokens used in window, "
            f"need {estimated_tokens} more - waiting {wait_for:.1f}s for budget to free up"
        )
        time.sleep(wait_for)
        waited += wait_for
        now = time.monotonic()
        used = _window_usage(now)
        if used + estimated_tokens <= TPM_LIMIT:
            break

    _ledger.append((now, estimated_tokens))
    return waited


def estimate_diagram_caption_tokens() -> int:
    """detail="low" images cost a flat 85 tokens (see table_diagram_extractor.py),
    plus the fixed caption prompt (~80 tokens) and a max_tokens=100 response."""
    return 85 + 80 + 100


def estimate_text_tokens(char_count: int) -> int:
    """~3 chars/token, deliberately conservative (rounds up) - overestimating
    just means a slightly earlier wait, not a missed budget check."""
    return max(1, char_count // 3)
