# Class-10 Personalized Learning — Test Guide v2 (Targeted, Post-Fix)

Purpose: re-test after the Round 5/6 fixes (session continuity, style/restyle, escalation gating).
Every question below targets one specific mechanism or edge case — no filler. Each scenario states
the exact expected log values to check. Create ONE fresh test user for this run (do not reuse
`praneeth10@cg.com` — a clean profile makes the quadrant/engagement math unambiguous).

**Setup**: create a new Firebase Auth account, log in, complete the profile modal (Class 10, any avatar).
For **response_style**, start with **`storytelling`** — most of Scenario 1/2 depends on it.
`tough_subjects`/`easy_subjects` (Scenario 8) cannot be set from the UI yet (known gap) — ask whoever
has backend access to run:
```python
from backend.app.services.personalization import profile_service
profile_service.set_preferences(uid, tough_subjects=["science"], easy_subjects=["maths"])
```
before Scenario 8, using the real `uid` from Firebase Auth.

Keep every question in **one continuous chat session (no page refresh)** unless a scenario says otherwise.

---

## Scenario 1 — Storytelling / Detailed style actually applied (not just stored)
| # | Question | Expect |
|---|---|---|
| 1 | `What is Ohm's Law?` | `text_narration` opens with or centers on a concrete analogy (e.g. water-in-a-pipe). Plain bulleted facts with zero analogy = **fail**. |
| 2 | `What are the characteristics of an arithmetic progression?` | Same — must read as a narrative/analogy, not a dry fact list. |

**Log check**: `personalization.preference = "storytelling"` on both. Read the actual response text — this scenario cannot be verified from `personalization` fields alone, the answer text itself is the evidence.

---

## Scenario 2 — Inline override beats stored preference (and a tie-break edge case)
| # | Question | Expect |
|---|---|---|
| 3 | `Explain the difference between metals and non-metals in detailed step-by-step points.` | Literal numbered steps (`1.` `2.` `3.`...), NOT prose, NOT bullets — despite stored preference being storytelling. |
| 4 | `What is a coordinate grid? Give me a quick storytelling analogy.` | A real analogy, consistent with the inline request (this one matches storytelling anyway, so it's a weak test — see Q5 for the real edge case). |
| 5 | **Edge case**: `Explain photosynthesis in detail, like a story.` | Contains BOTH "detail" and "story" keywords — conflicting signals in one query. Current implementation checks for "detailed" markers first, so expect **numbered steps to win**. This is a genuine ambiguity in the system, not a hidden expectation — if you get a story instead, that just tells us the tie-break needs to be revisited, not that something crashed. |

**Log check**: `personalization.preference` will still show the *stored* value ("storytelling") in all three — that's expected, the log field reflects the stored default, not which override fired. Judge Q3/Q4/Q5 purely from response text shape.

---

## Scenario 3 — Skill classification grounded in real curriculum depth (not keywords)
| # | Question | Expect `grade_relative_difficulty` |
|---|---|---|
| 6 | `What is the chemical formula of common salt?` | `below_grade` or `at_grade` |
| 7 | `Find the roots of x^2 - 5x + 6 = 0.` | `at_grade` |
| 8 | `What is the Heisenberg uncertainty principle?` | `above_grade` (confirmed pre-verified — genuinely beyond Class 10 physics) |
| 9 | `What is the rate law and reaction order in chemical kinetics?` | `above_grade` (confirmed pre-verified) |
| 10 | `What are resources?` | `at_grade` |

**Do not use compound "explain X using Y theory and Z theory" phrasing for above-grade questions** — during prep for this guide we found (and have NOT fixed, it's a separate, higher-stakes issue) that the child-safety classifier consistently refuses compound multi-clause advanced questions as `UNAUTHORIZED`, even when completely benign. Simple single-clause phrasing (like Q8/Q9 above) doesn't trigger it. If you hit an unexpected `UNAUTHORIZED` on a benign question, that's this known issue, not a personalization bug — note it separately, don't fold it into these results.

**After Q10 (5 questions asked)**: check `users/{uid}.profile` in Firestore (or the log) — `total_questions = 5`, `advanced_questions = 2` (Q8, Q9), ratio = 40% ≥ 30% → **`skill: "high"`**. `total_questions ≥ 5` → **`engagement: "high"`**. Quadrant should read **`high_skill_high_engagement`**.

---

## Scenario 4 — Feedback overriding engagement (and a negative control)
Continue from Scenario 3's same user (already at `high_skill_high_engagement`).

1. In the UI, click 👎 on **3** of the answers from Scenario 3.
2. **Expected**: `users/{uid}.profile.engagement` flips to `"low"` even though `total_questions` is still ≥5 — quadrant becomes `high_skill_low_engagement`. `signals.negative_feedback_count = 3`.
3. **Negative control (important — don't skip)**: this only proves feedback *can* override engagement, not that the threshold is correctly *gated*. To test the guard itself, you'd need a fresh user, click 👎 on only **2** answers, and confirm engagement does **NOT** flip (the rule requires 3+ feedback events, not just a bad ratio on 1-2). Worth doing as a separate quick run if you have time — this is the kind of boundary case that's easy to get subtly wrong.

**Log check**: no JSONL field currently captures feedback events directly (feedback is a separate endpoint, not a chat turn) — verify via Firestore `users/{uid}.signals.{positive,negative}_feedback_count` and `profile.engagement` before/after.

---

## Scenario 5 — Repeat-question escalation (corrected math — verify these EXACT values)
| # | Question | Expected `escalation_level` | Expected `same_topic` |
|---|---|---|---|
| 11 | `What is electricity?` | `0` | `true` |
| 12 | `Define electric current.` | `1` | `true` |
| 13 | `Explain electricity simply.` | `2` | `true` |
| 14 | `What is rainwater harvesting?` | `0` | `false` |

This is the exact sequence that failed completely in the last real test (all four turns logged `escalation_level = 0` due to the session-continuity bug). It's also the sequence used to catch and fix a second, subtler bug this round (a stale escalation value could leak into an unrelated topic's turn) — **this scenario is the single most important one to re-verify**, since it validates two separate fixes at once.

**Log check**: all 4 turns MUST share the same `session_id` in the JSONL log — if they don't, the session-continuity fix didn't take effect and nothing else in this scenario means anything. Check that first, before looking at `escalation_level`.

---

## Scenario 6 — Per-student memory continuity (and a negative control)
| # | Question | Expect |
|---|---|---|
| 15 | `What is a combination reaction?` | Fresh answer, `per_student_hits = 0`. |
| 16 | `Can you give me another example of that?` | Must reference the combination-reaction context (e.g. "another example of a combination reaction is..."), NOT ask "example of what?". `per_student_hits ≥ 1`. |
| 17 | **Negative control**: `What is the capital of France?` | Must NOT reference combination reactions or any prior chemistry context — `per_student_hits` should be `0` or reflect a genuinely unrelated (low-score) result, not a false match. This confirms retrieval doesn't fire indiscriminately. |

---

## Scenario 7 — Semantic global cache: hit vs. miss (needs 2 different test users)
| # | User | Question | Expect |
|---|---|---|---|
| 18 | User A | `How do organisms reproduce?` | `mode = "smart_query_fresh"`, `global_cache_hit = false`. |
| 19 | User B (different uid, same class) | `Explain the reproduction process in living organisms.` | Should be recognized as the same question worded differently → `mode = "smart_query_cache_hit"`, `global_cache_hit = true`. |
| 20 | User B | `What is respiration?` | Unrelated topic → must NOT hit the cache → `mode = "smart_query_fresh"`, `global_cache_hit = false`. |

---

## Scenario 8 — Tough/easy subjects (needs manual setup — see top of this doc)
With `tough_subjects=["science"]`, `easy_subjects=["maths"]` set on a test user:
| # | Question | Expect |
|---|---|---|
| 21 | `What is a quadratic equation?` (maths, marked easy) | Moves quickly, assumes baseline understanding, minimal scaffolding. |
| 22 | `What is photosynthesis?` (science, marked tough) | Extra scaffolding — simpler vocabulary, more concrete breakdown, before adding nuance. |

---

## How to report back
Same as last time — export the JSONL lines for this test user from `consolidated_deployment_outputs/chat_modes/{date}_user_logs.jsonl`, plus (for Scenarios 3/4/8) a snapshot of `users/{uid}` from Firestore, since feedback counts and the tough/easy note aren't in the JSONL. I'll do the comparison against the expected values above — you don't need to judge pass/fail yourself, just capture the raw data.

---

## How student input actually improves the system (plain explanation, not test-related)

Every question a student asks does three things simultaneously, all from that single turn:
1. **It's stored as memory** (Qdrant `student_history`, keyed to that student) — so their *next* related question gets an answer that builds on it, instead of starting over.
2. **It updates their running profile** (Firestore `users/{uid}.signals` → `.profile`) — whether the question was above/at/below their grade level nudges their `skill` rating; how often they come back and how they react (👍/👎) nudges `engagement`. This recomputes on every turn, not on a delay.
3. **It's compared against their own recent pattern** (session-scoped, resets every 24h) — asking a similar simple question again without a real gap in between triggers a different explanation strategy next time, instead of repeating the same one.

None of this changes the underlying facts the system teaches — it changes *how* an answer is delivered (style, depth, whether it references what they already know) and *when* the system decides to try something different. The model itself isn't retrained on this data; it reads the student's current profile fresh on every turn and adapts its response accordingly.
