import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

from app.auth import hash_password
from app.db import get_db
from app.main import app
from app.models import Base, User


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
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
