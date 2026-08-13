"""
Shared subject-resolution helper.

"subject" on a users/{uid}/queries doc is often just the frontend's raw
chat-mode filter value ("all") rather than a real classification - see
chat.py, where `subject` defaults to that UI value whenever `matched_subject`
retrieval didn't resolve one. This module recovers a real subject in two
steps, used both when SAVING a query (analytics_service.log_query, so future
queries stop being saved as "all") and when READING history (history.py, for
existing queries that were already saved that way):

1. Authoritative: look up chapter_name in Firestore's
   classes/{class}/subjects/{subject}.chapters registry (written at book-
   ingestion time).
2. Last resort: a keyword heuristic over chapter_name + query text, used only
   when the chapter isn't in that registry at all (e.g. chapter_name is
   "Unknown", or was renamed since the query was logged).
"""

from typing import Optional

from backend.app.core.firebase.firebase_init import db

SUBJECT_KEYWORDS = {
    "science": [
        "electricity", "circuit", "ohm", "current", "resistance", "voltage",
        "chemical", "reaction", "acid", "base", "salt", "compound", "element",
        "atom", "molecule", "photosynthesis", "digestive", "respiration",
        "cell", "force", "energy", "light", "sound", "magnet", "gravity",
        "reflection", "refraction", "metal", "carbon", "periodic",
    ],
    "maths": [
        "equation", "progression", "algebra", "geometry", "triangle", "circle",
        "probability", "statistics", "polynomial", "quadratic", "trigonometry",
        "fraction", "hcf", "lcm", "root", "arithmetic", "coordinate", "surface area",
        "volume", "mean", "median", "mode",
    ],
    "social": [
        "constitution", "resources", "development", "map", "government",
        "democracy", "freedom movement", "dynasty", "empire", "agriculture",
        "climate", "population", "election", "parliament", "president",
        "history", "geography", "civics", "rainwater", "harvesting", "kingdom",
        "ruler", "revolution", "colonial", "independence",
    ],
    "english": [
        "grammar", "essay", "poem", "poetry", "tense", "noun", "verb",
        "comprehension", "literature", "author", "narrator", "figure of speech",
    ],
}


def clean_class_id(class_name) -> str:
    clean = "".join(c for c in str(class_name or "") if c.isdigit())
    return clean or "8"


def get_class_subject_docs(class_name) -> list:
    """
    Firestore's classes/{class} parent document has no fields of its own (it
    exists only implicitly as a parent of the subjects subcollection), so
    listing the "classes" collection directly returns nothing for it - the
    subjects subcollection has to be queried by its explicit path instead.
    """
    class_id = clean_class_id(class_name)
    return list(db.collection("classes").document(class_id).collection("subjects").stream())


def build_chapter_subject_map(class_name) -> dict:
    """chapter_name (lowercased) -> subject, from the Firestore chapter registry."""
    chapter_map = {}
    for subject_doc in get_class_subject_docs(class_name):
        subject = subject_doc.id.lower()
        chapters = subject_doc.to_dict().get("chapters") or []
        for chapter in chapters:
            name = chapter.get("chapter_name")
            if name:
                chapter_map[name.strip().lower()] = subject
    return chapter_map


def classify_by_keywords(chapter_name: Optional[str], query: Optional[str]) -> Optional[str]:
    text = f"{chapter_name or ''} {query or ''}".lower()
    for subject, keywords in SUBJECT_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return subject
    return None


def resolve_subject(
    class_name,
    chapter_name: Optional[str],
    query_text: Optional[str],
    raw_subject: Optional[str],
    valid_subjects: Optional[set] = None,
    chapter_subject_map: Optional[dict] = None,
) -> str:
    """
    Best-effort real subject for a query. `valid_subjects`/`chapter_subject_map`
    can be precomputed and passed in when resolving many queries in one
    request (e.g. history.py's list endpoint) to avoid re-querying Firestore
    per item; omit them for a single one-off resolution (e.g. at write time).
    """
    raw = (raw_subject or "").strip().lower()
    chapter_key = (chapter_name or "").strip().lower()

    if chapter_subject_map is None:
        chapter_subject_map = build_chapter_subject_map(class_name)
    if valid_subjects is None:
        valid_subjects = set(chapter_subject_map.values()) or set(SUBJECT_KEYWORDS.keys())

    if raw and raw != "all" and raw in valid_subjects:
        return raw
    if chapter_key in chapter_subject_map and chapter_subject_map[chapter_key] in valid_subjects:
        return chapter_subject_map[chapter_key]

    guessed = classify_by_keywords(chapter_name, query_text)
    if guessed and guessed in valid_subjects:
        return guessed

    return raw or "uncategorized"
