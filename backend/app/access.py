"""Central access-control helper.

`effective_role` collapses ownership + shares into one of
"owner" | "editor" | "viewer" | None. `require_document(min_role)` builds a
FastAPI dependency that loads a document and enforces a minimum role, applying
the 404-not-403 leak rule: no visibility at all -> 404; visible but under-
privileged -> 403.
"""

from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import get_current_user
from .db import get_db
from .models import Document, Share, User

ROLE_RANK = {"viewer": 1, "editor": 2, "owner": 3}


@dataclass
class DocAccess:
    document: Document
    role: str
    user: User


def effective_role(db: Session, user: User, document: Document) -> Optional[str]:
    if document.owner_id == user.id:
        return "owner"
    share = db.execute(
        select(Share).where(Share.document_id == document.id, Share.user_id == user.id)
    ).scalar_one_or_none()
    return share.role if share else None


def require_document(min_role: str):
    min_rank = ROLE_RANK[min_role]

    def dependency(
        doc_id: str,
        db: Session = Depends(get_db),
        user: User = Depends(get_current_user),
    ) -> DocAccess:
        document = db.get(Document, doc_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Document not found")
        role = effective_role(db, user, document)
        if role is None:
            # No visibility -> behave as if it does not exist.
            raise HTTPException(status_code=404, detail="Document not found")
        if ROLE_RANK[role] < min_rank:
            raise HTTPException(status_code=403, detail="You don't have permission to do that")
        return DocAccess(document=document, role=role, user=user)

    return dependency


require_reader = require_document("viewer")
require_editor = require_document("editor")
require_owner = require_document("owner")
