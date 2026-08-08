# Personalized Learning System — Design Document

**Status**: Requirements capture phase. Nothing below is a locked design yet — this document is the running record of the requirement, our decisions, and what's actually built, so anyone picking this up later (teammate, or a future session) has the full trail.

**Started**: 2026-08-08

---

## 1. Why this document exists

This is not a single feature — it's a standing intelligence system: the platform's core promise is personalized learning, so the system must adapt to the student rather than the student adapting to the system. Because of that scope, we're doing this in stages: capture raw requirements → market/technical research on the open questions → lock a data model and prompt strategy → build incrementally, updating this doc after every material change. If work pauses here, this file plus the change log at the bottom should be enough for someone else to pick it up.

---

## 2. Raw requirements (from CEO call, transcribed 2026-08-08)

Captured close to verbatim/paraphrased from the source discussion — not yet filtered into a design. Anything marked "(open)" is explicitly undecided.

### 2.1 Student profile — two inputs
- **Input 1 — registration-time preferences** (student states these explicitly, locked in at signup):
  - Learning style: storytelling vs. straight/direct answer vs. detailed step-by-step explanation. (open) — exact preference taxonomy is not decided; CEO wants this informed by market research on Indian students' actual learning habits/preferences before finalizing options, not just picked internally.
  - Tough subjects / easy subjects (self-reported).
  - Preferred output format: text / text+audio / text+audio+video. (open, tier-dependent — see §2.5)
- **Input 2 — dynamically inferred profile** (built by the system from behavior, not stated by the student):
  - Built by observing: what subjects/questions are asked, whether questions are basic or advanced relative to grade level, follow-up question patterns, feedback given (👍/👎 + reason on dislike), and (later, once a test/quiz feature exists) test performance across complexity tiers.
  - Used to categorize the student — CEO's framing (borrowed from a management "4-quadrant employee" analogy): skill level × attitude/engagement level, plus an IQ/advancement signal (e.g. a 6th grader asking 10th-grade-level questions should be recognized as such, not restricted to the 6th-grade default).
  - **Repeat-simple-question detection**: if a student keeps asking simple/basic questions on the same topic repeatedly, the system should recognize this as a "stuck" or "needs a different approach" pattern and adapt — not just re-answer the same way each time. (open) — what "rebuild" means concretely (change explanation style? escalate to a different format? flag for review?) is not decided.

### 2.2 Preferences → prompt strategy (open, needs research)
- Does each learning-style preference (storytelling / straight answer / detailed) require a distinct answer-generation prompt variant? Not yet decided how many variants, or whether it's prompt-branching vs. a single prompt with a style instruction slot.
- Per-query intent, expressed inline in the question itself (e.g. "give me a quick answer," "explain in detail," "as a diagram," "as bullet points") should also influence response shape, independent of the stored preference — a stated preference is a default, not a lock.

### 2.3 24-hour session / question numbering
- When a student logs in and asks their first question, a 24-hour session window starts.
- Within that window, question "numbering" (first question, follow-up, etc.) is tracked relative to that 24h session, not the account's all-time history.
- (open) exact mechanics of what resets vs. persists across a 24h boundary — the profile itself is long-lived, but session-scoped context is not.

### 2.4 Continuity / RAG of the student's own interaction history — the core complaint
- **The concrete failure that triggered this whole conversation**: a tester asked a follow-up question and got an answer that ignored the prior turn entirely ("gave a blunder answer").
- Every question, its reformulated form, and its answer must be stored in a retrievable way (the CEO's words: "should be stored in our RAG") — specifically so that:
  - If a student already covered a topic (e.g. asked about the digestive system) and later asks a related question (e.g. "difference between digestive and circulatory system"), the system should recognize the digestive system was already taught to *this student* and build on that instead of re-explaining from scratch.
  - This is explicitly a **per-student** memory, not the existing global query cache (which is anonymous/shared and keyed only on normalized text + class + subject — see §3).
- Before generating any answer, the system should check whether something sufficiently similar was already asked (globally, via the existing cache) *and* whether this specific student already has relevant history on the topic (new, per-student layer) — these are two different checks serving two different purposes and must not be conflated.

### 2.5 Tiering (stated, not yet scoped for build)
- Free tier: text-only, capped at 10 questions/day, intended to stay free permanently (not a bait trial).
- Trial: one week, everything unlocked (text/audio/video).
- Paid: format selection available per the plan purchased.
- **Explicitly deprioritized for the current build phase** (per 2026-08-08 follow-up discussion) — not being designed or built yet.

### 2.6 Registration / login / parent dashboard — explicitly deprioritized for now
- Real registration UI, login flow, and a parent-controlled account/dashboard model were all part of the original transcript, but per the 2026-08-08 follow-up: **not needed right now**. For development/testing, a manually-created test user profile (mocked registration fields) is sufficient to build and observe the adaptive system against. Revisit once the intelligence layer itself is proven.

### 2.7 CEO's explicit process requirement
- Do market research + technical research before finalizing the preference taxonomy and profile schema — this is not to be designed from internal assumptions alone.
- CEO intends to produce a flowchart mapping the full system and future features to discuss against.
- This document must be kept current after every material decision or change, specifically so it can be handed to a teammate if needed.

---

## 3. Current system — verified state (as of 2026-08-08)

Baseline facts established before any new design, so decisions below are made against reality, not assumption.

### 3.1 What exists today
- **No student/preference profile exists.** `users/{uid}` only holds `email/name/class/board/role`. No signup route was found in the backend; no registration UI exists in `public/`.
- **No follow-up context continuity.** `run_orchestrator_pipeline()` (`backend/app/orchestrator_test/test_runner.py`) receives only the raw query + a shallow student_profile dict — no prior turns, no conversation history. Session turns are written to Redis (`session_manager.add_turn`) after each answer but never read back into the next orchestrator call. This is the direct root cause of the "blunder answer on follow-up" complaint.
- **Global query cache already exists** but is the wrong tool for per-student memory: keyed on `(normalized_query_text, class, subject)` only, in Firestore under `classes/{class}/subjects/{subject}/query_cache/{doc_id}`. It's anonymous and shared across all students — useful for not re-answering the same question twice system-wide, but cannot answer "has *this* student already learned this."
- **Feedback (👍/👎 + reason on dislike) is already fully built**, both frontend (`public/script.js`) and backend (`POST /api/feedback` in `backend/app/api/routes/chat.py`) — a real asset to build on, not a gap.
- **No quiz/test feature exists** — the "test result by complexity tier" signal from the original transcript has no data source yet. Complexity signals can currently only come from question content itself.
- **No tiering/rate-limiting exists** anywhere in the backend.

### 3.2 Firestore schema — assessment (CEO's direct question, 2026-08-08)
The schema is inconsistent, not because it was never planned, but because a migration was started and never fully finished:
- A cleaner nested schema (`users/{uid}/stats`, `users/{uid}/achievements`, `users/{uid}/notebooks`, `users/{uid}/mistakes`, etc.) was designed and a one-time migration script (`backend/scripts/migrate_db.py`) exists and appears complete as written.
- **However, the app's live write paths were never updated to target the new shape** — `analytics_service.py`, `dashboard_service.py`, and `bag_service.py` still actively write to the old flat collections (`user_stats`, `user_achievements`, `student_mistakes`, `notebooks`, `bag_items`) today, in parallel with the nested ones.
- Concretely broken: `user_stats` has **two different doc-ID schemes both being written by live code right now** (`{uid}_{class}` and a "legacy fallback" `{uid}`). "Mistakes" data has two separate homes (`student_mistakes` root collection vs. `users/{uid}/mistakes/mistakes_doc`). At least one write path (`track_cumulative_analytics` in `chat.py`) is dead code — defined, never called.
- **Implication for this project**: building a new per-student profile/history layer on top of this without first deciding where it lives risks becoming a third inconsistent home for user data. This should be resolved — at minimum, a firm decision on where new personalization data lives — before or alongside schema work, not after.

---

## 4. Open questions (not yet answered — do not assume)

- What exact preference taxonomy should registration collect? (pending market research)
- What does "rebuild the approach" mean concretely when repeat-simple-questions are detected?
- Does per-student interaction history live in Qdrant (semantic retrieval, matching the "RAG" framing) or Firestore (structured, matching profile/session data) or both?
- How is the profile categorization (skill × attitude quadrant, IQ/advancement signal) actually computed — rules-based, or a separate scoring model?
- Should the existing global query cache and the new per-student history layer be merged, layered, or kept fully separate?
- Firestore schema cleanup: fix now, alongside, or explicitly deferred with a documented reason?

---

## 5. Change log

- **2026-08-08**: Document created. Raw requirements captured from CEO call transcript + same-day follow-up (registration/login/parent-dashboard deprioritized for now; focus is the adaptive intelligence layer, using a manually-seeded test profile instead of real registration). Current-system assessment completed (no profile exists, follow-up continuity is broken, Firestore schema has an incomplete migration). No design decisions made yet — next step is CEO-driven market/technical research on the open questions in §4.


