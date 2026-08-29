"""
Standalone runner for the section 17 test cases, mirroring how
backend/app/orchestrator_test was used to validate new_rag before wiring it
into chat.py. Run directly:

    python -m backend.app.services.question_pipeline.test_harness

For each case, prints every stage's output (Question Understanding | Intent |
Curriculum Mapping | Query Reformulation | RAG Retrieval | RAG Context |
LLM Answer | Grounding) per the doc's per-test-case checklist, so a wrong
final answer can be traced back to the exact stage that caused it
(section 20).
"""
import json
from dataclasses import asdict

from backend.app.services.llm.openai_client import OPENAI_MODEL, create_client
from backend.app.services.question_pipeline.pipeline import run_pipeline
from backend.app.services.question_pipeline.schemas import PipelineInput

# Section 17 test cases. session_context carries what a real request would
# already know about the student (class/subject/book), same as
# learner_context/session_context in the section 3 input contract.
TEST_CASES = [
    {
        "name": "Direct question",
        "student_question": "What is photosynthesis?",
        "conversation_context": [],
        "session_context": {"class_name": "7", "subject": "Science"},
    },
    {
        "name": "Why question",
        "student_question": "Why does the Moon not have its own light?",
        "conversation_context": [],
        "session_context": {"class_name": "6", "subject": "Science"},
    },
    {
        "name": "Calculation",
        "student_question": "Find the area of a circle with radius 7 cm.",
        "conversation_context": [],
        "session_context": {"class_name": "8", "subject": "Maths"},
    },
    {
        "name": "Follow-up",
        "student_question": "Why does this happen?",
        "conversation_context": [
            {"query": "Why does pressure increase with depth?", "answer": "Because the weight of water above increases with depth, increasing pressure."}
        ],
        "session_context": {"class_name": "8", "subject": "Science"},
    },
    {
        "name": "Contextual",
        "student_question": "Explain the second point again.",
        "conversation_context": [
            {"query": "What are the properties of metals?", "answer": "1. Malleable 2. Good conductors of electricity 3. Sonorous"}
        ],
        "session_context": {"class_name": "8", "subject": "Science"},
    },
    {
        "name": "Ambiguous",
        "student_question": "Explain it.",
        "conversation_context": [],
        "session_context": {"class_name": "7", "subject": "Science"},
    },
    {
        "name": "Wrong terminology",
        "student_question": "Why do plants respire food?",
        "conversation_context": [],
        "session_context": {"class_name": "7", "subject": "Science"},
    },
    {
        "name": "Cross-topic",
        "student_question": "How is pressure related to force?",
        "conversation_context": [],
        "session_context": {"class_name": "8", "subject": "Science"},
    },
    {
        "name": "Out-of-syllabus",
        "student_question": "Explain quantum computing.",
        "conversation_context": [],
        "session_context": {"class_name": "6", "subject": "Science"},
    },
]


def _print_stage_report(name: str, result) -> None:
    print("=" * 100)
    print(f"CASE: {name}")
    print("-" * 100)
    print("Question Understanding:", result.validation.classification if result.validation else None,
          "| reason:", result.validation.reason if result.validation else None)
    print("Resolved Question:", result.resolved.resolved_question if result.resolved else None)
    print("Intent:", result.intent.primary_intent if result.intent else None,
          "(confidence:", result.intent.confidence if result.intent else None, ")")
    print("Curriculum Mapping:", asdict(result.curriculum) if result.curriculum else None)
    print("Query Reformulation:", asdict(result.query) if result.query else None)
    print("Routing:", result.routing.route if result.routing else None, "-", result.routing.reason if result.routing else None)
    print("RAG Retrieval status:", result.rag.retrieval_status if result.rag else None,
          "| confidence_tier:", result.rag.confidence_tier if result.rag else None)
    print("RAG Context (first 200 chars):", (result.rag.context[:200] if result.rag and result.rag.context else None))
    print("LLM Answer:", result.final_answer)
    print("Grounding:", asdict(result.grounding) if result.grounding else None)
    print("Trace:", " -> ".join(result.trace))
    print("Final status:", result.status)


def main():
    client = create_client()
    for case in TEST_CASES:
        pipeline_input = PipelineInput(
            student_question=case["student_question"],
            conversation_context=case["conversation_context"],
            learner_context={},
            session_context=case["session_context"],
        )
        try:
            result = run_pipeline(pipeline_input, client, OPENAI_MODEL)
            _print_stage_report(case["name"], result)
        except Exception as e:
            print("=" * 100)
            print(f"CASE: {case['name']} -> CRASHED: {e}")


if __name__ == "__main__":
    main()
