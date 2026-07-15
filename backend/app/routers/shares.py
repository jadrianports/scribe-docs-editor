from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..access import DocAccess, require_owner
from ..db import get_db
from ..models import Share, User
from ..schemas import ShareCreate, ShareOut

router = APIRouter(tags=["shares"])


@router.get("/documents/{doc_id}/shares", response_model=list[ShareOut])
def list_shares(db: Session = Depends(get_db), acc: DocAccess = Depends(require_owner)):
    rows = db.execute(
        select(Share, User)
        .join(User, User.id == Share.user_id)
        .where(Share.document_id == acc.document.id)
        .order_by(User.name)
    ).all()
    return [
        ShareOut(user_id=user.id, name=user.name, email=user.email, role=share.role)
        for (share, user) in rows
    ]


@router.post("/documents/{doc_id}/shares", response_model=ShareOut)
def add_share(
    payload: ShareCreate,
    response: Response,
    db: Session = Depends(get_db),
    acc: DocAccess = Depends(require_owner),
):
    grantee = db.execute(select(User).where(User.email == payload.email)).scalar_one_or_none()
    if grantee is None:
        raise HTTPException(status_code=404, detail="No user with that email")
    if grantee.id == acc.user.id:
        raise HTTPException(status_code=400, detail="You can't share a document with yourself")

    existing = db.execute(
        select(Share).where(
            Share.document_id == acc.document.id, Share.user_id == grantee.id
        )
    ).scalar_one_or_none()
    if existing:
        existing.role = payload.role
        response.status_code = 200
    else:
        db.add(Share(document_id=acc.document.id, user_id=grantee.id, role=payload.role))
        response.status_code = 201
    db.commit()
    return ShareOut(user_id=grantee.id, name=grantee.name, email=grantee.email, role=payload.role)


@router.delete("/documents/{doc_id}/shares/{user_id}", status_code=204)
def revoke_share(
    user_id: int,
    db: Session = Depends(get_db),
    acc: DocAccess = Depends(require_owner),
):
    share = db.execute(
        select(Share).where(
            Share.document_id == acc.document.id, Share.user_id == user_id
        )
    ).scalar_one_or_none()
    if share:
        db.delete(share)
        db.commit()
    return Response(status_code=204)
