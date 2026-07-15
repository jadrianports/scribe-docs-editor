from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..access import DocAccess, require_editor, require_owner, require_reader
from ..auth import get_current_user
from ..content import sanitize_html
from ..db import get_db
from ..models import Document, Share, User
from ..schemas import (
    DocumentListOut,
    DocumentOut,
    DocumentSummary,
    DocumentUpdate,
    OwnerOut,
)

router = APIRouter(tags=["documents"])


def _summary(doc: Document, role: str) -> DocumentSummary:
    return DocumentSummary(
        id=doc.id,
        title=doc.title,
        updated_at=doc.updated_at,
        owner=OwnerOut(name=doc.owner.name),
        role=role,
    )


def _out(doc: Document, role: str) -> DocumentOut:
    return DocumentOut(
        id=doc.id,
        title=doc.title,
        content_html=doc.content_html,
        role=role,
        owner=OwnerOut(name=doc.owner.name),
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


@router.get("/documents", response_model=DocumentListOut)
def list_documents(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    owned = (
        db.execute(
            select(Document)
            .where(Document.owner_id == user.id)
            .order_by(Document.updated_at.desc())
        )
        .scalars()
        .all()
    )
    shared_rows = db.execute(
        select(Document, Share.role)
        .join(Share, Share.document_id == Document.id)
        .where(Share.user_id == user.id)
        .order_by(Document.updated_at.desc())
    ).all()
    return DocumentListOut(
        owned=[_summary(doc, "owner") for doc in owned],
        shared=[_summary(doc, role) for (doc, role) in shared_rows],
    )


@router.post("/documents", response_model=DocumentOut, status_code=201)
def create_document(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    doc = Document(title="Untitled document", content_html="", owner_id=user.id)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return _out(doc, "owner")


@router.get("/documents/{doc_id}", response_model=DocumentOut)
def get_document(acc: DocAccess = Depends(require_reader)):
    return _out(acc.document, acc.role)


@router.patch("/documents/{doc_id}", response_model=DocumentOut)
def update_document(
    payload: DocumentUpdate,
    db: Session = Depends(get_db),
    acc: DocAccess = Depends(require_editor),
):
    doc = acc.document
    if payload.title is not None:
        doc.title = payload.title.strip() or "Untitled document"
    if payload.content_html is not None:
        doc.content_html = sanitize_html(payload.content_html)
    db.commit()
    db.refresh(doc)
    return _out(doc, acc.role)


@router.delete("/documents/{doc_id}", status_code=204)
def delete_document(db: Session = Depends(get_db), acc: DocAccess = Depends(require_owner)):
    db.delete(acc.document)
    db.commit()
    return Response(status_code=204)
