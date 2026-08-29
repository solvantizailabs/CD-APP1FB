import logging
from fastapi import APIRouter, Query, HTTPException

from backend.app.services.analytics import achievements_service
from backend.app.services.analytics import profile_service
from backend.app.core.firebase.firebase_init import db

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/students/lookup", tags=["Profile"])
async def lookup_student_by_email(email: str = Query(...)):
    """
    Looks up a student's profile (users/{uid} in Firestore) by email, not
    uid - every other endpoint in this file is uid-keyed already and expects
    the caller to already know the uid; this is the bridge for a caller
    (e.g. a terminal tool, a login flow) that only has the student's email.
    Firestore doesn't support querying by document ID pattern-matching, so
    this is a real query against the `email` field each user doc already
    stores (confirmed present - see test_personalization_cli.py's seeded
    user shape), not a workaround.

    Returns {"found": False} rather than a 404 when no match exists - "no
    student with this email" is an expected, normal outcome for a caller
    like a login prompt, not an error condition.
    """
    try:
        query = db.collection("users").where("email", "==", email).limit(1).get()
        docs = list(query)
        if not docs:
            return {"found": False}
        doc = docs[0]
        data = doc.to_dict() or {}
        return {
            "found": True,
            "uid": doc.id,
            "email": data.get("email"),
            "name": data.get("name"),
            "class": data.get("class"),
            "board": data.get("board"),
        }
    except Exception as e:
        logger.error(f"Failed to look up student by email {email!r}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/achievements/summary", tags=["Achievements"])
async def get_achievements_summary(uid: str = Query(...)):
    """
    Get comprehensive achievements data for a user.
    """
    try:
        logger.info(f"[ACHIEVEMENTS] Fetching achievements for uid: {uid}")
        achievements_data = achievements_service.get_user_achievements(uid)
        return achievements_data
    except Exception as e:
        logger.error(f"Failed to get achievements for {uid}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/achievements/tiers", tags=["Achievements"])
async def get_achievement_tiers():
    """Get information about all achievement tiers"""
    try:
        tiers = ["newcomer", "rising_star", "scholar", "master", "legend"]
        tier_info = [achievements_service.get_tier_info(tier) for tier in tiers]
        return {"tiers": tier_info}
    except Exception as e:
        logger.error(f"Failed to get tier info: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/profile/stats", tags=["Profile"])
async def get_profile_stats(uid: str = Query(...)):
    """
    Get comprehensive profile statistics for the enhanced profile page.
    """
    try:
        logger.info(f"[PROFILE] Fetching profile stats for uid: {uid}")
        profile_data = profile_service.get_profile_stats(uid)
        return profile_data
    except Exception as e:
        logger.error(f"Failed to get profile stats for {uid}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
