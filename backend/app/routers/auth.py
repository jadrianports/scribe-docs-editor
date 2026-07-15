from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import get_current_user, login_session, logout_session, verify_password
from ..db import get_db
from ..models import User
from ..schemas import LoginRequest, UserOut

router = APIRouter(tags=["auth"])


@router.post("/auth/login", response_model=UserOut)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = db.execute(select(User).where(User.email == payload.email)).scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    login_session(request, user)
    return user


@router.post("/auth/logout", status_code=204)
def logout(request: Request):
    logout_session(request)
    return Response(status_code=204)


@router.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user
