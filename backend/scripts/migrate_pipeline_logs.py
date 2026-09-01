"""
Backfills the new `pipeline_logs` collection (see
backend/app/services/question_pipeline/observability/log_store.py) from the
existing users/{uid}/queries history, so the Pipeline Trace Inspector
dashboard shows something for every question ever asked, not just questions
asked after this logging feature shipped.

IMPORTANT - what this can and cannot recover, honestly:
Old queries were logged by analytics_service.log_query() before any of this
stage-timing/token-tracking instrumentation existed. There is no way to
retroactively know how long Stage 4's RAG call took, or how many tokens a
2026-07 LLM call used - that data was never captured. This script does NOT
invent numbers for those fields. Every migrated record is stamped
`"legacy": true, "pre_instrumentation": true`, `stages: []`, `llm_calls: []`,
`total_duration_ms: None`, `total_tokens: None`, `total_cost: None` - the
dashboard is expected to render these as "pre-instrumentation - detail not
available" rather than a broken/zero timeline. Only the fields that were
genuinely already stored (question, reformulated question, subject,
chapter, class, format_decision, llm_action, timestamp) are backfilled.

Idempotent: request_id is deterministically derived from the source doc
path (`legacy_{uid}_{doc_id}`), so re-running this is always safe - it will
overwrite the same legacy records with the same data, never duplicate them.

Usage:
    python -m backend.scripts.migrate_pipeline_logs [--dry-run] [--limit N]
"""
import argparse
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from backend.app.core.firebase.firebase_init import db

PIPELINE_LOGS_COLLECTION = "pipeline_logs"
_BATCH_SIZE = 400


def _status_from_llm_action(llm_action: str) -> str:
    if llm_action == "UNAUTHORIZED":
        return "REFUSED"
    if llm_action in ("INVALID", "AMBIGUOUS"):
        return "CLARIFICATION_NEEDED"
    if llm_action == "UNSUPPORTED":
        return "REFUSED"
    return "ANSWERED"


def _build_legacy_record(uid: str, doc_id: str, data: dict) -> dict:
    llm_action = data.get("llm_action") or "UNKNOWN"
    return {
        "request_id": f"legacy_{uid}_{doc_id}",
        "legacy": True,
        "pre_instrumentation": True,
        "legacy_source": f"users/{uid}/queries/{doc_id}",
        "tracker_id": None,
        "tracker_hours_left": None,
        "book_session_id": None,
        "uid": uid,
        "raw_question": data.get("query") or "",
        "resolved_question": data.get("reformulated_query") or data.get("query") or "",
        "grade": data.get("class"),
        "board": None,
        "language": None,
        "status": _status_from_llm_action(llm_action),
        "format_decision": data.get("format_decision"),
        "route": None,
        "is_follow_up": None,
        "stages": [],
        "total_duration_ms": None,
        "llm_calls": [],
        "total_tokens": None,
        "total_cost": None,
        "trace": [],
        "subject": data.get("subject"),
        "chapter_name": data.get("chapter_name"),
        "llm_action": llm_action,
        "timestamp": data.get("timestamp"),
    }


def migrate(dry_run: bool = False, limit: int = None) -> None:
    users = list(db.collection("users").stream())
    total_migrated = 0
    total_skipped = 0
    batch = db.batch()
    pending_in_batch = 0

    for user_doc in users:
        uid = user_doc.id
        queries = db.collection("users").document(uid).collection("queries").stream()
        for q_doc in queries:
            if limit is not None and total_migrated >= limit:
                break
            data = q_doc.to_dict() or {}
            if not data.get("query"):
                total_skipped += 1
                continue

            record = _build_legacy_record(uid, q_doc.id, data)

            if dry_run:
                print(f"[DRY RUN] would write {record['request_id']}: \"{record['raw_question'][:60]}\"")
                total_migrated += 1
                continue

            doc_ref = db.collection(PIPELINE_LOGS_COLLECTION).document(record["request_id"])
            batch.set(doc_ref, record)
            pending_in_batch += 1
            total_migrated += 1

            if pending_in_batch >= _BATCH_SIZE:
                batch.commit()
                print(f"  committed batch, {total_migrated} migrated so far...")
                batch = db.batch()
                pending_in_batch = 0

        if limit is not None and total_migrated >= limit:
            break

    if not dry_run and pending_in_batch > 0:
        batch.commit()

    print(f"\nDone. Migrated {total_migrated} legacy queries into '{PIPELINE_LOGS_COLLECTION}' "
          f"({total_skipped} skipped - no query text). Dry run: {dry_run}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print what would be migrated without writing anything.")
    parser.add_argument("--limit", type=int, default=None, help="Stop after migrating this many records (for a quick test run).")
    args = parser.parse_args()
    migrate(dry_run=args.dry_run, limit=args.limit)
