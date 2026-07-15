import anyio
from pycrdt import Doc, Text
from app.collab.rooms import RoomManager


def test_updates_persist_across_room_restart(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # yjs.db is created under ./data
    async def scenario():
        mgr = RoomManager()
        room = await mgr.get("doc-1")
        room.ydoc.get("t", type=Text).insert(0, "hello")
        await anyio.sleep(0.2)          # let the room persist the update
        await mgr.release("doc-1")      # stops the room
        room2 = await mgr.get("doc-1")  # fresh room, must rehydrate
        assert str(room2.ydoc.get("t", type=Text)) == "hello"
    anyio.run(scenario)
