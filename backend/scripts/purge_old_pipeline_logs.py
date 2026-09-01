"""
Retention policy for pipeline_logs (see
backend/app/services/question_pipeline/observability/log_store.py):
deletes FRESH records older than the retention window, from both Firestore
and their local JSON mirror under terminal_test/outputs/pipeline_logs/.

Scope, deliberately: only records WITHOUT `legacy: true` are eligible.
Legacy records (backfilled by backend/scripts/migrate_pipeline_logs.py from
old users/*/queries history) never have a `prompt_sent`/`stages`/`llm_calls`
payload in the first place - the privacy concern this policy exists for
(raw student text + full LLM prompts accumulating indefinitely) does not
apply to them, and purging them would undo the deliberate backfill. Only
the ongoing stream of new, fully-detailed records is subject to this.

Access to the data this purges is separately restricted to admin accounts
only (require_admin on backend/app/api/routes/pipeline_logs.py) - retention
and access control are two different controls, both needed.

Usage:
    python -m backend.scripts.purge_old_pipeline_logs [--days 30] [--dry-run]

Not scheduled automatically by anything in this repo - run manually, or
wire it into whatever job scheduler / Windows Task Scheduler entry the team
sets up for recurring maintenance.
"""
import argparse
import datetime
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from backend.app.core.firebase.firebase_init import db
from backend.app.services.question_pipeline.observability.log_store import FIRESTORE_COLLECTION, _LOCAL_LOG_DIR


def _as_datetime(ts) -> datetime.datetime:
    if hasattr(ts, "timestamp"):
        return datetime.datetime.fromtimestamp(ts.timestamp(), tz=datetime.timezone.utc)
    if isinstance(ts, datetime.datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=datetime.timezone.utc)
    return datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)


def purge(days: int, dry_run: bool) -> None:
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    docs = list(db.collection(FIRESTORE_COLLECTION).stream())

    deleted = 0
    kept_legacy = 0
    kept_recent = 0

    for doc in docs:
        data = doc.to_dict() or {}
        if data.get("legacy"):
            kept_legacy += 1
            continue

        ts = data.get("timestamp")
        if ts is None or _as_datetime(ts) >= cutoff:
            kept_recent += 1
            continue

        request_id = data.get("request_id", doc.id)
        if dry_run:
            print(f"[DRY RUN] would delete {request_id} (timestamp={ts})")
        else:
            doc.reference.delete()
            local_path = os.path.join(_LOCAL_LOG_DIR, f"{request_id}.json")
            if os.path.exists(local_path):
                os.remove(local_path)
        deleted += 1

    print(f"\nDone. {'Would delete' if dry_run else 'Deleted'} {deleted} record(s) older than {days} days.")
    print(f"Kept {kept_legacy} legacy record(s) (never subject to this policy) "
          f"and {kept_recent} record(s) within the retention window.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30, help="Retention window in days (default: 30).")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be deleted without deleting anything.")
    args = parser.parse_args()
    purge(days=args.days, dry_run=args.dry_run)
