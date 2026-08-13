# Personalized Learning System — Design & Implementation Plan

**Status**: Core system implemented and verified live against the real Firebase/Qdrant project — the original 7 changes (§6), 2 design corrections found on review (§6.2, §6.3), a round of gap-analysis fixes (§9b: feedback signal, tough/easy subjects, inline-preference override), real-user log analysis fixes (§11: session continuity, style-preference enforcement), and a further round (§12: the feedback endpoint was silently broken in production this whole time - now fixed - plus new topic-tagged feedback memory and a video/text format tiebreaker) plus infrastructure (real profile-completion UI, video pipeline personalization, daily-refresh endpoint, durable consolidated logging). See §7 for how to test and where logs live, §8 for known gaps, §9b for the 2 items still needing your scope decision, §12 for what's new and NOT yet proven effective vs. confirmed.
**Purpose of this document**: single source of truth for what's being built, why, and how — so anyone (teammate, CEO, or a future session) can read it cold and understand the full picture without needing prior conversation context.

---

## 1. Executive Summary

The platform's core promise is personalized learning — the system should adapt to each student, not the other way around. Today, it doesn't: there is no student profile, and follow-up questions are answered with zero memory of what the student asked before (the bug that started this work — a student asked a follow-up and got an answer that ignored the prior turn entirely).

This document defines **7 changes** that together close that gap:

| # | Change | One-line summary |
|---|--------|-------------------|
| 1 | Preference taxonomy | Student states how they like answers delivered (storytelling / direct / detailed) |
| 2 | Skill × engagement quadrant | System silently classifies each student's ability and engagement level |
| 3 | Stuck-question escalation | Repeated same-topic questions trigger a different explanation strategy, not a repeat |
| 4 | Per-student memory | The system remembers what each student was already taught, and builds on it |
| 5 | Layered retrieval | The new per-student memory and the existing shared answer cache stay separate |
| 6 | Firestore schema decision | New personalization data gets one clean home, not a third inconsistent one — **approved** |
| 7 | Semantic cache upgrade | The existing shared cache stops missing same-intent questions worded differently |

Registration UI, login, parent dashboards, and paid tiering are explicitly out of scope for this phase (§2.6) — a manually-seeded test profile stands in for real registration until the intelligence layer itself is proven.

---

## 2. Requirements (source: CEO call + same-day follow-up)

### 2.1 Student profile — two inputs
- **Registration-time preferences** (stated by the student, locked in at signup): response-style preference (storytelling / direct / detailed), self-reported tough/easy subjects, preferred output format (text / text+audio / text+audio+video — tier-dependent, see §2.5).
- **Dynamically inferred profile** (built by the system from behavior, not stated): what's asked, whether questions are basic or advanced for grade level, follow-up patterns, feedback given (👍/👎 + reason on dislike), and later, test performance by complexity tier. Used to classify the student into a skill × engagement quadrant, including recognizing above-grade-level students rather than capping them at the default.
- **Repeat-question detection**: a student repeatedly asking simple/basic questions on the same topic should trigger a different explanation approach, not a repeat of the same one.

### 2.2 Preferences → response shape
Per-query intent stated inline in the question itself ("give me a quick answer," "as a diagram") should shape the response independent of the stored preference — a stated preference is a default, not a lock.

### 2.3 24-hour session window
A student's first question of the day starts a 24h session window; question "numbering" (first question, follow-up, etc.) is tracked relative to that window, not all-time history. The profile itself is long-lived; session-scoped context is not.

### 2.4 Continuity of the student's own history — the core complaint
The triggering failure: a follow-up question got an answer that ignored the prior turn. Every question, its reformulated form, and its answer must be retrievable per-student, so that (e.g.) a student who already covered the digestive system and later asks about the difference between digestive and circulatory systems gets an answer that builds on what they were already taught — not a re-explanation from scratch. This is explicitly separate from the existing global query cache (anonymous, shared, keyed on text only).

### 2.5 Tiering — deprioritized
Free (text-only, 10 questions/day, permanent), trial (1 week, everything unlocked), paid (format per plan). Not being designed or built in this phase.

### 2.6 Registration / login / parent dashboard — deprioritized
Not needed right now. A manually-created test user profile is sufficient for building and testing the adaptive system. Revisit once the intelligence layer is proven.

### 2.7 Process requirement
Market/technical research before finalizing the preference taxonomy and profile schema — not designed from internal assumptions alone. This document stays current after every material decision.

---

## 3. Current System — Verified State

- **No student/preference profile exists.** `users/{uid}` only holds `email/name/class/board/role`. No signup route or registration UI exists.
- **No follow-up context continuity.** `run_orchestrator_pipeline()` (`backend/app/orchestrator_test/test_runner.py`) receives only the raw query + a shallow `student_profile` dict — no prior turns. Session turns are written to Redis (`session_service.py`'s `SmartSessionManager.add_turn`) but never read back into the next orchestrator call. This is the direct root cause of the "blunder answer on follow-up" bug.
- **Global query cache exists but is the wrong tool for per-student memory** — keyed on exact normalized text + class + subject (`backend/app/core/firestore_service.py`), anonymous and shared across all students. Cannot answer "has *this* student already learned this," and (newly identified, §4) also misses same-intent questions that are worded differently.
- **Feedback (👍/👎 + reason) is already fully built**, frontend and backend — a real asset to build on.
- **No quiz/test feature exists** — the "test result by complexity tier" signal has no data source yet.
- **No tiering/rate-limiting exists.**
- **Firestore schema is mid-migration.** A cleaner nested schema (`users/{uid}/stats`, `/achievements`, `/notebooks`, `/mistakes`) was designed and migrated once, but live write paths (`analytics_service.py`, `dashboard_service.py`, `bag_service.py`) still also write to the old flat collections in parallel. Concretely broken: `user_stats` has two different doc-ID schemes both live; "mistakes" data has two separate homes; at least one write path is dead code.

---

## 4. Research Findings

A market/technical research pass was run against every open design question before locking any decisions.

- **Preference taxonomy — a risk in the original framing.** The storytelling/direct/detailed idea was implicitly framed as a "learning style" claim (i.e. "this is how this student learns best"). That specific claim is not scientifically supported: the "meshing hypothesis" (matching instruction to a stated learning style improves outcomes) has been tested repeatedly with proper methodology and shows no effect; VARK-style typing is widely classified as a neuromyth in education research. **Resolution**: keep the 3 options, but treat them as a *format/engagement* preference (affects satisfaction and completion — real, valuable goals) rather than a comprehension claim.
- **"Escalate the approach" on repeated questions** — intelligent-tutoring-system research has an existing pattern: detect repeats within a session, escalate through a strategy ladder (simplify → concrete example/analogy → diagram) instead of repeating the same explanation.
- **Where per-student history should live** — both Firestore and Qdrant, different jobs, matching how modern LLM memory systems are built (hierarchical session/profile/episodic layers). Redis (exists) = session, Firestore = profile, a new Qdrant collection = episodic per-student history, semantically searchable.
- **How the skill × attitude quadrant should be computed** — maps directly onto the established "Skill-Will Matrix" management framework (skill × motivation, 4 quadrants, each with a different intervention). Rules-based scoring from existing signals is sufficient to start; no ML model needed yet.
- **Global cache vs. per-student layer** — keep layered, not merged. They answer different questions ("has anyone asked this" vs. "has this student covered related ground"), and merging risks one student's context leaking into another's cached answer.
- **New finding, raised in discussion**: the existing global query cache only does exact/near-exact text matching, not semantic matching — "explain photosynthesis" and "how does photosynthesis work" are treated as unrelated. Same class of bug as the per-student memory problem, just at the shared layer — added to the build list as item 7.

---

## 5. Decisions

| Item | Decision | Status |
|------|----------|--------|
| §6.6 — Firestore schema for new personalization data | All new personalization data (preferences, quadrant, per-student history) is written exclusively to the nested `users/{uid}/...` shape. The old flat collections are never used for new data. Cleaning up the *existing* flat-vs-nested inconsistency in unrelated data (`user_stats`, `student_mistakes`, etc.) is tracked as a separate, later task — not a blocker for this build. | **Approved** |
| Implementation order (§6.1–6.7) | Not yet decided — pending this document's sign-off. | Open |

---

## 6. Implementation Plan

Each item: what the change is, why it's being made, and exactly how (files, approach). This is the actual build list — nothing here is implemented yet.

### 6.1 Preference taxonomy at profile level
**What**: capture one of 3 response-style preferences (storytelling / direct / detailed step-by-step) plus self-reported tough/easy subjects, stored on the student's profile.
**Why**: requested directly (§2.1); reframed per research (§4) as a format/engagement preference, not a learning-outcome claim.
**How — Implemented**: since there's no registration UI yet (§2.6), preferences are set on a manually-seeded test user via `profile_service.set_preferences()`, writing `users/{uid}.preferences.{response_style, tough_subjects, easy_subjects}`. Read back via `profile_service.get_profile_context(uid)`, merged into the `student_profile` dict passed to `run_orchestrator_pipeline()`, and formatted into 4 new prompt placeholders (`{student_response_style}`, plus SS6.2-SS6.4's placeholders) in `master_orchestrator_prompt.txt`'s new "PERSONALIZATION CONTEXT" block, with explicit per-style delivery instructions (storytelling/direct/detailed) and an instruction to never describe this to the student as a "learning style."
**Files**: `backend/app/services/personalization/profile_service.py` (new), `backend/app/orchestrator_test/test_runner.py`, `backend/app/orchestrator_test/master_orchestrator_prompt.txt`, `backend/app/api/routes/chat.py`.
**Status**: Implemented, verified live.

### 6.2 Skill × engagement quadrant classification
**What**: classify every student into one of 4 buckets (skill high/low × engagement high/low), refreshed periodically rather than on every turn.
**Why**: CEO's 4-quadrant analogy (§2.1) — lets the system decide "challenge this student more" vs. "this student needs more scaffolding."
**How — Implemented, then corrected after review**: the first pass used a keyword/length heuristic ("why," "compare," "derive," or 14+ words = advanced) — flagged as too weak on review (a student could trivially game it or trip it by accident, and it has no grounding in what the student's grade actually covers). **Fixed**: the orchestrator itself now judges difficulty directly against real curriculum data. `master_orchestrator_prompt.txt` SECTION 3 directive 7 has the LLM set a new schema field, `grade_relative_difficulty` (`below_grade`/`at_grade`/`above_grade`), by comparing the query's conceptual depth against `CURRICULUM_DATA` (the actual chapter summaries) for the student's real grade — e.g. a question needing a concept CURRICULUM_DATA places in a later grade, or asking for a derivation where the grade's own chapter summary only covers the definition, is `above_grade`. `profile_service.record_turn_signals()` now takes this as its primary signal; the old keyword heuristic (`is_advanced_question`) only fires as a fallback when the field is genuinely missing (e.g. a safety-refused turn that never reached generation, or an old cache entry from before this field existed). `skill = high` if ≥30% of a student's questions came back `above_grade`; `engagement = high` if they've asked 5+ questions total (still an honestly-simple v1 proxy — real follow-up-depth/session-frequency signals are future work, not this build). Result written to `users/{uid}.profile.{quadrant, skill, engagement}`, recomputed every turn.
**Files**: `backend/app/services/personalization/profile_service.py`, `backend/app/api/routes/chat.py`, `backend/app/orchestrator_test/master_orchestrator_prompt.txt`, `backend/app/orchestrator_test/test_personalization_cli.py`.
**Status**: Implemented, verified live (both the original heuristic version and the corrected curriculum-grounded version).

### 6.3 Stuck/repeat-question escalation
**What**: detect a student re-asking a simple question on the same topic within their 24h session, and change the explanation approach instead of repeating it.
**Why**: §2.1's repeat-question requirement, given a concrete mechanism by the research in §4.
**How — Implemented, then corrected after review**: the first pass incremented `current_topic_basic_streak` on ANY consecutive basic-phrased question, regardless of topic — flagged on review as a real false-positive risk (e.g. "What is a leaf?" right after "What is the digestive system?" would have wrongly counted as a repeat). **Fixed**: `SmartSessionManager` (`session_service.py`) now also stores `current_topic_streak_anchor` (the question text that started the current streak). Before calling `add_turn()`, `chat.py` calls `session_manager.get_streak_anchor()` and, if a streak is active, compares the new question against that anchor via `qdrant_service.text_similarity()` (in-memory cosine similarity, same 0.30 threshold as §6.4's memory retrieval) — only a genuine topic match increments the streak; an unrelated basic question starts a *fresh* streak of 1 with itself as the new anchor, rather than either wrongly continuing or fully resetting to 0. `get_escalation_level(session_id)` still exposes the current streak; `chat.py` passes it into the prompt as `escalation_level`, which `test_runner.py` turns into an explicit instruction ("escalate to a concrete example/analogy" at 1 repeat, "escalate strongly, favor a diagram" at 2+).
**Files**: `backend/app/services/chat/session_service.py`, `backend/app/services/retrieval/qdrant_service.py` (new `text_similarity()`), `backend/app/services/personalization/profile_service.py`, `backend/app/orchestrator_test/test_runner.py`, `backend/app/orchestrator_test/master_orchestrator_prompt.txt`, `backend/app/orchestrator_test/test_personalization_cli.py`.
**Status**: Implemented, verified live — confirmed both that a genuine repeat escalates (0→1→2) AND that an unrelated basic question in between correctly resets to a fresh streak instead of wrongly continuing it.

### 6.4 Per-student memory — Firestore profile + new Qdrant collection
**What**: Firestore holds the student's profile facts (preferences, quadrant); a **new** Qdrant collection holds the student's own past Q&A turns, retrievable by meaning.
**Why**: the direct fix for the core complaint in §2.4 — a follow-up got answered with zero memory of the student's prior turn.
**How — Implemented**: new Qdrant collection `student_history` (separate from `textbooks_v2` and from the global cache index, per §6.5), auto-created on first use. `qdrant_service.store_student_turn(uid, question, reformulated_question, answer_summary, class_name, subject, topic)` embeds the question+answer-summary and upserts a point with `{uid, question, reformulated_question, answer_summary, class_name, subject, topic, timestamp}`, called after every answer (both fresh-generation and cache-hit paths). `qdrant_service.retrieve_student_history(uid, query, limit=3)` embeds the incoming query and searches filtered to that `uid`, called before generation; its hits get formatted into the prompt's `{per_student_memory_context}` placeholder with an explicit "build on this, don't re-explain" directive.
**Similarity threshold — calibrated against real live data, not guessed**: initially set at 0.55 (cosine, `text-embedding-3-small`); a live test showed genuinely related-but-differently-worded questions ("difference between digestive and circulatory systems" vs. an earlier "what is digestion?") scoring only 0.40, while a clearly unrelated control topic scored 0.10-0.13. Recalibrated to **0.30**, which sits with margin on both sides of that real gap.
**Files**: `backend/app/services/retrieval/qdrant_service.py`, `backend/app/api/routes/chat.py`, `backend/app/orchestrator_test/test_runner.py`.
**Status**: Implemented, verified live end-to-end — a 3-turn sequence correctly retrieved 0, then 1, then 2 prior turns on the digestive/circulatory-system example that originally motivated this project (§2.4).

### 6.5 Keep the global cache and per-student layer separate
**What**: both checks run independently before every answer; neither is folded into the other's storage.
**Why**: §2.4's explicit warning, confirmed by research (§4).
**How — Implemented**: in `chat.py`, `check_global_query_cache()` and `retrieve_student_history()` (§6.4) are two separate calls with two separate result variables, each passed as its own labeled field into `student_profile`/the prompt — never merged into one lookup or one cache write. A single decision-trace log line prints both outcomes together per turn (see §7) so the separation is visible, not just structurally true.
**Files**: `backend/app/api/routes/chat.py`.
**Status**: Implemented, verified live.

### 6.6 Firestore schema for new personalization data — Approved (§5)
**What**: all new personalization writes/reads target the nested `users/{uid}/...` shape exclusively.
**Why**: prevents the new profile/quadrant/history data from becoming a third inconsistent home for user data, on top of the existing flat-vs-nested split (§3).
**How — Implemented**: every write in `profile_service.py` targets `users/{uid}` map fields (`preferences`, `profile`, `signals`) only — no old flat collection is touched. Cleanup of the *existing* inconsistency remains a separate, later, explicitly tracked task, unaffected by this build.
**Files**: `backend/app/services/personalization/profile_service.py`.
**Status**: Implemented.

### 6.7 Semantic upgrade of the global query cache
**What**: change the existing global cache from exact/near-exact text matching to meaning-based (semantic) matching.
**Why**: raised in discussion (§4) — the cache matched on normalized string equality only, missing same-intent questions worded differently.
**How — Implemented**: new Qdrant collection `global_answer_cache_index` (no `uid` field at all — genuinely global, per §6.5's separation rule). `qdrant_service.index_global_cache_entry()` is called from `save_to_global_query_cache()` right after every cache write, indexing the raw question text against the Firestore doc's ID. `check_global_query_cache()` now tries the existing exact-text match first (unchanged, still the fast path); only on a miss does it call `qdrant_service.find_semantic_cache_match()`, which returns a candidate `doc_id` if similarity clears the bar, then fetches and validates that specific Firestore doc through the same TTL/content/video-file checks the exact-match path already used.
**Similarity threshold — calibrated against real live data**: a real paraphrase pair ("explain photosynthesis" vs. "how does photosynthesis work") scored 0.68-0.73; an unrelated topic scored 0.23. Set to **0.62** — deliberately the *stricter* of the two thresholds in this build, since a false positive here means replaying a possibly-wrong cached answer, a materially worse failure mode than a per-student memory miss.
**Files**: `backend/app/services/retrieval/qdrant_service.py`, `backend/app/core/firestore_service.py`.
**Status**: Implemented, verified live (calibration queries only — not yet exercised through a full duplicate-question chat turn; see §8 known gaps).

---

## 7. How to Test This — and Where the Logs Are

### 7.0 Where to look while testing (the dedicated log folder)

**`consolidated_deployment_outputs/chat_modes/{YYYY-MM-DD}_user_logs.jsonl`** — this is the one place to check. One JSON line per real chat turn (append-only, a new file each day), including:
```json
{
  "timestamp": "...", "uid": "...", "session_id": "...", "mode": "smart_query_fresh | smart_query_cache_hit",
  "user_query": "...", "subject": "...", "llm_response": "...", "execution_time_ms": 0,
  "personalization": {
    "preference": "storytelling|direct|detailed|null",
    "quadrant": "low_skill_low_engagement|...|null",
    "escalation_level": 0,
    "per_student_hits": 0,
    "global_cache_hit": true,
    "grade_relative_difficulty": "below_grade|at_grade|above_grade|null"
  }
}
```
**This folder already existed before this project** (`deployment_logger.py`), but had gone stale since 2026-07-22 — it was only ever wired to an older endpoint (`/api/query`), not the live `/api/smart_query` path everything here runs through, so nothing from real testing was landing there. Fixed this session: `save_chat_log_background()` is now also called from both the cache-hit and fresh-generation branches of `/api/smart_query`, personalization data included. Verified live.
**Files**: `backend/app/services/deployment_logger.py`, `backend/app/api/routes/chat.py`.

### 7.1 The live decision-trace log line
Every turn also prints one line to the server's stdout/terminal —
```
[PERSONALIZATION TRACE] uid=... preference=... quadrant=... escalation_level=... per_student_hits=N global_cache_hit=YES/NO
```
— useful for watching in real time while the server is running, but not durable (use §7.0's JSONL file to review after the fact).

### 7.2 Standalone test harness (no frontend, no live server needed)
```bash
python -m backend.app.orchestrator_test.test_personalization_cli
```
Seeds a test user (`users/test_student_personalization_demo`, class 6, `storytelling` preference), starts a fresh 24h session, and fires 3 scripted questions: a basic question, a repeat of it worded differently, then the exact digestive/circulatory follow-up from §2.4 — printing the trace line and a preview of each answer. Override with `--uid`, `--class`, `--response-style`, or `--questions "q1" "q2" ...` for a different scenario.

**What "testing one real user end-to-end" (per your workflow) looks like**: run the harness (or the real app) for a fresh `uid`, then inspect `users/{uid}` in Firestore directly — you'll see `preferences`, `signals`, and `profile.quadrant` fields appear and update turn by turn — and the `student_history` Qdrant collection filtered by that `uid` for the stored turns. No separate dashboard was built for this round; the Firestore doc itself is the inspectable state.

---

## 8. Known Gaps (honest, not hidden)

- **Quadrant `engagement` signal is a simplification**: currently just "5+ total questions asked," not real session-frequency or follow-up-depth tracking. Documented in §6.2 as a v1 placeholder.
- **`is_basic_question` is still a phrasing heuristic** (short / "what is X" style), used only to decide whether to check for a repeat at all — not a complexity judgment. The complexity/skill judgment itself (§6.2) is now curriculum-grounded, not heuristic (fixed after review — see §6.2, §6.3, and the change log).
- **§6.7's semantic cache fallback was calibration-tested (real paraphrase vs. unrelated scores) but not yet exercised through an actual duplicate chat turn** in this session — the mechanism is live and the threshold is evidence-based, but end-to-end confirmation through a real repeated-but-reworded question in the live app is still worth doing.
- **Test data was left in place, not deleted**: `users/test_student_personalization_demo` (and `_demo2`), plus their `student_history` Qdrant points, exist in the shared Firebase/Qdrant project from live verification runs this session. Low risk (isolated by uid, clearly named as test data), but worth knowing they're there.
- **No registration UI** — preferences are still only settable via `profile_service.set_preferences()` directly or the test harness, per §2.6's explicit deprioritization.
- **The video/text format tiebreaker (§12) has an unconfirmed real-world effect** — implemented and correctly scoped, but every synthetic test run so far found the underlying model too decisive to ever hit a genuine tie for it to break. Needs observation against real, varied student usage, not more synthetic query guessing.
- **Topic-tagged feedback memory (§12) is verified for plumbing and for producing a real behavioral shift, but not yet verified against a real student's real dislike** — all confirmation so far used synthetic test profiles/questions.
- **Feedback data collected before Round 8's fix is permanently lost** — every 👍/👎 given before today silently failed to save (the `/api/feedback` 500 bug), so the engagement-from-feedback signal (§9b item 1) has no real history to draw on yet; it starts accumulating from today forward only.

---

## 9. Open Items Requiring Sign-off

None currently blocking — all 7 items in §6 are implemented and verified live. Next natural step is either a broader test pass (more students, more question patterns) or closing the gaps in §8, at your direction.

---

## 9a. Round 3: registration correction, video personalization, daily refresh

**Correction to an earlier claim in this document**: §3 originally said "no registration UI exists." That was **wrong** — carried over from an assumption never actually checked against this repo's `public/` folder. The real state: there IS a login flow (`public/auth.js`, `signInWithEmailAndPassword`) plus an automatic "Complete Your Profile" modal (`public/auth-modal.js` + `index.html`) that already fires on first login for any account missing class/avatar, driven by `authManager.needsProfileSetup()`. There is **no self-serve account-creation (signup) flow** — accounts must still be created directly in Firebase Authentication (console or Admin SDK) — but everything after that (name/class/avatar) is already self-service via the existing modal.

**What changed this round**:
1. **Response-style preference added to the existing "Complete Your Profile" modal** — a 3rd step (📖 Storytelling / ⚡ Direct / 📋 Detailed) alongside the existing avatar and class steps, saved via the same direct-client Firestore write pattern (`authManager.updateUserProfile()`) already used for class/avatar — no new backend endpoint needed, since it writes to the exact `preferences.response_style` shape `profile_service.get_profile_context()` already reads. `needsProfileSetup()` now also gates on this field, so existing accounts get re-prompted once to set it. **This is also the direct answer to "does manually creating a Firebase Auth user trigger profile collection"**: yes — manual Auth creation is sufficient, because the *existing* app already detects an incomplete profile on first login and blocks progress until class/avatar/preference are filled in. No new "is this a new user" flag was needed; it was already solved by checking which Firestore fields are populated.
   **Files**: `public/index.html`, `public/auth-modal.js`, `public/auth.js`.
   **Status**: Implemented; markup verified to render correctly (3 buttons present) via a static browser check. **Not yet verified through an actual live login** (needs a real Firebase account + running server — the static check confirms structure, not the full interactive flow).
2. **Video/storyboard pipeline now receives personalization context — closing the gap flagged earlier**: `generate_visual_lesson_stream()` and `get_visual_lesson_prompt()` now accept `student_profile`, and a new `_build_video_personalization_block()` injects the student's response-style preference and a summary of their related prior learning into the storyboard-generation prompt — the same two highest-value signals the text path already had, now reaching video lessons too. Quadrant/escalation were deliberately left out of this pass (lower value for video specifically; can be added the same way later if wanted).
   **Files**: `backend/app/services/visual_learning/visual_learning_service.py`, `backend/app/services/visual_learning/visual_lesson_prompt.py`, `backend/app/api/routes/chat.py`.
   **Status**: Implemented; the block-builder function verified directly (produces correct text for both a populated and an empty profile). **Not yet verified through an actual generated video** (Sarvam TTS/full video rendering wasn't exercised this round, consistent with earlier sessions' sandbox limitation).
3. **Daily-refresh endpoint** — addresses the "collect today's data, categorize, feed into tomorrow" idea, with one architectural correction worth being explicit about: the skill x engagement quadrant already recomputes **live, every turn** (§6.2) — that's strictly more current than any nightly batch could be, so this isn't "turning on" personalization on a delay. What a daily job adds on top is a **dated snapshot** for inspectability (an actual history of how a student's classification changed day to day) and a concrete integration point if you later want batch-style processing. New endpoint: `POST /api/personalization/daily-refresh` (single `uid`, or a bounded batch across all students). **This code cannot make itself fire at midnight IST** — nothing in a request-driven backend can guarantee wall-clock execution; making it actually run daily means pointing an external scheduler (e.g. Google Cloud Scheduler) at this endpoint once deployed. That wiring is an infra/ops step, not a code change, and is not done.
   **Files**: `backend/app/api/routes/personalization.py` (new), `backend/app/api/routes/__init__.py`, `backend/app/main.py`.
   **Status**: Implemented, verified live — ran against a real test uid and correctly wrote a dated snapshot matching that student's actual computed quadrant.

**Also corrected in §6.2/§6.3 this session** (see those sections and the change log): the skill signal is no longer keyword-based (now grounded in real curriculum data via the orchestrator itself), and the repeat-question streak is no longer "any consecutive basic question" (now checked for actual topic similarity) — both were raised as valid concerns and fixed, not just discussed.

---

## 9b. Round 4: a real gap-analysis pass against the original requirements (§2)

Requested explicitly: "are there really no more gaps" — so this round re-read §2's original requirements line by line against what's actually wired up, rather than only reacting to specific complaints. Found 5 real gaps. 3 were small and well-scoped enough to fix immediately; 2 are genuinely bigger and need a scope decision, not a silent fix.

### Fixed this round

1. **Feedback (👍/👎) was never wired into the quadrant, despite §2.1 explicitly listing it as a profile-building signal.** The feedback endpoint (`POST /api/feedback`) already saved likes/dislikes to Firestore, but nothing ever read that data back into personalization - pure infrastructure with no effect. Fixed: `profile_service.record_feedback_signal(uid, is_positive)` increments `signals.{positive,negative}_feedback_count`; `submit_feedback` in `chat.py` calls it (deriving `uid` from the query doc's own path, since the feedback request doesn't carry one) and recomputes the quadrant immediately. `compute_quadrant()`'s engagement calculation now downgrades to "low" if a student has 3+ feedback events and half or more are negative, even if their question *volume* alone would suggest high engagement - a student who keeps asking but keeps disliking the answers isn't the CEO's "engaged" quadrant member, they're frustrated. **Verified live**: a test user with 5 questions (volume-qualifying as high engagement) correctly dropped to `low_engagement` after 3 negative feedback events.
   **Files**: `backend/app/services/personalization/profile_service.py`, `backend/app/api/routes/chat.py`.

2. **`tough_subjects`/`easy_subjects` were collected by `set_preferences()` but never actually used anywhere** - not merged into `student_profile`, not read by the prompt. A field that existed in the schema and did nothing. Fixed: merged into `student_profile` in `chat.py`, new `{tough_easy_subjects_note}` prompt placeholder, and directive 8 in `master_orchestrator_prompt.txt` telling the LLM to add more scaffolding for a tough subject or move faster for an easy one. **Verified live.**
   **Files**: `backend/app/api/routes/chat.py`, `backend/app/orchestrator_test/test_runner.py`, `backend/app/orchestrator_test/master_orchestrator_prompt.txt`, `backend/app/orchestrator_test/test_personalization_cli.py`.

3. **§2.2's explicit requirement - "a stated preference is a default, not a lock" - was never actually written into the prompt as a rule.** The LLM might have honored an inline request like "explain in detail" over a stored "direct" preference incidentally, but nothing told it to. Fixed: added an explicit OVERRIDE RULE under directive 3 in `master_orchestrator_prompt.txt`.
   **Files**: `backend/app/orchestrator_test/master_orchestrator_prompt.txt`.

### Found, NOT fixed - genuinely need your scope call, not a silent build

4. **The app has a second, entirely separate live chat pipeline that none of this personalization work touches.** `public/conversation.js` connects to a WebSocket (`/ws/conversation/{id}`), handled by `conversation_manager.process_query()` (`backend/app/services/chat/conversation.py`) - a completely different code path from the SSE `/api/smart_query` route (`chat.py`) that everything in this project was built against. If that WebSocket mode is an actively-used feature (looks voice/live-conversation-oriented based on its interrupt-handling), **every single thing built in this project - preference, quadrant, escalation, per-student memory, semantic cache - is invisible to it.** I have not touched it, on purpose, since duplicating this entire mechanism into a second transport layer is a real second project, not a small addition. Need to know: is this WebSocket path actively used in production, and if so, is extending personalization to it in scope now or later?

5. **Preferred output format (text / text+audio / text+audio+video) from §2.1 was never built at all** - not collected anywhere, not read anywhere. This one was explicitly tied to tiering in the original requirements (§2.5, "tier-dependent") which the CEO deprioritized for this build phase - so leaving it unbuilt is arguably correct, not an oversight, but naming it explicitly so it's a documented decision rather than something that quietly fell through.

**Recommendation**: treat items 4 and 5 as the next explicit decision point, not something to build reactively. Item 4 in particular could be a substantial second phase of work if the WebSocket mode turns out to be live and important.

**Resolution on item 4**: investigated further - the WebSocket conversation mode is **not** dead code (contrary to the initial assumption it might be). It has a real, currently-reachable trigger path (Sidebar → "Switch Mode" → `/mode-selection` → "Start Conversation" button → WebSocket → `conversation_manager`), and its files were actively edited within the last ~2 weeks (`mode-selection.html` is the single most recent commit in the repo). Decision: **do not extend personalization to it, and do not delete it now** - it's slated for deletion later as a separate, deliberate cleanup task, at which point this gap becomes moot. Not touching it further in this project.

---

## 10. Change Log

- Document created. Raw requirements captured from CEO call + same-day follow-up (registration/login/parent-dashboard deprioritized; focus is the adaptive intelligence layer using a manually-seeded test profile). Current-system assessment completed. No design decisions made yet.
- Research pass completed on all open questions (§4). New finding: the existing global query cache is exact-text-match only, not semantic — added as a 7th build item. Full implementation plan written (§6), each item with concrete files and approach.
- Document restructured for clarity (executive summary, decisions table, numbered implementation plan). §6.6 (Firestore schema for new personalization data) formally approved.
- **All 7 items implemented and verified live against the real (shared) Firebase/Qdrant project**: new `backend/app/services/personalization/profile_service.py` module; `session_service.py` extended for repeat-question escalation; two new Qdrant collections (`student_history`, `global_answer_cache_index`); `chat.py` wired to read/write all of it plus a per-turn decision-trace log line; `master_orchestrator_prompt.txt`/`test_runner.py` extended with a "PERSONALIZATION CONTEXT" prompt block. Both new similarity thresholds were calibrated against real embedding scores from live test data, not left at guessed defaults. New standalone test harness: `backend/app/orchestrator_test/test_personalization_cli.py`. See §8 for honest known gaps.
- **Two design gaps found on review, fixed same day**: (1) §6.2's skill signal was a crude keyword/length heuristic with no grounding in real curriculum difficulty — replaced with a new orchestrator-emitted `grade_relative_difficulty` field, judged directly against `CURRICULUM_DATA` for the student's actual grade. (2) §6.3's repeat-question streak incremented on ANY consecutive basic-phrased question regardless of topic — fixed by tracking a streak anchor question and requiring semantic similarity (via a new `qdrant_service.text_similarity()` helper) before continuing the streak. Both fixes verified live, including a specific test proving an unrelated basic question no longer wrongly continues someone else's repeat streak.
- **Round 3 (§9a)**: corrected a wrong earlier claim that no registration UI existed (it does — an existing "Complete Your Profile" modal, extended with a response-style preference step). Video/storyboard generation now receives personalization context (preference + prior learning), closing a gap where video lessons got none of this project's work. New `POST /api/personalization/daily-refresh` endpoint for dated profile snapshots (live quadrant updates already exist; this adds an inspectable history on top — actual midnight-IST scheduling is an infra step, not code).
- **Round 4 (§9b)**: a full line-by-line gap-analysis against the original §2 requirements found 5 real gaps. Fixed 3: feedback (👍/👎) now actually feeds the engagement calculation (previously collected, never used); `tough_subjects`/`easy_subjects` now actually reach the prompt (previously dead fields); the explicit "stated preference is a default, not a lock" requirement is now a real prompt rule (previously unwritten). Investigated and deliberately left alone: the separate WebSocket "conversation mode" pipeline — confirmed live and recently maintained (not dead code), decision made not to extend personalization to it or delete it now (user will handle deletion later as its own task). Preferred output format (text/audio/video) remains unbuilt, correctly, since it's tied to the already-deprioritized tiering work.
- **Logging fix**: the pre-existing consolidated log folder (`consolidated_deployment_outputs/chat_modes/`) had gone stale since 2026-07-22 because it was wired to an old endpoint, not the live one this project uses. Now written on every turn (both cache-hit and fresh-generation) with full personalization data — see §7.0 for the exact format and location.
- **Round 7 (UI)**: fixed the congested response-style picker in the profile-completion modal (`public/index.html`, `public/auth-modal.css`) — it had been reusing `.class-btn` (a 60x60px box built for a single digit) for an icon+word combo, causing visible text overlap. Replaced with a dedicated `.style-option` card (icon, title, one-line description of what each style produces) - verified via a real running server, no overlap, 387x67px per card. Also added a **"Change Answer Preferences" item** to the Additional Settings menu (`public/user.html`) so a student can change their response style at any time, not just once at signup — writes straight to Firestore via the existing `authManager.updateUserProfile()`, applies from the very next question since preference is read fresh every turn. Dark-theme version of the same card component added to `conversation.css` (already loaded on `user.html`) so it matches the app's theme instead of reusing the light-themed signup modal's styling. Verified live end-to-end (pre-select current preference, change selection, save, confirm payload) against a running server, with one real testing pitfall caught and worked around: `auth.js` declares `authManager` as a top-level `const`, which is a separate lexical binding from `window.authManager` - a naive `window.authManager = ...` test stub silently doesn't reach code that references the bare `authManager` identifier (which is the correct, established pattern already used everywhere else in this codebase).
- **Round 6**: while hand-verifying exact expected log values for a new test guide (before any live failure was reported), found and fixed a real bug: `escalation_level` fed to the prompt reflected the streak *going into* a turn, not whether *this specific turn* actually continued it - so a genuinely off-topic question right after a long same-topic streak would still tell the LLM "escalate strongly" instead of 0. Fixed by gating on `is_same_topic_as_streak` (`backend/app/api/routes/chat.py`, `backend/app/orchestrator_test/test_personalization_cli.py`). Verified live: the exact electricity→electricity→electricity→rainwater sequence now correctly logs `escalation_level = 0, 1, 2, 0`. Separately, while hunting for a safe "above-grade" test example, found (but did NOT fix, out of scope/high-stakes to touch casually) a pre-existing false positive in the child-safety classifier: compound, multi-clause advanced academic questions (e.g. "explain X using Y theory and Z theory") get refused as UNAUTHORIZED consistently, even for benign Class 10-12 physics/chemistry content, regardless of grade - confirmed via 3 repeated runs, not flaky. Simple single-clause phrasing of the same topics passes fine. Flagged for separate, careful investigation - this is SECTION 2 of the prompt (child safety), not something to patch quickly alongside personalization work.
- **Round 5 (§11)**: analyzed 11 real user turns (uid `praneeth10@cg.com`, Class 10) against a formal test guide, without re-running any questions. Found and fixed 3 real bugs: (1) session continuity was completely broken app-wide - the backend never told the frontend its session_id, so escalation tracking never worked in the real app despite working in isolated tests; fixed by emitting a `session` SSE event. (2) response-style preference and inline overrides were being silently ignored by the LLM despite two rounds of prompt-wording fixes that were verified NOT to work - replaced with a dedicated, isolated restyle pass, verified live across 3 scenarios. (3) the durable log lacked `classification`/`format_decision`, made diagnosis harder than necessary - added.
- **Round 8 (§12)**: correction - §9b item 1's feedback-signal wiring was "verified live" only via test harness; the real `POST /api/feedback` endpoint had been crashing with a `500` on every single call, for every student, the entire time (a Firestore collection-group document-ID filter bug), so no real feedback had ever actually reached the quadrant despite the code being correct. Fixed. New: feedback is now stored as topic-tagged searchable memory (not just a blind counter), resurfacing as a binding `MANDATORY FEEDBACK REQUIREMENT` on a related future question - verified to produce a real, repeatable structural change in testing, confirmed to generalize across topics, but not yet confirmed against a real student's real dislike. New: a video/text format-decision tiebreaker using the skill/engagement quadrant for genuinely ambiguous cases - implemented, but testing found zero genuine ties to actually trigger it, so its real-world effect is unconfirmed either way. Also fixed 2 unrelated but load-bearing bugs found along the way: a Firestore write bug that silently dropped entire video-question records under a specific data shape, and a TTS cache that had been silently non-functional (always refetching from Sarvam) since it was built.

---

## 11. Round 5: real-user log analysis (uid `praneeth10@cg.com`, Class 10) against `class10_personalization_test_guide.md`

The user ran 11 real turns through the actual live UI and provided a formal test guide with expected outcomes per scenario. This round analyzed the **existing** logs and Firestore state (no re-running of the test questions, per instruction) against those expectations, found 3 confirmed, real, reproducible bugs, root-caused each one precisely, and fixed and verified all 3.

### Coverage note (be precise about what was actually tested)
Only Scenarios 1, 2 (partially), 3 (partially), 4, and 5 (partially) had matching questions in the log. Scenario 6 (semantic cache, needs 2 users) and Scenario 7 (tough/easy subjects) were never exercised — Firestore confirms no `tough_subjects`/`easy_subjects` were ever set for this user, and no matching questions were asked. This is a coverage gap in the test run itself, not a confirmed defect — those 2 scenarios remain unverified either way.

### Bug 1 (CONFIRMED, most severe) — session continuity was completely broken, silently disabling all session-scoped personalization
**Evidence**: every one of the 11 log entries had a *completely different* `session_id` (`global_{timestamp}_{random}`), even for turns seconds apart in the same browsing session. Scenario 4's escalation test (turns 7-10: "What is electricity?" → "Define electric current." → "Explain electricity simply." → "What is rainwater harvesting?") should have shown `escalation_level` go 0→1→2→0; the real logs showed **0, 0, 0, 0 for all four turns**.
**Root cause**: `backend/app/api/routes/chat.py`'s `/api/smart_query` endpoint never sent a `{"type": "session", "session_id": ...}` SSE event back to the browser. `public/script.js` (line ~854) has always been *written* to capture and persist `session_id` from exactly that event shape — but since the backend never sent it, the frontend's `_sessionId` variable stayed `null` forever, so every request went out with no session_id, and `session_manager.get_or_create_session(book_uuid, None)` created a **brand new Redis session on every single turn**. This is a pre-existing gap in the app's session wiring, not something introduced by this project — but it silently disabled escalation tracking (and likely other session-scoped behavior) for the entire life of the feature. Per-student memory (`student_history`, Qdrant, keyed by `uid` not `session_id`) was unaffected and confirmed working correctly in the same logs (`per_student_hits` climbed 0→1→2→3 correctly across those same 4 turns).
**Fix**: `chat.py` now yields the session event as the very first thing in the stream, right after the session is created: `yield f"data: {json.dumps({'type': 'session', 'session_id': session['session_id']})}\n\n"`.
**Files**: `backend/app/api/routes/chat.py`.
**Status**: Fixed, syntax/import-verified. Full live confirmation requires a real browser session against a running server (not exercised in this sandbox) — **please re-run Scenario 4 for real after this deploys** to get the final confirmation.

### Bug 2 (CONFIRMED) — response-style preference and inline overrides were being silently ignored
**Evidence**: with `response_style: "storytelling"` set, "What is Ohm's Law?" and "characteristics of an AP" both came back as flat, dry bulleted facts — zero analogy, zero narrative, matching neither the stored preference nor the Scenario 1 expected output. Scenario 2's inline-override test ("...in detailed step-by-step points") came back as flowing prose, not numbered steps — matching neither the stored preference NOR the inline request.
**Root cause**: `master_orchestrator_prompt.txt` SECTION 5's QUICK_ANSWER format contract was phrased as an unconditional "CRITICAL... MUST be formatted as a bulleted list," with no carve-out for style preference — a stronger, more emphatic instruction than the softer style directive elsewhere in the same (very long, many-competing-instruction) prompt, so it won every time.
**Fix attempts and what actually worked** (documented honestly, not just the final answer): first tried strengthening the wording in-place (marking directive 3 "CRITICAL, MANDATORY," adding a carve-out to the bullet rule) — **verified live, did not work**, model behavior unchanged. Second tried adding a forceful reminder immediately adjacent to the JSON schema itself — **verified live, still did not work**. Concluded the single main orchestrator call has too many simultaneously-competing instructions (safety, classification, RAG grounding, JSON mechanics, style) for reliable style compliance from this model. **Real fix**: a new, separate, narrowly-scoped second LLM call (`restyle_text_narration()` in `test_runner.py`) whose only job is to rewrite already-correct `text_narration` into the requested style (storytelling → open with a concrete analogy; detailed → literal numbered steps) - explicitly forbidden from changing any fact. Only fires for `QUICK_ANSWER` when the effective style is `storytelling` or `detailed`. New `detect_inline_style_override()` checks the raw query text for explicit style requests ("step by step," "storytelling," "quick answer," etc.) and takes precedence over the stored preference, closing Scenario 2's gap too.
**Verified live** (fresh test uids, not the real user's data): Ohm's Law + storytelling → *"Imagine a narrow stream winding its way through a forest, where the flow of water represents the current..."* (real analogy). Salt formula + detailed → literal `1. 2. 3. 4. 5.` numbered steps. Coordinate grid + stored `direct` + inline "storytelling analogy" request → a full analogy response, correctly overriding the stored default.
**Known residual gap**: the video/storyboard personalization block (`_build_video_personalization_block`, §9a item 2) still only reads the stored preference, not `detect_inline_style_override()` — lower priority since none of the real failures analyzed were video-format, but worth closing later for consistency.
**Files**: `backend/app/orchestrator_test/test_runner.py`, `backend/app/orchestrator_test/master_orchestrator_prompt.txt`.
**Status**: Fixed and verified live across 3 distinct scenarios (stored storytelling, stored detailed, inline override beating a stored default).

### Bug 3 (observability gap, not a behavior bug) — the durable log didn't capture enough to diagnose failures without guessing
The JSONL log had `mode` but not `classification`/`format_decision`, so confirming *why* a response looked a certain way required inferring from response text shape alone. Fixed: both `classification` and `format_decision` are now included in the `personalization` object written to `consolidated_deployment_outputs/chat_modes/*.jsonl`.
**Files**: `backend/app/api/routes/chat.py`.

### What was NOT found to be a bug (checked and ruled out)
- Scenario 3's quadrant computation logic itself: verified correct given the actual signals present (engagement flipped to `high` exactly at the turn where `total_questions` crossed 5, matching the documented rule precisely). The reason `skill` stayed `low` is that the real test never asked either of Scenario 3's two designed above-grade questions (Q4 circle-tangent proof, Q5 organic mechanism) — a test-coverage gap, not a classification bug.
- `grade_relative_difficulty` classifications for the 11 real questions asked (`at_grade`/`below_grade` throughout) are all directionally correct for genuinely basic-to-at-grade NCERT recall questions.

---

## 12. Round 8: the feedback pipeline was silently broken in production this whole time; new topic-tagged feedback memory; video/text format tiebreaker

Built while doing unrelated History-page work, in response to a real student-facing error found in production logs. Includes a correction to §9b item 1's implicit claim, plus 3 new pieces of personalization work with honestly mixed confirmation status — not everything here is "verified live" to the same standard as earlier rounds, and this section says exactly which is which.

### Correction to §9b item 1 — feedback had never actually reached the quadrant in production, despite being "Verified live" there

§9b item 1 describes `record_feedback_signal`/`compute_quadrant` wiring as verified live — true for isolated test-harness calls, but **misleading about real-world state**: `POST /api/feedback` (`chat.py`) was crashing with a `500` on every single call, for every student, the entire time, including before and after §9b was written. Root cause: it queried Firestore with `collection_group("queries").where(FieldPath.document_id(), "==", query_id)` — a bare string document-ID filter, which Firestore rejects on a collection-group query with `400 __key__ filter value must be a Key` (it needs a full document reference there, not a plain ID). This means **every 👍/👎 any student has ever clicked, before this fix, silently vanished** — the feedback UI appeared to work (no visible frontend error), but nothing was ever saved, so `record_feedback_signal`/`compute_quadrant`/engagement-from-feedback (§9b item 1) had zero real data to work with in production despite being correctly wired in code.
**Fix**: the frontend now sends `uid` with the feedback request (`public/script.js`); the backend does a direct `users/{uid}/queries/{doc_id}` lookup instead of the broken collection-group document-ID query.
**Files**: `backend/app/api/routes/chat.py`, `public/script.js`.
**Status**: Fixed and verified live — real feedback click confirmed to actually persist to Firestore (previously would 500 every time). From this point forward, not retroactively, §9b item 1's engagement-from-feedback logic finally has real data reaching it.

### New: topic-tagged feedback memory — a disliked/liked answer now resurfaces on a related future question

**What**: previously, feedback only ever fed a blind aggregate counter (`positive_feedback_count`/`negative_feedback_count`) — enough to detect "this student seems frustrated lately," but with no memory of *what* was disliked or why. New: `qdrant_service.store_feedback_note(uid, question, class_name, subject, topic, is_positive, reason)` upserts the feedback as its own point in the *same* `student_history` Qdrant collection §6.4 already uses, embedded on the original question's text. `retrieve_student_history()` (already called before every orchestrator turn, §6.4) now also returns these feedback points via the exact same semantic search — no separate lookup, no chicken-and-egg problem about knowing the new question's topic in advance, since it reuses the retrieval that was already running.
**Formatting into the prompt** (`test_runner.py`): a feedback point with a concrete reason renders as a `MANDATORY FEEDBACK REQUIREMENT` line — explicitly a binding constraint on the current answer, not background context to weigh ("if they asked for a real-world example, your answer MUST contain one"). This is a strengthened second pass — the first version (a soft "try a different approach" nudge) was tested and found to reliably shift the answer's *structure* but not reliably fulfill the *specific* request (asked for a real-world example, didn't get one). `master_orchestrator_prompt.txt` SECTION 3 directive 6 also now explicitly says a `MANDATORY FEEDBACK REQUIREMENT` line outranks SECTION 5's default format rules when they'd conflict.
**Files**: `backend/app/services/retrieval/qdrant_service.py` (`store_feedback_note`, and `retrieve_student_history`'s return shape extended with `is_feedback`/`feedback_type`/`feedback_reason`), `backend/app/orchestrator_test/test_runner.py`, `backend/app/orchestrator_test/master_orchestrator_prompt.txt`, `backend/app/api/routes/chat.py` (`/api/feedback` now calls `store_feedback_note` after saving).
**Status — verified, with an honest nuance**: end-to-end plumbing (store → semantic retrieval → prompt formatting) confirmed working via direct Qdrant queries. Behavioral effect confirmed **real, not placebo**: a 3-trial-vs-3-trial comparison on "What is Ohm's Law?" with a negative note attached ("I want a real-life example, not just the formula") showed **consistent, repeatable structural difference** — every "without" trial led with the raw formula, every "with" trial led with a conceptual/proportionality description instead — but did **not** reliably add the specific real-world analogy that was asked for, which is exactly what motivated the MANDATORY strengthening above. Confirmed to generalize to a second, unrelated topic (chemistry) with zero code changes required. **Not yet confirmed against a real student's real dislike** — all trials so far used synthetic test profiles; real-world confirmation is the deliberate next step, not done in this round.

### New: video/text format_decision personalization tiebreaker — implemented, effect NOT yet confirmed

**What**: `master_orchestrator_prompt.txt` SECTION 5 gained a 3rd directive: when a query is genuinely borderline between `QUICK_ANSWER` and `VIDEO_REQUIRED` (not a clear-cut case either way), lean `VIDEO_REQUIRED` for `low_skill_*` students and `QUICK_ANSWER` for `high_skill_*` students. Explicitly scoped as a tiebreaker, not an override — does not touch obviously-simple or obviously-complex queries regardless of quadrant.
**Why this exists**: raised directly — students asking simpler/more basic questions are plausible candidates for more visual explanations, and the skill/engagement quadrant (§6.2) already exists and already reaches the prompt, just never used for this decision before.
**Status — implemented, but testing found NO measurable effect so far**: tested across 4 different candidate "borderline" queries, low_skill vs. high_skill profile each — identical `format_decision` in every single case. Follow-up test (6 queries × 4 repeat trials each, no quadrant variation) found **zero genuine ties** — GPT-4o-mini had a confident, 100%-consistent answer for every query tried, meaning the tiebreaker never actually had an opportunity to activate in any test run. This does not mean it's broken (it's correctly scoped to only act on genuine ties), but it means **its real-world impact is unconfirmed** — plausibly rare-to-never triggered at this model/temperature, or plausibly effective on tie cases these tests simply didn't find. Left in place (inert cost when not triggered) pending observation against real, varied student usage rather than more synthetic query guessing.
**Files**: `backend/app/orchestrator_test/master_orchestrator_prompt.txt`.

### New: QUICK_ANSWER vs. VIDEO_REQUIRED procedural-intent clarification — verified live

**What**: SECTION 5's two directives previously had a real gap: a query like "explain step by step how to calculate the nth term of an AP" could be read as either "a single formula" (→ `QUICK_ANSWER`) or a multi-step process (→ `VIDEO_REQUIRED`), with no tiebreaker between them — found via a real production case where a clearly step-by-step question got a text-only answer. Fixed with an explicit distinction: `QUICK_ANSWER`'s formula bullet now only covers being *told* the formula/value; `VIDEO_REQUIRED` gained an explicit trigger for step-by-step/procedural language ("step by step," "how do I calculate/derive/solve," "walk me through") even when the underlying content is a single formula.
**Status — verified live**: the exact production example that motivated this now correctly returns `VIDEO_REQUIRED` (previously `QUICK_ANSWER`), confirmed consistent across a repeat run. Confirmed no overcorrection: "What is the formula for the nth term of an AP?" (genuinely just asking for the formula, no procedural intent) correctly still returns `QUICK_ANSWER`.
**Files**: `backend/app/orchestrator_test/master_orchestrator_prompt.txt`.

### Also fixed this round, adjacent but not personalization-specific
While testing the above, found and fixed 3 more real bugs surfaced through this same testing: (1) a Firestore write for an entire video-question's data was silently failing (`400 Property storyboard_data contains an invalid nested entity`) whenever a video's scene data contained a list-of-lists (e.g. a diagram template's coordinate-pair `connections` field) — fixed with a recursive sanitizer in `analytics_service.log_query()`, plus a fallback that retries without `storyboard_data` rather than losing the whole question if anything else about it is ever rejected. (2) The TTS Redis cache (meant to avoid re-billing Sarvam for identical text) had been silently no-op'ing since it was built — `redis_service.r` decodes every read as UTF-8, but the cache was writing raw binary audio bytes, so every read threw and fell through to a fresh, billed synthesis call, every time, for the whole app. Fixed by base64-encoding before writing. (3) Generated videos were disappearing after a server restart/redeploy despite already being backed up to Supabase at generation time — the serving route only ever used the local-disk copy, never fell back to the cloud backup that was sitting right there; fixed with a checked redirect fallback. None of these are personalization mechanisms themselves, but (1) and (2) directly affect whether personalization data (this round's feedback memory, saved answer audio) actually reaches Firestore/Supabase reliably, so they're recorded here for completeness.
**Files**: `backend/app/services/analytics/analytics_service.py`, `backend/app/services/chat/tts_service.py`, `backend/app/main.py`.
