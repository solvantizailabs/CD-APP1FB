import logging
from typing import List, Dict, Optional
from pydantic import BaseModel
from fastapi import APIRouter, Query, HTTPException

from backend.app.services.analytics import bag_service
from backend.app.services.analytics import analytics_service

logger = logging.getLogger(__name__)

router = APIRouter()

class NoteAddRequest(BaseModel):
    uid: str
    title: str
    content: str

class NoteDeleteRequest(BaseModel):
    uid: str
    note_index: int

class NotebookCreateRequest(BaseModel):
    uid: str
    name: str
    subject: str = "General"
    color: str = "#4F46E5"

class NotebookDeleteRequest(BaseModel):
    uid: str
    notebook_id: str

class SaveToBagRequest(BaseModel):
    uid: str
    notebook_id: str
    content: str
    title: Optional[str] = None
    source_query: Optional[str] = None
    chapter_name: Optional[str] = None
    subject: Optional[str] = None

class DeleteBagItemRequest(BaseModel):
    uid: str
    item_id: str

class ToggleFavoriteRequest(BaseModel):
    uid: str
    item_id: str

class VisualLibraryAddRequest(BaseModel):
    uid: str
    doc_id: str

class VisualLibraryRemoveRequest(BaseModel):
    uid: str
    item_id: str


@router.get("/api/notes/list", tags=["Notes"])
async def list_notes_endpoint(uid: str = Query(...)):
    """
    Get all saved notes for a user.
    """
    try:
        notes = analytics_service.get_notes(uid)
        return {"notes": notes, "total": len(notes)}
    except Exception as e:
        logger.error(f"Failed to list notes: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/notes/add", tags=["Notes"])
async def add_note_endpoint(request: NoteAddRequest):
    """
    Add a new note to user's saved notes.
    """
    try:
        analytics_service.add_note(
            uid=request.uid,
            title=request.title,
            content=request.content
        )
        return {"success": True, "message": "Note added successfully"}
    except Exception as e:
        logger.error(f"Failed to add note: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/notes/delete", tags=["Notes"])
async def delete_note_endpoint(request: NoteDeleteRequest):
    """
    Delete a note by index.
    """
    try:
        analytics_service.delete_note(
            uid=request.uid,
            note_index=request.note_index
        )
        return {"success": True, "message": "Note deleted successfully"}
    except Exception as e:
        logger.error(f"Failed to delete note: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/bag/notebook/create", tags=["My Bag"])
async def create_notebook_endpoint(request: NotebookCreateRequest):
    """Create a new notebook."""
    try:
        notebook_id = bag_service.create_notebook(
            uid=request.uid,
            notebook_name=request.name,
            subject=request.subject,
            color=request.color
        )
        return {"success": True, "notebook_id": notebook_id, "message": "Notebook created!"}
    except Exception as e:
        logger.error(f"Failed to create notebook: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/bag/notebooks", tags=["My Bag"])
async def get_notebooks_endpoint(uid: str = Query(...)):
    """Get all notebooks for a user."""
    try:
        notebooks = bag_service.get_notebooks(uid)
        return {"notebooks": notebooks, "total": len(notebooks)}
    except Exception as e:
        logger.error(f"Failed to get notebooks: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/bag/notebooks", tags=["My Bag"])
async def create_notebook_alias(request: NotebookCreateRequest):
    """Alias for creating a notebook (frontend compatibility)."""
    return await create_notebook_endpoint(request)


@router.delete("/api/bag/notebook/delete", tags=["My Bag"])
async def delete_notebook_endpoint(request: NotebookDeleteRequest):
    """Delete a notebook and all its contents."""
    try:
        bag_service.delete_notebook(request.uid, request.notebook_id)
        return {"success": True, "message": "Notebook deleted"}
    except Exception as e:
        logger.error(f"Failed to delete notebook: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/bag/save", tags=["My Bag"])
async def save_to_bag_endpoint(request: SaveToBagRequest):
    """Save content to a notebook."""
    try:
        item_id = bag_service.save_to_bag(
            uid=request.uid,
            notebook_id=request.notebook_id,
            content=request.content,
            title=request.title,
            source_query=request.source_query,
            chapter_name=request.chapter_name,
            subject=request.subject
        )
        return {"success": True, "item_id": item_id, "message": "Saved to bag!"}
    except Exception as e:
        logger.error(f"Failed to save to bag: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/bag/items", tags=["My Bag"])
async def get_bag_items_endpoint(
    uid: str = Query(...),
    notebook_id: Optional[str] = Query(None)
):
    """Get items from bag, optionally filtered by notebook."""
    try:
        items = bag_service.get_bag_items(uid, notebook_id)
        return {"items": items, "total": len(items)}
    except Exception as e:
        logger.error(f"Failed to get bag items: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/bag/item/delete", tags=["My Bag"])
async def delete_bag_item_endpoint(request: DeleteBagItemRequest):
    """Delete an item from bag."""
    try:
        bag_service.delete_bag_item(request.uid, request.item_id)
        return {"success": True, "message": "Item deleted"}
    except Exception as e:
        logger.error(f"Failed to delete bag item: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/bag/items/{item_id}", tags=["My Bag"])
async def delete_bag_item_alias(item_id: str, uid: str):
    """Delete an item from bag (alias route)."""
    try:
        bag_service.delete_bag_item(uid, item_id)
        return {"success": True, "message": "Item deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/bag/item/toggle-favorite", tags=["My Bag"])
async def toggle_favorite_endpoint(request: ToggleFavoriteRequest):
    """Toggle favorite status of an item."""
    try:
        new_status = bag_service.toggle_favorite(request.uid, request.item_id)
        return {"success": True, "is_favorite": new_status}
    except Exception as e:
        logger.error(f"Failed to toggle favorite: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/bag/visual-library/add", tags=["My Bag"])
async def add_to_visual_library_endpoint(request: VisualLibraryAddRequest):
    """Save a reference to a video lesson (a users/{uid}/queries/{doc_id} doc) into Visual Library."""
    try:
        item_id = bag_service.add_to_visual_library(request.uid, request.doc_id)
        return {"success": True, "item_id": item_id, "message": "Saved to Visual Library!"}
    except Exception as e:
        logger.error(f"Failed to add to visual library: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/bag/visual-library/remove", tags=["My Bag"])
async def remove_from_visual_library_endpoint(request: VisualLibraryRemoveRequest):
    """Remove a saved video reference from Visual Library."""
    try:
        bag_service.remove_from_visual_library(request.uid, request.item_id)
        return {"success": True, "message": "Removed from Visual Library"}
    except Exception as e:
        logger.error(f"Failed to remove visual library item: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/bag/visual-library", tags=["My Bag"])
async def get_visual_library_endpoint(uid: str = Query(...)):
    """Get all saved video-lesson references for a user, resolved against their source query docs."""
    try:
        items = bag_service.get_visual_library(uid)
        return {"items": items, "total": len(items)}
    except Exception as e:
        logger.error(f"Failed to get visual library: {e}")
        raise HTTPException(status_code=500, detail=str(e))
