import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .models import Base

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./data/scribe.db")

# Make sure the parent directory of a SQLite file exists (handles both
# relative "sqlite:///./data/scribe.db" and absolute "sqlite:////data/scribe.db").
if DATABASE_URL.startswith("sqlite:///"):
    _path = DATABASE_URL.replace("sqlite:///", "", 1)
    _dir = os.path.dirname(_path)
    if _dir:
        os.makedirs(_dir, exist_ok=True)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
