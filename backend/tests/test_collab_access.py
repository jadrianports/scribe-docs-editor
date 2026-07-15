import gc

import pytest
from starlette.websockets import WebSocketDisconnect
from app.models import Document


@pytest.fixture(autouse=True)
def _gc_after_ws_test():
    """Each test here opens a real WebSocket via `TestClient`, which spins up
    a fresh anyio portal (OS thread) per connection; the server-side room
    created for `test_owner_can_connect` holds pycrdt's Rust-backed
    `Doc`/`Subscription` objects, which are thread-affine. If garbage
    collection for one of those objects happens to run on a different thread
    than it was created on (timing-dependent, not tied to test boundaries),
    it raises during `__del__`, surfacing as a `PytestUnraisableExceptionWarning`
    attributed to whatever test/file pytest's GC happens to run next --
    already observed and documented in task-1-report.md / task-3-report.md.
    Forcing collection here, still on this test's own thread, reclaims those
    objects deterministically before control returns to pytest or another
    test's TestClient thread (same mitigation Task 3 used in
    test_collab_room_manager.py).
    """
    yield
    gc.collect()


@pytest.fixture()
def a_doc(db_session, seed_users):
    doc = Document(title="D", content_html="<p>hi</p>", owner_id=seed_users["alice"].id)
    db_session.add(doc); db_session.commit()
    return doc


def test_unauthenticated_is_rejected(client, a_doc):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/api/collab/{a_doc.id}") as ws:
            ws.receive_bytes()


def test_non_collaborator_is_rejected(client, a_doc, seed_users, login):
    login("bob@example.com")  # bob has no relationship to alice's doc
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/api/collab/{a_doc.id}") as ws:
            ws.receive_bytes()


def test_owner_can_connect(client, a_doc, login):
    login("alice@example.com")
    with client.websocket_connect(f"/api/collab/{a_doc.id}") as ws:
        assert ws.receive_bytes()  # server sends initial SYNC_STEP1
