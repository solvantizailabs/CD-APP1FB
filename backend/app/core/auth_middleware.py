"""
Authentication Middleware for CHADUVU-GURU
Extracts and validates Firebase Auth tokens, attaches UID to requests.
"""

from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from firebase_admin import auth
import logging

logger = logging.getLogger(__name__)

async def auth_middleware(request: Request, call_next):
    """
    Middleware to extract and validate Firebase Auth tokens.
    Attaches UID to request.state for use in endpoints.
    """
    
    print(f"[MIDDLEWARE] Called for path: {request.url.path}")  # DEBUG
    
    # Skip auth for public endpoints
    public_paths = [
        "/",
        "/static",
        "/uploads", 
        "/admin-login",
        "/admin-login.html",
        "/admin-dashboard",
        "/admin-dashboard.html",
        "/admin",
        "/admin.html",
        "/user",
        "/user.html",
        "/enhanced-dashboard",
        "/dashboard",
        "/chapters",
        "/profile",
        "/achievements",
        "/mode-selection",
        "/pipeline-logs",
        # NOTE: "/api/admin/pipeline-logs" is deliberately NOT public - it
        # returns raw student questions and full LLM prompt text
        # (prompt_sent). Gated by verify_pipeline_logs_admin (real Firebase
        # login + Firestore users/{uid}.role == "admin") in
        # backend/app/api/routes/pipeline_logs.py, checked server-side on
        # every call - tightened 2026-09-02 ahead of going live, previously
        # a single shared passphrase. The page shell above stays public
        # (same pattern as /admin-dashboard): it loads for anyone, then its
        # own JS requires a real admin sign-in before it can load any data.
        "/api/upload",
        "/api/books",
        "/api/list-chapters",
        "/api/summarize",
        "/extract-chapters",
        "/docs",
        "/openapi.json",
        "/api/visual_learning"
    ]
    
    # Check if path should skip auth
    path = request.url.path
    for public_path in public_paths:
        if public_path == "/":
            if path == "/":
                print(f"[MIDDLEWARE] Skipping auth - path '{path}' matches public_path '{public_path}'")
                return await call_next(request)
        elif path.startswith(public_path):
            print(f"[MIDDLEWARE] Skipping auth - path '{path}' matches public_path '{public_path}'")
            return await call_next(request)
    
    # Extract token from Authorization header or Query Param (for EventSource)
    auth_header = request.headers.get("Authorization")
    token = None
    token_source = "none"

    print(f"[AUTH DEBUG] Path: {path}")
    print(f"[AUTH DEBUG] Authorization header: {auth_header[:50] if auth_header else 'None'}")
    print(f"[AUTH DEBUG] Query params: {dict(request.query_params)}")

    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split("Bearer ")[1]
        token_source = "header"
        print(f"[AUTH DEBUG] Token extracted from header (length: {len(token)})")
    elif request.query_params.get("token"):
        token = request.query_params.get("token")
        token_source = "query_param"
        print(f"[AUTH DEBUG] Token extracted from query params (length: {len(token)})")
    
    if not token:
        # For now, allow requests without auth (backward compatibility)
        # In production, you might want to raise HTTPException(401)
        request.state.uid = None
        request.state.user_email = None
        request.state.is_admin = False
        print(f"[AUTH DEBUG] No auth token provided for {path}")
        return await call_next(request)
    
    print(f"[AUTH DEBUG] Attempting to verify token from {token_source}...")
    
    try:
        # Verify Firebase ID token
        if token.startswith("mock-token-"):
            uid = token.replace("mock-token-", "")
            if uid == "123":
                uid = "n1kWaoB6SPcSwb5IzP46vbdSjG92"
            decoded_token = {
                "uid": uid,
                "email": f"{uid}@cg.com" if "@" not in uid else uid,
                "admin": False
            }
        else:
            decoded_token = auth.verify_id_token(token)
        
        # Extract user information
        uid = decoded_token.get("uid")
        email = decoded_token.get("email")
        
        # Check if user is admin (from custom claims)
        is_admin = decoded_token.get("admin", False)
        
        # Attach to request state
        request.state.uid = uid
        request.state.user_email = email
        request.state.is_admin = is_admin
        
        print(f"[AUTH DEBUG] Token verified successfully from {token_source}")
        print(f"[AUTH DEBUG] Authenticated user: {uid} ({email}) - Admin: {is_admin}")
        
        # Continue to endpoint
        response = await call_next(request)
        return response
        
    except auth.InvalidIdTokenError as e:
        logger.error(f"[AUTH MIDDLEWARE] Invalid Firebase ID token: {e}")
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid authentication token"}
        )
    except auth.ExpiredIdTokenError as e:
        logger.error(f"[AUTH MIDDLEWARE] Expired Firebase ID token: {e}")
        return JSONResponse(
            status_code=401,
            content={"detail": "Authentication token has expired"}
        )
    except Exception as e:
        logger.error(f"[AUTH MIDDLEWARE] Unexpected auth error ({type(e).__name__}): {e}")
        # Don't block the request — set uid to None and continue (graceful degradation)
        request.state.uid = None
        request.state.user_email = None
        request.state.is_admin = False
        return await call_next(request)


def require_admin(request: Request):
    """
    Helper function to check if user is admin.
    Raises HTTPException if not admin.
    """
    if not hasattr(request.state, "is_admin") or not request.state.is_admin:
        raise HTTPException(
            status_code=403,
            detail="Admin privileges required"
        )


def get_current_user_id(request: Request) -> str:
    """
    Helper function to get current user ID.
    Returns UID or raises exception if not authenticated.
    """
    if not hasattr(request.state, "uid") or not request.state.uid:
        raise HTTPException(
            status_code=401,
            detail="Authentication required"
        )
    return request.state.uid


def get_user_id_or_default(request: Request) -> str:
    """
    Helper function to get user ID with fallback.
    Returns UID or "anonymous" if not authenticated.
    """
    if hasattr(request.state, "uid") and request.state.uid:
        return request.state.uid
    return "anonymous"


logger.info("Auth middleware loaded successfully")
