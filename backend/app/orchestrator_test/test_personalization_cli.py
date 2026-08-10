"""
End-to-end test harness for the personalized-learning changes
(personalized_learning.md SS6.1-SS6.5, SS6.7).

Seeds one manually-created test user (SS2.6 - no real registration UI yet),
then fires a scripted sequence of questions through the SAME personalization
wiring chat.py uses (profile context, escalation, per-student memory,
semantic cache), printing the same decision-trace line chat.py logs per turn
so you can verify preference/quadrant/escalation/retrieval behavior without
needing the frontend or a live server.

Usage:
    python -m backend.app.orchestrator_test.test_personalization_cli
    python -m backend.app.orchestrator_test.test_personalization_cli --uid test_student_class6 --class 6

No Sarvam TTS, no video rendering - text-only orchestrator calls, same as
test_orchestrator_cli.py.
"""
import argparse
import os
import sys
import time

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.app.orchestrator_test.test_runner import run_orchestrator_pipeline
from backend.app.services.personalization import profile_service
from backend.app.services.chat.session_service import session_manager
from backend.app.services.retrieval import qdrant_service as qdrant
from backend.app.core.firebase.firebase_init import db


DEFAULT_QUESTIONS = [
    # 1: a plain, basic question - establishes the topic, nothing to retrieve yet.
    "What is the digestive system?",
    # 2: a repeat of a similarly basic question on the SAME topic - should
    #    trigger escalation_level >= 1 on turn 3, and per-student memory
    #    should already have turn 1 to build on.
    "What is digestion?",
    # 3: the actual follow-up that motivated this whole project (SS2.4) -
    #    should retrieve turn 1/2 from per-student memory and build on it
    #    instead of re-explaining from scratch.
    "What is the difference between the digestive and circulatory systems?",
]


def seed_test_user(uid: str, class_name: int, response_style: str):
    """Manually-seeded test profile (SS2.6 - stands in for real registration)."""
    db.collection("users").document(uid).set({
        "email": f"{uid}@test.local",
        "name": "Test Student",
        "class": class_name,
        "board": "CBSE",
        "role": "student",
    }, merge=True)
    profile_service.set_preferences(
        uid,
        response_style=response_style,
        tough_subjects=["science"],
        easy_subjects=["maths"],
    )
    print(f"[SEED] Test user '{uid}' created/updated: class={class_name}, response_style={response_style}")


def run_turn(uid: str, class_name: int, session_id: str, question: str, turn_no: int):
    print(f"\n{'=' * 65}\nTURN {turn_no}: \"{question}\"\n{'=' * 65}")

    profile_ctx = profile_service.get_profile_context(uid)
    escalation_level = session_manager.get_escalation_level(session_id)
    student_history_hits = qdrant.retrieve_student_history(uid, question)

    streak_anchor = session_manager.get_streak_anchor(session_id)
    is_same_topic_as_streak = True
    if streak_anchor:
        is_same_topic_as_streak = qdrant.text_similarity(question, streak_anchor) >= qdrant.STUDENT_HISTORY_MIN_SCORE

    print(
        f"[PERSONALIZATION TRACE] uid={uid} preference={profile_ctx.get('response_style')} "
        f"quadrant={profile_ctx.get('quadrant')} escalation_level={escalation_level} "
        f"per_student_hits={len(student_history_hits)} streak_anchor={streak_anchor!r} "
        f"same_topic={is_same_topic_as_streak}"
    )
    if student_history_hits:
        for h in student_history_hits:
            print(f"    - retrieved (score={h['score']:.3f}): \"{h.get('reformulated_question') or h.get('question')}\"")

    student_profile = {
        "uid": uid,
        "name": "Test Student",
        "class": class_name,
        "board": "CBSE",
        "role": "student",
        "response_style": profile_ctx.get("response_style"),
        "quadrant": profile_ctx.get("quadrant"),
        "escalation_level": escalation_level,
        "tough_subjects": profile_ctx.get("tough_subjects", []),
        "easy_subjects": profile_ctx.get("easy_subjects", []),
        "per_student_history": student_history_hits,
    }

    report = run_orchestrator_pipeline(question, student_profile)
    out = report.get("orchestrator_output", {})
    answer = out.get("text_narration") or "(no text_narration - check format_decision)"

    print(f"\n[ANSWER] format={out.get('format_decision')} classification={out.get('classification')}")
    print(f"{answer[:600]}{'...' if len(answer) > 600 else ''}")

    is_basic = profile_service.is_basic_question(question)
    session_manager.add_turn(session_id, {
        "query": question,
        "reformulated": out.get("reformulated_query", question),
        "answer": answer,
        "intent_type": out.get("classification", "CURRICULUM"),
        "is_basic_question": is_basic,
        "is_same_topic_as_streak": is_same_topic_as_streak,
    })
    profile_service.record_turn_signals(
        uid, class_name, question,
        grade_relative_difficulty=out.get("grade_relative_difficulty")
    )
    quadrant_result = profile_service.compute_quadrant(uid)
    qdrant.store_student_turn(
        uid, question, out.get("reformulated_query", question), answer,
        class_name, out.get("matched_subject") or "science", topic=out.get("matched_chapter"),
    )
    print(f"[POST-TURN] is_basic_question={is_basic} quadrant_now={quadrant_result}")


def main():
    parser = argparse.ArgumentParser(description="Personalization end-to-end test harness")
    parser.add_argument("--uid", default="test_student_personalization_demo")
    parser.add_argument("--class", dest="class_name", type=int, default=6)
    parser.add_argument("--response-style", default="storytelling", choices=list(profile_service.RESPONSE_STYLES))
    parser.add_argument("--questions", nargs="*", default=None, help="Override the default question sequence")
    args = parser.parse_args()

    from backend.app.services.retrieval import qdrant_service
    qdrant_service.initialize()

    seed_test_user(args.uid, args.class_name, args.response_style)

    book_uuid = f"personalization_test_{args.uid}"
    session = session_manager.get_or_create_session(book_uuid)
    session_id = session["session_id"]
    print(f"[SEED] New 24h session started: {session_id}")

    questions = args.questions or DEFAULT_QUESTIONS
    for i, q in enumerate(questions, start=1):
        run_turn(args.uid, args.class_name, session_id, q, i)
        time.sleep(1)

    print(f"\n{'=' * 65}\nDONE. Inspect users/{args.uid} in Firestore for profile/signals/quadrant,")
    print(f"and the 'student_history' Qdrant collection filtered by uid='{args.uid}' for stored turns.")
    print("=" * 65)


if __name__ == "__main__":
    main()
