from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr

Role = Literal["viewer", "editor"]


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    email: EmailStr


class OwnerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    name: str


class DocumentSummary(BaseModel):
    id: str
    title: str
    updated_at: datetime
    owner: OwnerOut
    role: str


class DocumentListOut(BaseModel):
    owned: list[DocumentSummary]
    shared: list[DocumentSummary]


class DocumentOut(BaseModel):
    id: str
    title: str
    content_html: str
    role: str
    owner: OwnerOut
    created_at: datetime
    updated_at: datetime


class DocumentUpdate(BaseModel):
    title: Optional[str] = None
    content_html: Optional[str] = None


class ShareCreate(BaseModel):
    email: EmailStr
    role: Role


class ShareOut(BaseModel):
    user_id: int
    name: str
    email: EmailStr
    role: str
