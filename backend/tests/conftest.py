import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

from app.auth import hash_password
from app.collab import snapshot as collab_snapshot
from app.db import get_db
from app.main import app
from app.models import Base, User
from app.routers import collab as collab_router


@pytest.fixture()
def db_session(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    # The collab WS route authorizes by calling `app.db.SessionLocal` directly
    # (`app/routers/collab.py::_authorize`), not FastAPI's `Depends(get_db)`
    # that the `client` fixture below overrides -- a websocket connection has
    # no single per-request DI point the way an HTTP request does. Because
    # `app/routers/collab.py` does `from ..db import SessionLocal`, that name
    # was bound into *its own* module namespace at import time; patching
    # `app.db.SessionLocal` itself would not reach that already-imported
    # local binding. Redirect the binding actually used
    # (`app.routers.collab.SessionLocal`) to this test's isolated engine
    # instead, so a session opened during a WS test sees the same seeded
    # users/documents as the rest of the test, not the real
    # backend/data/scribe.db.
    monkeypatch.setattr(collab_router, "SessionLocal", TestingSessionLocal)
    # Same trap, same fix, for Task 7's snapshot writer: `RoomManager.release()`
    # calls `app.collab.snapshot.write_snapshot()`, which does its own
    # `from ..db import SessionLocal` + `SessionLocal()` (release() has no
    # request-scoped session to pass down -- a room emptying isn't a single
    # DI-scoped HTTP request). Confirmed empirically this isn't just
    # theoretical: without this redirect, write_snapshot's unpatched
    # SessionLocal() resolves against the *real* backend/data/scribe.db --
    # pinned there by SQLAlchemy at engine-creation (import) time, so even
    # `monkeypatch.chdir(tmp_path)` elsewhere in a test does NOT retarget it
    # the way it retargets ScribeYStore's relative "data/yjs.db" path. Left
    # unfixed, a snapshot test would silently query prod, find no matching
    # doc_id, and no-op -- passing or failing for the wrong reason instead of
    # exercising the real write path.
    monkeypatch.setattr(collab_snapshot, "SessionLocal", TestingSessionLocal)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    # Instantiated without a context manager, so the app lifespan (which seeds
    # the production DB) never runs — tests stay fully isolated on their temp DB.
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def seed_users(db_session):
    users = {
        "alice": User(email="alice@example.com", name="Alice", password_hash=hash_password("pw")),
        "bob": User(email="bob@example.com", name="Bob", password_hash=hash_password("pw")),
    }
    db_session.add_all(list(users.values()))
    db_session.commit()
    return users


@pytest.fixture()
def login(client):
    def _login(email, password="pw"):
        res = client.post("/api/auth/login", json={"email": email, "password": password})
        assert res.status_code == 200, res.text
        return res

    return _login
