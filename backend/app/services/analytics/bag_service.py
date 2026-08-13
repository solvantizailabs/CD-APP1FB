"""
My Bag Service for CHADUVU-GURU
Manages student notebooks and saved content.
"""

from google.cloud import firestore
from backend.app.core.firebase.firebase_init import db
import logging
from datetime import datetime, timezone
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# ============================================
# NOTEBOOK MANAGEMENT
# ============================================

def create_notebook(uid: str, notebook_name: str, subject: str = "General", color: str = "#4F46E5") -> str:
    """
    Create a new notebook for the student.
    
    Args:
        uid: User ID
        notebook_name: Name of the notebook
        subject: Subject category
        color: Hex color code for the notebook
    
    Returns:
        Notebook ID
    """
    try:
        doc_ref = db.collection("notebooks").document()
        notebook_id = doc_ref.id
        
        notebook_data = {
            "notebook_id": notebook_id,
            "uid": uid,
            "name": notebook_name,
            "subject": subject,
            "color": color,
            "created_at": firestore.SERVER_TIMESTAMP,
            "updated_at": firestore.SERVER_TIMESTAMP,
            "item_count": 0
        }
        
        doc_ref.set(notebook_data)
        logger.info(f"✅ Created notebook '{notebook_name}' for user {uid}")
        return notebook_id
        
    except Exception as e:
        logger.error(f"❌ Failed to create notebook: {e}")
        raise


def get_notebooks(uid: str) -> List[Dict]:
    """
    Get all notebooks for a user.
    
    Args:
        uid: User ID
    
    Returns:
        List of notebook dictionaries
    """
    try:
        notebooks_ref = db.collection("notebooks")\
            .where("uid", "==", uid)\
            .order_by("updated_at", direction=firestore.Query.DESCENDING)
        
        notebooks = []
        for doc in notebooks_ref.stream():
            notebook_data = doc.to_dict()
            # Format timestamps
            if notebook_data.get("created_at"):
                notebook_data["created_at"] = notebook_data["created_at"].isoformat()
            if notebook_data.get("updated_at"):
                notebook_data["updated_at"] = notebook_data["updated_at"].isoformat()
            notebooks.append(notebook_data)
        
        logger.info(f"✅ Retrieved {len(notebooks)} notebooks for user {uid}")
        return notebooks
        
    except Exception as e:
        logger.error(f"❌ Failed to get notebooks: {e}")
        raise


def delete_notebook(uid: str, notebook_id: str) -> None:
    """
    Delete a notebook and all its contents.
    
    Args:
        uid: User ID (for security check)
        notebook_id: Notebook ID to delete
    """
    try:
        # Verify ownership
        notebook_ref = db.collection("notebooks").document(notebook_id)
        notebook = notebook_ref.get()
        
        if not notebook.exists:
            raise ValueError("Notebook not found")
        
        if notebook.to_dict().get("uid") != uid:
            raise ValueError("Unauthorized: You don't own this notebook")
        
        # Delete all items in the notebook
        items_ref = db.collection("bag_items")\
            .where("notebook_id", "==", notebook_id)
        
        for item_doc in items_ref.stream():
            item_doc.reference.delete()
        
        # Delete the notebook
        notebook_ref.delete()
        logger.info(f"✅ Deleted notebook {notebook_id} for user {uid}")
        
    except Exception as e:
        logger.error(f"❌ Failed to delete notebook: {e}")
        raise


# ============================================
# CONTENT MANAGEMENT
# ============================================

def save_to_bag(
    uid: str,
    notebook_id: str,
    content: str,
    title: str = None,
    source_query: str = None,
    chapter_name: str = None,
    subject: str = None
) -> str:
    """
    Save content to a notebook.
    
    Args:
        uid: User ID
        notebook_id: Target notebook ID
        content: The content to save
        title: Optional title (auto-generated if not provided)
        source_query: Original query that generated this content
        chapter_name: Chapter the content is from
        subject: Subject category
    
    Returns:
        Item ID
    """
    try:
        # Verify notebook ownership
        notebook_ref = db.collection("notebooks").document(notebook_id)
        notebook = notebook_ref.get()
        
        if not notebook.exists:
            raise ValueError("Notebook not found")
        
        if notebook.to_dict().get("uid") != uid:
            raise ValueError("Unauthorized: You don't own this notebook")
        
        # Create the item
        item_ref = db.collection("bag_items").document()
        item_id = item_ref.id
        
        # Auto-generate title if not provided
        if not title:
            title = content[:50] + "..." if len(content) > 50 else content
        
        item_data = {
            "item_id": item_id,
            "notebook_id": notebook_id,
            "uid": uid,
            "title": title,
            "content": content,
            "source_query": source_query,
            "chapter_name": chapter_name,
            "subject": subject,
            "created_at": firestore.SERVER_TIMESTAMP,
            "is_favorite": False
        }
        
        item_ref.set(item_data)
        
        # Update notebook item count and timestamp
        notebook_ref.update({
            "item_count": firestore.Increment(1),
            "updated_at": firestore.SERVER_TIMESTAMP
        })
        
        logger.info(f"✅ Saved content to notebook {notebook_id} for user {uid}")
        return item_id
        
    except Exception as e:
        logger.error(f"❌ Failed to save to bag: {e}")
        raise


def get_bag_items(uid: str, notebook_id: str = None) -> List[Dict]:
    """
    Get items from bag, optionally filtered by notebook.
    
    Args:
        uid: User ID
        notebook_id: Optional notebook filter
    
    Returns:
        List of bag item dictionaries
    """
    try:
        items_ref = db.collection("bag_items").where("uid", "==", uid)
        
        if notebook_id:
            items_ref = items_ref.where("notebook_id", "==", notebook_id)
        
        items_ref = items_ref.order_by("created_at", direction=firestore.Query.DESCENDING)
        
        items = []
        for doc in items_ref.stream():
            item_data = doc.to_dict()
            # Format timestamp
            if item_data.get("created_at"):
                item_data["created_at"] = item_data["created_at"].isoformat()
            items.append(item_data)
        
        logger.info(f"✅ Retrieved {len(items)} items for user {uid}")
        return items
        
    except Exception as e:
        logger.error(f"❌ Failed to get bag items: {e}")
        raise


def delete_bag_item(uid: str, item_id: str) -> None:
    """
    Delete an item from bag.
    
    Args:
        uid: User ID (for security check)
        item_id: Item ID to delete
    """
    try:
        item_ref = db.collection("bag_items").document(item_id)
        item = item_ref.get()
        
        if not item.exists:
            raise ValueError("Item not found")
        
        item_data = item.to_dict()
        if item_data.get("uid") != uid:
            raise ValueError("Unauthorized: You don't own this item")
        
        notebook_id = item_data.get("notebook_id")
        
        # Delete the item
        item_ref.delete()
        
        # Update notebook item count
        if notebook_id:
            notebook_ref = db.collection("notebooks").document(notebook_id)
            notebook_ref.update({
                "item_count": firestore.Increment(-1),
                "updated_at": firestore.SERVER_TIMESTAMP
            })
        
        logger.info(f"✅ Deleted item {item_id} for user {uid}")
        
    except Exception as e:
        logger.error(f"❌ Failed to delete bag item: {e}")
        raise


def toggle_favorite(uid: str, item_id: str) -> bool:
    """
    Toggle favorite status of an item.
    
    Args:
        uid: User ID
        item_id: Item ID
    
    Returns:
        New favorite status
    """
    try:
        item_ref = db.collection("bag_items").document(item_id)
        item = item_ref.get()
        
        if not item.exists:
            raise ValueError("Item not found")
        
        if item.to_dict().get("uid") != uid:
            raise ValueError("Unauthorized")
        
        current_status = item.to_dict().get("is_favorite", False)
        new_status = not current_status
        
        item_ref.update({"is_favorite": new_status})
        logger.info(f"✅ Toggled favorite for item {item_id}: {new_status}")
        
        return new_status
        
    except Exception as e:
        logger.error(f"❌ Failed to toggle favorite: {e}")
        raise


# ============================================
# VISUAL LIBRARY (saved video-lesson references)
# ============================================
# Stores a reference to an existing users/{uid}/queries/{doc_id} doc, not a
# copy of its content - History's "My videos" tab already owns that data
# (video_url, subject, chapter_name, query), so every read here resolves
# live against it instead of duplicating it.

def add_to_visual_library(uid: str, doc_id: str) -> str:
    """
    Save a reference to a video lesson (a users/{uid}/queries/{doc_id} doc)
    into the student's Visual Library. Idempotent - re-saving the same
    doc_id returns the existing item_id instead of creating a duplicate.

    Args:
        uid: User ID
        doc_id: The query doc's ID (from users/{uid}/queries/{doc_id})

    Returns:
        Item ID
    """
    try:
        existing_ref = db.collection("visual_library_items")\
            .where("uid", "==", uid)\
            .where("doc_id", "==", doc_id)\
            .limit(1)
        for doc in existing_ref.stream():
            logger.info(f"ℹ️ doc_id {doc_id} already in visual library for user {uid}")
            return doc.id

        item_ref = db.collection("visual_library_items").document()
        item_id = item_ref.id

        item_ref.set({
            "item_id": item_id,
            "uid": uid,
            "doc_id": doc_id,
            "created_at": firestore.SERVER_TIMESTAMP,
        })

        logger.info(f"✅ Saved doc_id {doc_id} to visual library for user {uid}")
        return item_id

    except Exception as e:
        logger.error(f"❌ Failed to add to visual library: {e}")
        raise


def remove_from_visual_library(uid: str, item_id: str) -> None:
    """
    Remove a saved video reference from the student's Visual Library.
    This only deletes the reference - the underlying
    users/{uid}/queries/{doc_id} doc (and its History entry) is untouched.

    Args:
        uid: User ID (for security check)
        item_id: Visual library item ID to delete
    """
    try:
        item_ref = db.collection("visual_library_items").document(item_id)
        item = item_ref.get()

        if not item.exists:
            raise ValueError("Item not found")

        if item.to_dict().get("uid") != uid:
            raise ValueError("Unauthorized: You don't own this item")

        item_ref.delete()
        logger.info(f"✅ Removed visual library item {item_id} for user {uid}")

    except Exception as e:
        logger.error(f"❌ Failed to remove visual library item: {e}")
        raise


def get_visual_library(uid: str) -> List[Dict]:
    """
    Get all saved video-lesson references for a user, resolved against
    their source users/{uid}/queries/{doc_id} docs.

    Args:
        uid: User ID

    Returns:
        List of dicts: {item_id, doc_id, query, subject, chapter_name,
        video_url, created_at}. A reference whose source query doc has
        since been deleted is silently skipped rather than erroring the
        whole list.
    """
    try:
        # Sorted in Python rather than via Firestore order_by: an equality
        # filter (uid) combined with an order_by on a different field
        # (created_at) requires a Firestore composite index, which doesn't
        # exist for this brand-new collection (unlike "notebooks", which
        # already has one from an earlier feature). A student's library is
        # small, so sorting the already-fetched docs here avoids depending
        # on a manual Firebase console step for this feature to work.
        items_ref = db.collection("visual_library_items").where("uid", "==", uid)

        raw_items = [doc.to_dict() for doc in items_ref.stream()]
        raw_items.sort(key=lambda d: d.get("created_at") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

        resolved = []
        for item_data in raw_items:
            doc_id = item_data.get("doc_id")
            if not doc_id:
                continue

            query_doc = db.collection("users").document(uid)\
                .collection("queries").document(doc_id).get()
            if not query_doc.exists:
                logger.info(f"ℹ️ Skipping visual library item {item_data.get('item_id')} - source doc {doc_id} no longer exists")
                continue

            query_data = query_doc.to_dict() or {}
            created_at = item_data.get("created_at")

            resolved.append({
                "item_id": item_data.get("item_id"),
                "doc_id": doc_id,
                "query": query_data.get("query"),
                "subject": query_data.get("subject"),
                "chapter_name": query_data.get("chapter_name"),
                "video_url": query_data.get("video_url"),
                "created_at": created_at.isoformat() if created_at else None,
            })

        logger.info(f"✅ Retrieved {len(resolved)} visual library items for user {uid}")
        return resolved

    except Exception as e:
        logger.error(f"❌ Failed to get visual library: {e}")
        raise


logger.info("✅ Bag service loaded successfully")
