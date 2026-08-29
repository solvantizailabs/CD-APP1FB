"""
Internal consistency check on a retrieve() result itself - distinct from
grounding.py, which checks a generated ANSWER against context. This checks
the RETRIEVAL RESULT's own shape/values are sane before anything downstream
trusts it (e.g. catching a status/confidence_tier combination that
shouldn't be possible, or a "confident" result with no chunks attached).
"""
from typing import Dict, List, Tuple

VALID_STATUSES = ("confident", "insufficient_context")
VALID_CONFIDENCE_TIERS = ("HIGH", "MEDIUM", "LOW", "INSUFFICIENT")


def validate_retrieval_result(result: Dict) -> Tuple[bool, List[str]]:
    issues: List[str] = []

    status = result.get("status")
    if status not in VALID_STATUSES:
        issues.append(f"unexpected status: {status!r}")

    tier = result.get("confidence_tier")
    if tier not in VALID_CONFIDENCE_TIERS:
        issues.append(f"unexpected confidence_tier: {tier!r}")

    if status == "confident" and tier == "INSUFFICIENT":
        issues.append("status is 'confident' but confidence_tier is 'INSUFFICIENT' - contradictory")

    if status == "confident" and not result.get("chunks"):
        issues.append("status is 'confident' but no chunks were returned")

    top_k = result.get("top_k")
    if top_k is not None and (not isinstance(top_k, int) or top_k < 1):
        issues.append(f"top_k should be a positive integer, got {top_k!r}")

    return (len(issues) == 0), issues
