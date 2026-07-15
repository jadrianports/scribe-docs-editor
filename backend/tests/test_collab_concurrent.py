"""Phase-0 gate: proves that two *concurrent* WebSocket clients joined to the
same room, served by a real uvicorn subprocess, converge via the Yjs sync
protocol.

This cannot run under Starlette's `TestClient` (see the module docstring in
`test_collab_spike.py`): `TestClient.websocket_connect()` spins up a fresh
anyio portal -- a new OS thread -- per call, and pycrdt's `Doc` is a Rust
object with thread affinity, so a room served across two connections opened
on two different portal threads is unsafe. A real uvicorn worker runs
everything on one asyncio event loop / one OS thread -- which is also the
production topology -- so this test launches `uvicorn app.main:app` as a
subprocess and drives it with the real `websockets` client library instead.

Reuses the exact pycrdt sync-message wiring already proven in
`test_collab_spike.py` (`create_update_message` / `handle_sync_message` /
reading the broadcast update); only the transport (real `websockets` client
vs `TestClient`) and the concurrency (two connections instead of one) are
new.

Deleted at the end of Phase 1 once Task 4 replaces `routers/collab.py` with
the real, authorized version (same fate as `test_collab_spike.py`).
"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import anyio
import httpx
import pytest
import websockets
from pycrdt import (
    Doc,
    Text,
    YMessageType,
    YSyncMessageType,
    create_update_message,
    handle_sync_message,
)

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_until_healthy(
    proc: subprocess.Popen, port: int, log_path: Path, timeout: float = 20.0
) -> None:
    """Poll /api/health instead of just the TCP socket: a bare `connect()`
    can succeed before the ASGI lifespan (init_db + seed) has finished, and
    we want the app fully up before driving it.
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
    """A real `uvicorn app.main:app` subprocess, pointed at a throwaway
    per-test SQLite DB so it never touches the dev/demo database.
    """
    port = _free_port()
    log_path = tmp_path / "uvicorn.log"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{(tmp_path / 'collab_test.db').as_posix()}"

    log_file = open(log_path, "wb")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=BACKEND_DIR,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_until_healthy(proc, port, log_path)
        yield f"ws://127.0.0.1:{port}/api/collab"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        log_file.close()


def test_two_concurrent_clients_converge_through_real_uvicorn(live_server):
    """Client A and client B hold two independent, simultaneously-open
    WebSocket connections to the *same* room. A makes a real edit and
    pushes it as a SYNC_UPDATE; B -- which never sent anything itself --
    must receive the room's broadcast and converge to the same document
    state. This is precisely the scenario the old spike route could not
    survive (`RuntimeError: YRoom already running`, see task-1-report.md);
    it now must, because the room lifecycle was fixed to start-once,
    serve-many.
    """

    async def scenario() -> None:
        uri = f"{live_server}/concurrent-spike"

        doc_a = Doc()
        text_a = doc_a.get("shared", type=Text)
        updates: list[bytes] = []
        doc_a.observe(lambda event: updates.append(event.update))

        doc_b = Doc()

        with anyio.fail_after(10):
            async with websockets.connect(uri) as ws_a, websockets.connect(uri) as ws_b:
                # Both clients receive the room's initial SYNC_STEP1 and
                # reply like a real client would (a no-op here: both sides
                # start empty), exactly as test_collab_spike.py does.
                initial_a = await ws_a.recv()
                reply_a = handle_sync_message(initial_a[1:], doc_a)
                if reply_a is not None:
                    await ws_a.send(reply_a)

                initial_b = await ws_b.recv()
                reply_b = handle_sync_message(initial_b[1:], doc_b)
                if reply_b is not None:
                    await ws_b.send(reply_b)

                # Client A makes a real edit and pushes it as a
                # SYNC_UPDATE, exactly as a browser client would.
                with doc_a.transaction():
                    text_a += "hello from A"
                assert len(updates) == 1
                await ws_a.send(create_update_message(updates[0]))

                # A gets its own broadcast-echo back (YRoom broadcasts to
                # all clients, including the sender) -- same assertion
                # test_collab_spike.py already makes over one connection.
                echo = await ws_a.recv()
                assert echo[0] == YMessageType.SYNC.value
                assert echo[1] == YSyncMessageType.SYNC_UPDATE.value

                # Client B -- a second, fully independent connection that
                # never sent anything itself -- must receive the same
                # broadcast. This is the real convergence assertion across
                # two concurrent connections.
                broadcast = await ws_b.recv()
                assert broadcast[0] == YMessageType.SYNC.value
                assert broadcast[1] == YSyncMessageType.SYNC_UPDATE.value
                handle_sync_message(broadcast[1:], doc_b)

        assert str(doc_b.get("shared", type=Text)) == "hello from A"

    anyio.run(scenario)
