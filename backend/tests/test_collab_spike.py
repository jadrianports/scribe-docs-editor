"""Phase-0 spike: proves pycrdt + pycrdt-websocket + StarletteChannel speak
the real Yjs sync protocol end to end over a Starlette/FastAPI WebSocket.
Deleted at the end of Phase 1 once the real collab feature replaces
`routers/collab.py` (Task 4).

Note: this deliberately does NOT open two *concurrent* connections to the
same doc_id (the brief's original scaffold). Spiking that uncovered a real
bug in the throwaway route (kept verbatim -- Task 4 replaces the whole
file): `collab_ws` calls `async with room:` on every connection, but
`YRoom.__aenter__` raises `RuntimeError("YRoom already running")` if a task
group is already active, so a second concurrent client to the same room
crashes immediately. See task-1-report.md for the full finding. The tests
below instead prove convergence with two independent `pycrdt.Doc` objects
over a single live connection, which exercises the same sync-message wire
format and CRDT merge logic without hitting that bug.
"""

from pycrdt import (
    Doc,
    Text,
    YMessageType,
    YSyncMessageType,
    create_update_message,
    handle_sync_message,
)

from app.routers.collab import _rooms


def test_server_sends_initial_sync_message(client):
    with client.websocket_connect("/api/collab/spike-initial") as ws:
        initial = ws.receive_bytes()

    assert len(initial) > 0
    assert initial[0] == YMessageType.SYNC.value
    assert initial[1] == YSyncMessageType.SYNC_STEP1.value


def test_edit_converges_to_a_second_doc_through_the_room(client):
    doc_id = "spike-converge"
    doc_a = Doc()
    text_a = doc_a.get("shared", type=Text)
    updates: list[bytes] = []
    doc_a.observe(lambda event: updates.append(event.update))

    with client.websocket_connect(f"/api/collab/{doc_id}") as ws:
        # Drain the room's initial SYNC_STEP1 and reply like a real client
        # would (a no-op here: both sides start empty).
        initial = ws.receive_bytes()
        reply = handle_sync_message(initial[1:], doc_a)
        if reply is not None:
            ws.send_bytes(reply)

        # Make a real edit and push it as a SYNC_UPDATE, exactly as a
        # browser client would.
        with doc_a.transaction():
            text_a += "hello"
        assert len(updates) == 1
        ws.send_bytes(create_update_message(updates[0]))

        # The room applies the update to its own Doc and, as the only other
        # thing happening on the wire right now, immediately broadcasts it
        # back down the same channel (YRoom broadcasts to all clients,
        # including the sender). A second, independent Doc that was never
        # connected to anything applies that broadcast message and must end
        # up with the same content -- proving the edit really went
        # client -> WebSocket -> StarletteChannel -> YRoom -> WebSocket.
        broadcast = ws.receive_bytes()
        assert broadcast[0] == YMessageType.SYNC.value
        assert broadcast[1] == YSyncMessageType.SYNC_UPDATE.value

        doc_b = Doc()
        handle_sync_message(broadcast[1:], doc_b)
        assert str(doc_b.get("shared", type=Text)) == "hello"

    # The room's own authoritative document converged too.
    assert str(_rooms[doc_id].ydoc.get("shared", type=Text)) == "hello"
