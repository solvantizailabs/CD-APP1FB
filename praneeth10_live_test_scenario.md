# Live Test Scenario — praneeth10@cg.com — Personalization + History + TTS

**For a NEW chat session picking this up**: this file is self-contained. Read this whole file first, along with `personalized_learning.md` (especially §12) for full background on what was built and why. Don't assume you have the prior conversation's context — everything you need to interpret the results is here.

## What this tests, and why these 10 questions specifically

Everything built across a long session: the History page (video/text tabs, subject categorization, click-to-replay), TTS (video scene narration, and saved audio for text answers), the personalization pipeline (skill/engagement quadrant, per-student memory, topic-tagged feedback memory with a `MANDATORY FEEDBACK REQUIREMENT` directive, a video/text format tiebreaker), and session continuity when replaying from History. Each question below is chosen to exercise a *specific* mechanism, in a *specific* order, so a failure points at a specific cause — not just "something's wrong somewhere." Several questions are deliberately designed to try to **break** things, not just confirm they work — per explicit instruction to test failure modes, not only the positive path.

**Account**: log in as `praneeth10@cg.com`. Class 10. Stored preference is `"storytelling"`.

**Already done (in the prior session, not part of your 10)**: asked "What is the significance of the preamble of the Indian Constitution?" (logged, doc_id `20260812_143241_class10`), then gave real negative feedback via the actual API: *"too much storytelling, I just want the key points in bullet form."* A follow-up question ("What are the key features of the Indian Constitution?") was asked immediately after — **it failed the test**: the answer still opened in full storytelling style ("Imagine a vast, intricate tapestry..."), ignoring the feedback. Root cause found and fixed: the `MANDATORY FEEDBACK REQUIREMENT` directive only said it overrides the default *format* rule, not the *stored preference* rule — so the general "storytelling" default was winning over the specific correction. Fixed in `master_orchestrator_prompt.txt`. **This fix is UNTESTED live** — Question 1 below re-tests this exact scenario.

## The 10 questions, in order

Ask these **in order**, waiting for each answer to fully finish before the next. Where a step says "click X," actually click it in the real UI (not just read the answer) — several of these are testing UI interaction, not just answer content.

1. **Re-test the just-fixed conflict** (Constitution / social studies, related to the disliked topic): *"What are the fundamental rights guaranteed by the Indian Constitution?"*
   → **Watch for**: does it now actually respect the earlier feedback (concise, bullet-style, NOT opening with a story/analogy) despite the stored "storytelling" preference? This is the most important single check in this whole list — it's a real bug found live, just fixed, never confirmed.

2. **Trigger a genuine video** (multi-step science process, class 10): *"Explain the process of respiration in living organisms"*
   → **Watch for**: does a video actually generate? Note the lesson ID from the browser/network tab if possible. Does narration audio play automatically?

3. **Pure formula recall — should stay text, tests the procedural-intent fix stays correct**: *"What is the formula for the area of a circle?"*
   → **Watch for**: should be a short text answer (QUICK_ANSWER), NOT a video — confirms the earlier "asking to be told the formula, not asking how to apply it" distinction still holds correctly, live, for the first time on a real account.

4. **Procedural-intent trigger — should become a video**: *"Explain step by step how to find the roots of a quadratic equation"*
   → **Watch for**: should become VIDEO_REQUIRED (this exact class of question — "step by step" + formula — was the original bug that started the prompt-fixing work). If this comes back as text-only, that fix has regressed or doesn't generalize past the one example it was built on.

5. **Deliberately ambiguous/borderline question** (to probe the quadrant tiebreaker, which so far has never been observed to actually trigger): *"What is Newton's first law of motion?"*
   → **Watch for**: text or video? No strong prediction here — this is genuinely exploratory, record whatever happens.

6. **Open History from the header button. Click into "My videos."** Find the respiration video from Question 2.
   → **Watch for**: is it correctly categorized under "Science," not "Uncategorized"? Click it — does it replay correctly (original question shown as if just asked, video mounts, narration plays)? Click "Show Text Answer" — does the toggle work?

7. **Immediately after replaying that video (still in the same chat, don't refresh), ask a DIRECT follow-up about it**: *"What role do the lungs play in this process?"*
   → **This is the core "does context follow from History" test.** Watch for: does the answer clearly build on the respiration topic (proves the replayed turn actually entered the live session), or does it feel like a cold, disconnected answer (would mean context did NOT carry over from the replay)?

8. **Go back to History, "My questions" tab. Find any older TEXT-only answer** (not from today). Click it to replay.
   → **Watch for**: does a "🔊 Play answer" button appear? Click it — does real audio actually play, or is it silent/broken? If the item predates today's TTS-persistence work, it's *expected* to have no audio button at all — note whether that's the case (missing button = expected for old data, present-but-broken = a real bug).

9. **Deliberate failure-seeking case — false-positive check for feedback memory**: ask something from a **completely unrelated subject** to the Constitution complaint from earlier, e.g.: *"What is the boiling point of water?"*
   → **Watch for**: this should NOT be influenced by the earlier "too much storytelling" Constitution feedback at all — that correction should only resurface on related topics (Constitution/Social Studies), not bleed into an unrelated Science question. If this answer is suspiciously terse/bulleted for no reason, that's a false-positive bug (feedback memory triggering somewhere it shouldn't).

10. **Real thumbs-down via the actual UI button** (not an API call this time — click the real 👎 icon in the chat) on whichever answer above felt weakest, with a real, specific reason typed in.
    → **Watch for**: does the UI show any confirmation the feedback was received? This is the first-ever real human click on the fixed feedback button — confirm no error, no silent failure.

## What to send back

For each question: the question asked, a copy of the actual answer (or at least its opening + closing), whether it was video or text, and anything that looked wrong per the "watch for" notes above. Doesn't need to be formatted — raw paste is fine, it'll get read carefully either way.

## Known, already-documented gaps (don't re-report these as new findings)

- Old history items (before the History feature was built) have miscategorized subjects and no saved audio — expected, not fixable retroactively (see `personalized_learning.md` §8, `HISTORY_PAGE` context in the wider session).
- The quadrant-based video/text tiebreaker (Question 5) has never been observed to actually trigger in any test so far — a "no effect visible" result is expected and informative, not necessarily a bug.
- Live TTS and the saved-answer-audio persistence sometimes double-synthesize (an accepted, known cost tradeoff — not something to flag as a new bug).
