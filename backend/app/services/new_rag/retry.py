"""
Small retry-with-backoff helper for OpenAI calls in the new_rag pipeline.

Added after live testing surfaced a real reliability gap: this project's
OpenAI account has a shared 200,000 tokens/minute limit across everything
running on it, and a 13-chapter book with per-diagram vision captioning
calls plus topic-detection calls routinely exceeds that within a batch.
Before this fix, a rate limit either silently dropped a diagram caption
(non-fatal but lossy) or crashed an entire chapter's topic detection
(fatal - confirmed live, a whole diagnostic run died on one 429). Retrying
with backoff is the standard, correct fix for a transient, expected-to-clear
condition like this - not something to work around by hand-timing when
scripts run.
"""
import logging
import re
import time
from typing import Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# OpenAI's TPM limit is a rolling 60-SECOND window, not a per-request thing -
# confirmed live that "Used 200000/200000" persists across several retries
# in a row even though each individual request is small (~800 tokens),
# because a chapter's ~30-40 diagram-caption calls plus its one large
# topic-detection call can add up to well over the limit within that same
# rolling minute, and the next chapter's calls can land before the previous
# chapter's usage has rolled off. A ~30s total backoff budget (the old
# max_attempts=5) isn't long enough to reliably outlast that - it needs to
# be able to wait out close to a full window before giving up.
_MAX_SINGLE_DELAY_SECONDS = 30.0
_RETRY_AFTER_RE = re.compile(r"try again in ([\d.]+)(ms|s)", re.IGNORECASE)


_TRANSIENT_MARKERS = ("429", "rate_limit", "timeout", "timed out", "connection",
                      "500", "502", "503", "504", "internal server error", "service unavailable")


def _is_transient(e: Exception) -> bool:
    # Rate limits (429) were the originally-confirmed case (see module
    # docstring), but timeouts/connection drops/5xx are equally transient
    # "try again shortly" conditions - a whole chapter previously died on
    # any of these just as fatally as on a 429, for no good reason. A
    # non-transient error (e.g. 400 for bad input / context-length overflow)
    # deliberately stays excluded, since retrying that fails identically
    # every time and just wastes the backoff delay.
    try:
        import openai
        if isinstance(e, (openai.RateLimitError, openai.APITimeoutError,
                           openai.APIConnectionError, openai.InternalServerError)):
            return True
    except ImportError:
        pass
    msg = str(e).lower()
    return any(marker in msg for marker in _TRANSIENT_MARKERS)


def _server_suggested_delay(e: Exception) -> Optional[float]:
    """OpenAI's 429 body includes 'Please try again in 251ms' - honoring that
    exact figure (when it's larger than our own backoff would be) converges
    faster than blind exponential guessing, though it's usually far too
    optimistic on a persistently-saturated window (see module docstring),
    which is exactly why exponential backoff is still the fallback, not this."""
    m = _RETRY_AFTER_RE.search(str(e))
    if not m:
        return None
    value, unit = float(m.group(1)), m.group(2).lower()
    return value / 1000.0 if unit == "ms" else value


def call_with_retry(fn: Callable[[], T], max_attempts: int = 8, base_delay: float = 2.0) -> T:
    """
    Calls fn() and retries on a transient error (rate limit, timeout,
    connection error, 5xx) with exponential backoff, capped per-attempt at
    _MAX_SINGLE_DELAY_SECONDS. Re-raises the last error if all attempts are
    exhausted, and re-raises immediately on any non-transient error - this
    only handles the "try again shortly" case, not general failures.

    max_attempts=8 with the delay cap gives a total backoff budget of
    roughly 2+4+8+16+30+30+30 =~ 120s - comfortably longer than OpenAI's
    60s rolling TPM window, so a chapter landing on an already-saturated
    window (from its own burst or the previous chapter's tail) reliably
    outlasts it instead of giving up after ~30s.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as e:
            if not _is_transient(e) or attempt == max_attempts:
                raise
            delay = min(base_delay * (2 ** (attempt - 1)), _MAX_SINGLE_DELAY_SECONDS)
            suggested = _server_suggested_delay(e)
            if suggested is not None:
                delay = max(delay, suggested)
            logger.warning(f"[NEW_RAG] Transient error (attempt {attempt}/{max_attempts}): {e}. Retrying in {delay:.1f}s")
            time.sleep(delay)
    raise RuntimeError("unreachable")
