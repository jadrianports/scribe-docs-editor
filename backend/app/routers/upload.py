import os

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..content import ALLOWED_EXTENSIONS, MAX_UPLOAD_BYTES, md_to_html, txt_to_html
from ..db import get_db
from ..models import Document, User
from ..schemas import DocumentOut, OwnerOut

router = APIRouter(tags=["upload"])


@router.post("/documents/upload", response_model=DocumentOut, status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    filename = file.filename or "upload"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Only .txt and .md files are supported")

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 1 MB)")

    text = data.decode("utf-8", errors="replace")
    content_html = md_to_html(text) if ext == ".md" else txt_to_html(text)
    title = os.path.splitext(os.path.basename(filename))[0].strip() or "Untitled document"

    doc = Document(title=title, content_html=content_html, owner_id=user.id)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return DocumentOut(
        id=doc.id,
        title=doc.title,
        content_html=doc.content_html,
        role="owner",
        owner=OwnerOut(name=user.name),
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )
