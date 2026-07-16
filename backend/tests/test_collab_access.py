import gc
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import anyio
import httpx
import pytest
import websockets
from pycrdt import Doc, Text, YMessageType, create_update_message, handle_sync_message
from starlette.websockets import WebSocketDisconnect

from app.models import Document
from app.seed import DEMO_PASSWORD

BACKEND_DIR = Path(__file__).resolve().parent.parent


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


# --- Task 10: same-origin-aware origin check (deploy-origin fix) ---
#
# `routers/collab.py::_origin_allowed` fails CLOSED by default -- a mismatched
# Origin is rejected -- so the risk this fix guards against isn't "opens up a
# hole," it's "collaboration silently never works on the single-service Render
# deploy" (SPA + API share one origin there, which never matches the hardcoded
# ALLOWED_WS_ORIGINS localhost defaults). Each test below drives the real route
# through `TestClient.websocket_connect` with a spoofed `Origin`/`Host` header
# pair -- exactly what the brief asked for -- rather than unit-testing
# `_origin_allowed` in isolation, so this proves the check is actually wired
# into `collab_ws` and runs before `_authorize()`, not just that the helper
# function's logic is correct on its own.
#
# All four log in as alice (the doc's owner) so a rejection can only be
# attributed to the origin check, never to authorization; the cross-origin
# case additionally asserts the specific 4403 close code, distinguishing
# "origin check rejected me" from any other reason a socket might close.


def test_same_origin_is_allowed(client, a_doc, login):
    """Origin's host:port equals the Host header -- the shape every request
    has on the single-service deploy, where the SPA and the API are served
    from one origin (e.g. https://scribe-docs-editor.onrender.com). Allowed
    even though "example.onrender.com" is nowhere in ALLOWED_WS_ORIGINS --
    same-origin is what makes this pass, not an allow-list entry.
    """
    login("alice@example.com")
    with client.websocket_connect(
        f"/api/collab/{a_doc.id}",
        headers={"origin": "https://example.onrender.com", "host": "example.onrender.com"},
    ) as ws:
        assert ws.receive_bytes()  # SYNC_STEP1 -- connection was accepted


def test_cross_origin_is_rejected(client, a_doc, login):
    """Origin host != Host header -- a cross-site page (e.g. an attacker's
    evil.com) trying to open a WebSocket against our origin using a victim's
    browser (which sends OUR cookies but THEIR page's Origin). Rejected with
    4403 even for alice, who owns the document -- proving the origin check
    runs, and blocks, before authorization is ever considered.
    """
    login("alice@example.com")
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            f"/api/collab/{a_doc.id}",
            headers={"origin": "https://evil.com", "host": "example.onrender.com"},
        ) as ws:
            ws.receive_bytes()
    assert exc_info.value.code == 4403


def test_explicit_allow_listed_origin_is_allowed(client, a_doc, login):
    """An Origin in ALLOWED_WS_ORIGINS (the dev default here includes
    http://localhost:5173) is allowed even when it doesn't match Host at all
    -- this is what keeps a split-origin setup (the Vite dev server on :5173
    talking to the API on :8000) working, unchanged from before this fix.
    """
    login("alice@example.com")
    with client.websocket_connect(
        f"/api/collab/{a_doc.id}",
        headers={"origin": "http://localhost:5173", "host": "example.onrender.com"},
    ) as ws:
        assert ws.receive_bytes()


def test_missing_origin_is_allowed(client, a_doc, login):
    """No Origin header at all (any non-browser client) is allowed through
    unconditionally, unchanged from before this fix -- there's nothing to
    compare it against. The explicit origin-focused counterpart to
    `test_owner_can_connect` above (which exists to prove auth succeeds, not
    specifically to document the origin check's behavior on a missing header).
    """
    login("alice@example.com")
    with client.websocket_connect(
        f"/api/collab/{a_doc.id}", headers={"host": "example.onrender.com"}
    ) as ws:
        assert ws.receive_bytes()


# --- Task 9: end-to-end proof that ReadOnlyChannel enforces server-side ---
#
# Everything above this point uses Starlette's `TestClient`, which is fine
# for "does the server accept/reject the connection" (one WS per portal
# thread, never touching pycrdt's Doc from more than one thread). It cannot
# safely prove *mutation enforcement* though: that needs two clients pushing
# real Yjs sync traffic at the *same* room at the *same* time, and
# TestClient spins up a fresh anyio portal (a new OS thread) per
# `websocket_connect()` call -- pycrdt's Doc is Rust-backed and thread-
# affine, so driving one room from two TestClient portal threads is unsafe
# (see task-1-report.md / the module docstring this replaces, at git history
# 65f0058:backend/tests/test_collab_concurrent.py, deleted in Task 4 when
# the unauthenticated spike route it drove was replaced by the real
# authorized one below).
#
# A real uvicorn subprocess runs everything on one event loop / one OS
# thread -- also the production topology -- so `live_server` launches
# `uvicorn app.main:app` as a subprocess and drives it with `httpx` (login)
# and the real `websockets` client (the collab socket itself), reusing that
# same subprocess+sync-handshake scaffolding.


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_until_healthy(proc: subprocess.Popen, port: int, log_path: Path, timeout: float = 20.0) -> None:
    """Poll /api/health instead of just the TCP socket: a bare `connect()`
    can succeed before the ASGI lifespan (init_db + seed) has finished, and
    we want the app fully up -- including the seeded demo users -- before
    driving it.
    """
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/api/health"
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"uvicorn subprocess exited early (code {proc.returncode}):\n"
                f"{log_path.read_text(errors='replace')}"
            )
        try:
            if httpx.get(url, timeout=1.0).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise RuntimeError(
        f"server did not become healthy on 127.0.0.1:{port} within {timeout}s:\n"
        f"{log_path.read_text(errors='replace')}"
    )


@pytest.fixture()
def live_server(tmp_path):
    """A real `uvicorn app.main:app` subprocess, isolated to a throwaway
    per-test SQLite database -- never `backend/data/scribe.db` /
    `backend/data/yjs.db`.

    Before this fix, this fixture deliberately ran against the REAL dev DB:
    proving `ReadOnlyChannel` enforces for an actual viewer needs the real
    seeded relationship from `app/seed.py` (carol is a *viewer* and bob is
    an *editor* on alice's "Project Roadmap"), and a fresh empty DB seemed
    to have no such relationship to test against. That reasoning missed that
    `seed()` (below) recreates the identical relationship on ANY empty DB --
    and running a subprocess against real dev data is also just unsafe on
    its own terms: `conftest.py`'s `SessionLocal` monkeypatch, which
    isolates every other test in this file, only rebinds the name inside
    *this* test process and can never reach a separate subprocess. Combined
    with the (now-fixed) CRITICAL snapshot-on-empty bug
    (`app.collab.snapshot.write_snapshot`), a plain `pytest` run could
    silently mutate/wipe the real seeded "Project Roadmap" document.

    `DATABASE_URL` and `SCRIBE_DATA_DIR` are set in the SUBPROCESS's own
    environment (not this test process's) to absolute paths under
    `tmp_path`, so both `scribe.db` (`app.db`) and `yjs.db`
    (`app.collab.ystore.ScribeYStore`, see its FIX 2/3 docstring) resolve
    there regardless of the subprocess's cwd -- `cwd=BACKEND_DIR` below is
    kept only so `-m uvicorn app.main:app` still resolves the `app` package.
    The lifespan's `seed()` still runs on startup exactly as it does against
    the real dev DB; it is idempotent on any DB with no users yet
    (`backend/app/seed.py`), so this fresh tmp DB gets the identical
    alice/bob/carol + "Project Roadmap" (bob=editor, carol=viewer) fixture
    the tests below need, with no register endpoint or hand-rolled seeding
    required here -- and, being fresh per test, never accumulates state
    across runs either.
    """
    port = _free_port()
    log_path = tmp_path / "uvicorn.log"
    log_file = open(log_path, "wb")
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{tmp_path / 'scribe.db'}",
        "SCRIBE_DATA_DIR": str(tmp_path),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=BACKEND_DIR,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_until_healthy(proc, port, log_path)
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        log_file.close()


def _login(base_url: str, email: str) -> str:
    """POST /api/auth/login and return the raw `session` cookie value."""
    resp = httpx.post(f"{base_url}/api/auth/login", json={"email": email, "password": DEMO_PASSWORD})
    resp.raise_for_status()
    cookie = resp.cookies.get("session")
    assert cookie, f"no session cookie set for {email}"
    return cookie


def _roadmap_doc_id(base_url: str, cookie: str) -> str:
    """Discover the seeded "Project Roadmap" doc_id via GET /api/documents
    rather than hardcoding it -- it's a fresh uuid4 each time app/seed.py
    creates it (the first time the dev DB is ever built).
    """
    resp = httpx.get(f"{base_url}/api/documents", cookies={"session": cookie})
    resp.raise_for_status()
    body = resp.json()
    for doc in body["owned"] + body["shared"]:
        if doc["title"] == "Project Roadmap":
            return doc["id"]
    raise AssertionError("seeded 'Project Roadmap' document not found via GET /api/documents")


async def _apply_pending(ws, doc: Doc, timeout: float) -> bool:
    """Receive at most one pending message within `timeout` seconds and, if
    it's a Yjs SYNC message (STEP1/STEP2/UPDATE alike -- handle_sync_message
    dispatches on the sub-type itself), apply it to `doc`. Returns False on
    timeout (nothing pending right now), True if a message was received.
    """
    try:
        with anyio.fail_after(timeout):
            msg = bytes(await ws.recv())
    except TimeoutError:
        return False
    if msg and msg[0] == YMessageType.SYNC.value:
        handle_sync_message(msg[1:], doc)
    return True


async def _drain(ws, doc: Doc, quiet: float = 0.5, overall: float = 5.0) -> None:
    """Apply every message already in flight on `ws` into `doc`, stopping
    once `quiet` seconds pass with nothing new (bounded by `overall`). This
    is how a client's local `doc` is brought up to date with whatever the
    room actually broadcast -- as opposed to what it merely might broadcast,
    which is the point when proving a viewer's edit was silently dropped.
    """
    with anyio.fail_after(overall):
        while await _apply_pending(ws, doc, timeout=quiet):
            pass


async def _handshake(ws, doc: Doc) -> None:
    """Perform the initial Yjs sync handshake: the room sends a SYNC_STEP1
    right after `accept()`; reply with our own diff if handle_sync_message
    says we have one. Mirrors the pattern already proven in the (deleted,
    see git history at 65f0058) test_collab_concurrent.py spike.
    """
    initial = bytes(await ws.recv())
    reply = handle_sync_message(initial[1:], doc)
    if reply is not None:
        await ws.send(reply)


def test_viewer_edit_is_dropped_editor_edit_is_applied(live_server):
    """End-to-end proof that `ReadOnlyChannel` enforces server-side -- not
    just that it's the class `collab_ws` *selects* for a viewer role (that
    much is already implied by reading routers/collab.py). Carol is seeded
    as a VIEWER on alice's "Project Roadmap" (app/seed.py); she does the
    real Yjs sync handshake over a real, authenticated WebSocket and pushes
    a genuine SYNC_UPDATE. If the server ever applied it, bob -- a second,
    independently-connected EDITOR on the same document -- would see it
    show up in his own converged `Doc` via the room's broadcast. He never
    does. Bob then makes the same kind of edit himself, and it both lands
    and broadcasts (to carol too -- a viewer is read-only, not blind) --
    proving the negative result above is because of the role, not because
    the whole plumbing is silently broken.

    Runs against `live_server`'s isolated, freshly-seeded per-test database
    (see its docstring) but only ever touches a scratch `Text` root
    ("collab-test-<uuid4>") that `ydoc_to_html` never reads (it only ever
    walks the "default" `XmlFragment` -- see app/collab/html.py's module
    docstring) -- so this can never change the Roadmap document's actual
    content or its HTML snapshot. Each run also uses a fresh uuid4 root name
    and fresh uuid4-suffixed marker text, so assertions never depend on
    (accumulated) state left behind by a previous run. Connecting to and
    disconnecting from the Roadmap document's room still triggers
    RoomManager.release()'s snapshot consideration once the room empties,
    but this fixture's fresh tmp yjs.db means that room's Y.Doc was never
    *seeded* (no TipTap client -- this test only ever speaks raw pycrdt --
    ever ran EditorPage.tsx's seed effect), so `write_snapshot`'s seeded
    guard (`app.collab.snapshot`, the final-review CRITICAL fix) skips the
    write entirely: neither `content_html` nor `updated_at` changes as a
    side effect of this test.
    """

    async def scenario() -> None:
        carol_cookie = _login(live_server, "carol@example.com")
        bob_cookie = _login(live_server, "bob@example.com")
        doc_id = _roadmap_doc_id(live_server, bob_cookie)

        ws_url = live_server.replace("http://", "ws://", 1) + f"/api/collab/{doc_id}"
        root_key = f"collab-test-{uuid.uuid4().hex}"
        carol_marker = f"CAROL-{uuid.uuid4().hex}"
        bob_marker = f"BOB-{uuid.uuid4().hex}"

        # No Origin header (the `websockets` client omits it by default):
        # the server's origin check only rejects a *mismatched* Origin --
        # a missing one (any non-browser client) is allowed through, see
        # routers/collab.py's `collab_ws`.
        with anyio.fail_after(30):
            async with (
                websockets.connect(
                    ws_url, additional_headers={"Cookie": f"session={carol_cookie}"}
                ) as carol_ws,
                websockets.connect(
                    ws_url, additional_headers={"Cookie": f"session={bob_cookie}"}
                ) as bob_ws,
            ):
                doc_carol = Doc()
                doc_bob = Doc()
                await _handshake(carol_ws, doc_carol)
                await _handshake(bob_ws, doc_bob)

                # --- Carol (viewer) tries to mutate the shared doc. ---
                carol_updates: list[bytes] = []
                doc_carol.observe(lambda event: carol_updates.append(event.update))
                with doc_carol.transaction():
                    doc_carol.get(root_key, type=Text).insert(0, carol_marker)
                await carol_ws.send(create_update_message(carol_updates[0]))

                # Give the (wrongly-applied, if this regressed) update a
                # moment to land and broadcast, then see what bob's already-
                # connected, independent client actually receives.
                await anyio.sleep(0.3)
                await _drain(bob_ws, doc_bob, quiet=0.5)
                assert carol_marker not in str(doc_bob.get(root_key, type=Text)), (
                    "viewer's SYNC_UPDATE was applied server-side -- "
                    "ReadOnlyChannel did not enforce read-only access"
                )

                # --- Bob (editor) makes the same kind of edit. ---
                bob_updates: list[bytes] = []
                doc_bob.observe(lambda event: bob_updates.append(event.update))
                with doc_bob.transaction():
                    doc_bob.get(root_key, type=Text).insert(0, bob_marker)
                await bob_ws.send(create_update_message(bob_updates[0]))

                # Bob sees his own broadcast-echo...
                await _drain(bob_ws, doc_bob, quiet=0.5)
                assert bob_marker in str(doc_bob.get(root_key, type=Text))
                assert carol_marker not in str(doc_bob.get(root_key, type=Text))

                # ...and so does carol: a viewer is read-only, not blind --
                # ReadOnlyChannel never overrides `send()`, only `__anext__`.
                await _drain(carol_ws, doc_carol, quiet=0.5)
                assert bob_marker in str(doc_carol.get(root_key, type=Text))

    anyio.run(scenario)
    gc.collect()


def test_concurrent_editor_inserts_at_same_position_both_survive_merged(live_server):
    """Secondary (Task 9, part C): the authenticated counterpart to Task 2's
    unauthenticated convergence proof (git history 65f0058:backend/tests/
    test_collab_concurrent.py) -- two *authenticated* collaborators (alice,
    the owner, and bob, a seeded editor) on the same real document, each
    inserting their own marker at offset 0 of a shared scratch `Text`
    without waiting for the other's update first. This is precisely the
    scenario a naive last-write-wins scheme would resolve by silently
    dropping one side; Yjs's CRDT merge must keep both. Lower priority than
    parts A/B per the task brief (concurrency itself was already proven
    without auth in Task 2) -- included because the live_server/login/
    handshake/drain scaffolding built for the viewer-enforcement test above
    made it cheap and low-risk to add on top, not because it was required.

    Same scratch-root-key / unique-marker approach as the viewer test above
    (see its docstring) to stay clear of the real "default" document
    content -- kept even though `live_server` now gives each run its own
    fresh, isolated database (so there is no accumulated state to begin
    with) for consistency with that test and as defense-in-depth against
    ever pointing this fixture at a shared database again.
    """

    async def scenario() -> None:
        alice_cookie = _login(live_server, "alice@example.com")
        bob_cookie = _login(live_server, "bob@example.com")
        doc_id = _roadmap_doc_id(live_server, bob_cookie)

        ws_url = live_server.replace("http://", "ws://", 1) + f"/api/collab/{doc_id}"
        root_key = f"collab-concurrent-{uuid.uuid4().hex}"
        alice_marker = f"ALICE-{uuid.uuid4().hex}"
        bob_marker = f"BOB-{uuid.uuid4().hex}"

        with anyio.fail_after(30):
            async with (
                websockets.connect(
                    ws_url, additional_headers={"Cookie": f"session={alice_cookie}"}
                ) as alice_ws,
                websockets.connect(
                    ws_url, additional_headers={"Cookie": f"session={bob_cookie}"}
                ) as bob_ws,
            ):
                doc_alice = Doc()
                doc_bob = Doc()
                await _handshake(alice_ws, doc_alice)
                await _handshake(bob_ws, doc_bob)

                alice_updates: list[bytes] = []
                doc_alice.observe(lambda event: alice_updates.append(event.update))
                with doc_alice.transaction():
                    doc_alice.get(root_key, type=Text).insert(0, alice_marker)

                bob_updates: list[bytes] = []
                doc_bob.observe(lambda event: bob_updates.append(event.update))
                with doc_bob.transaction():
                    doc_bob.get(root_key, type=Text).insert(0, bob_marker)

                # Fire both inserts concurrently -- neither client waits for
                # the other's update before sending its own.
                async with anyio.create_task_group() as tg:
                    tg.start_soon(alice_ws.send, create_update_message(alice_updates[0]))
                    tg.start_soon(bob_ws.send, create_update_message(bob_updates[0]))

                # Each client receives both broadcasts (its own echo and the
                # other's edit) and converges to the same merged text.
                await _drain(alice_ws, doc_alice, quiet=0.5)
                await _drain(bob_ws, doc_bob, quiet=0.5)

                final_alice = str(doc_alice.get(root_key, type=Text))
                final_bob = str(doc_bob.get(root_key, type=Text))

                assert alice_marker in final_alice
                assert bob_marker in final_alice
                assert alice_marker in final_bob
                assert bob_marker in final_bob
                assert final_alice == final_bob  # both clients converge identically

    anyio.run(scenario)
    gc.collect()
