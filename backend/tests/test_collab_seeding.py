"""Tests for the server-side HTML -> Y.Doc seed converter and `seed_room`
orchestrator (D-06).

Unit-level for plan 10-04: the converter round-trip in isolation,
`seed_room`'s idempotence (D-18b), and its failure invariant (D-20).
Live-room for plan 10-05, now that `rooms.py`'s `_create_room` actually
calls `seed_room` (D-08): the true-concurrency exactly-once proof (D-18a,
Criterion 1) and the full-schema round trip through a real room (D-19).
"""

import gc

import anyio
import pytest
from pycrdt import Doc, XmlFragment
from app.collab import seeding as collab_seeding
from app.collab import snapshot as collab_snapshot
from app.collab.html import ydoc_to_html
from app.collab.rooms import RoomManager
from app.collab.seeding import _already_seeded, _html_to_prelim_tree, _materialize, seed_room
from app.content import sanitize_html
from app.models import Document

# A single string exercising every tag in content.ALLOWED_TAGS (p, br, strong,
# em, u, s, h1-h3, ul, ol, li, blockquote) -- mirrors test_collab_html.py's own
# D-27 full-schema fixture, built the other direction (HTML in, not ydoc in).
FULL_SCHEMA_HTML = (
    "<h1>Title</h1>"
    "<h2>Heading</h2>"
    "<h3>Subheading</h3>"
    "<p>plain <strong>bold</strong> <strong><em><u><s>combo</s></u></em></strong></p>"
    "<ul><li><p>First</p></li><li><p>Second</p></li></ul>"
    "<ol><li><p>Only</p></li></ol>"
    "<blockquote><p>quoted</p></blockquote>"
    "<p>line one<br>line two</p>"
    "<p></p>"
)


def test_convert_smoke_full_schema_round_trip_is_idempotent_on_second_pass():
    """D-04: `html -> ydoc -> html` is not asserted byte-exact against the
    input -- it is asserted equal to `sanitize_html(input)` after the
    outbound walk re-imposes `_MARK_ORDER`. This is idempotence on the
    *second* pass, not byte-exact ingest -- a future reader must not "fix"
    this into a stronger byte-equality assertion (see html.py's own
    `_MARK_ORDER` docstring for why the outbound walk's mark-nesting order
    is fixed and independent of Yjs's own unordered attrs map; here the
    input already writes marks in that same fixed order, so byte-equality
    happens to hold too, but the invariant this test protects is the weaker,
    always-true one).

    Builds the converter's off-doc tree directly (`_html_to_prelim_tree`)
    and integrates it the same way `seed_room` does (`_materialize`), on a
    bare `Doc()` -- no DB, no `seed_room` gate involved, isolating the
    converter itself from the orchestrator (covered separately in
    idempotence/failure tests below).
    """
    doc = Doc()
    frag = doc.get("default", type=XmlFragment)
    for node in _html_to_prelim_tree(FULL_SCHEMA_HTML):
        _materialize(frag, node)

    result = sanitize_html(ydoc_to_html(doc))
    assert result == sanitize_html(FULL_SCHEMA_HTML)
    gc.collect()


def test_convert_skips_comment_and_processing_instruction_nodes():
    """WR-04 regression: a leading (or inline) HTML comment or processing
    instruction must never be read as visible text. `sanitize_html`
    (bleach, `strip=True`) already strips these before `content_html` is
    ever stored through the app's own write path, so this exercises the
    converter directly (`_html_to_prelim_tree`), the same way the module's
    own module docstring frames this as "not reachable today, but a real
    gap in a converter with no compiler-enforced round-trip guarantee."
    """
    doc = Doc()
    frag = doc.get("default", type=XmlFragment)
    for node in _html_to_prelim_tree("<!-- injected comment text --><p>real</p>"):
        _materialize(frag, node)
    assert ydoc_to_html(doc) == "<p>real</p>"
    gc.collect()

    doc2 = Doc()
    frag2 = doc2.get("default", type=XmlFragment)
    for node in _html_to_prelim_tree("<p>before <!-- c -->after</p>"):
        _materialize(frag2, node)
    assert ydoc_to_html(doc2) == "<p>before after</p>"
    gc.collect()


def test_idempotent_seed_room_second_call_is_a_noop(tmp_path, monkeypatch, db_session, seed_users):
    """D-18b: calling `seed_room` twice on the same `ydoc` is a no-op the
    second time -- the first call seeds and flips `config.seeded`, the
    second returns immediately via the D-09 gate without touching the
    fragment again (no duplication, no second insert).

    `seed_room` is `async` (WR-02: its DB read + parse are offloaded via
    `asyncio.to_thread`), so both calls run inside an `anyio.run` scenario,
    matching this suite's existing convention for exercising async code.
    """
    monkeypatch.chdir(tmp_path)
    doc_row = Document(
        id="seed-1", title="T", content_html="<p>hello</p>", owner_id=seed_users["alice"].id
    )
    db_session.add(doc_row)
    db_session.commit()

    ydoc = Doc()

    async def scenario():
        await seed_room("seed-1", ydoc)
        frag = ydoc.get("default", type=XmlFragment)
        first_pass_html = ydoc_to_html(ydoc)
        assert _already_seeded(ydoc) is True
        assert first_pass_html == "<p>hello</p>"
        assert len(frag.children) == 1

        await seed_room("seed-1", ydoc)  # second call -- must be a no-op (D-18b)

        assert ydoc_to_html(ydoc) == first_pass_html
        assert len(frag.children) == 1

    anyio.run(scenario)
    gc.collect()


def test_seed_room_wraps_leading_bare_text_instead_of_failing_permanently(
    tmp_path, monkeypatch, db_session, seed_users
):
    """CR-01 regression: `content_html` starting with bare, non-block-wrapped
    text is reachable through the app's own `PATCH /documents/{doc_id}`
    endpoint -- `sanitize_html` strips disallowed tags but never adds a
    block wrapper around bare text. Before this fix, `_parse_fragment`'s
    `no_leading_text=True` turned this shape into a permanent, silent seed
    failure (`config.seeded` could never become true for that document).
    The leading text must instead be wrapped in its own paragraph, the same
    treatment already given to orphaned tail text (D-03).

    `seed_room` is `async` (WR-02), so this runs inside an `anyio.run`
    scenario, matching this suite's existing convention.
    """
    monkeypatch.chdir(tmp_path)
    doc_row = Document(
        id="seed-bare-1",
        title="T",
        content_html="hello <p>world</p>",
        owner_id=seed_users["alice"].id,
    )
    db_session.add(doc_row)
    db_session.commit()

    ydoc = Doc()

    async def scenario():
        await seed_room("seed-bare-1", ydoc)

    anyio.run(scenario)

    assert _already_seeded(ydoc) is True
    assert ydoc_to_html(ydoc) == "<p>hello </p><p>world</p>"
    gc.collect()


def test_seed_room_retry_after_materialize_failure_does_not_duplicate_content(
    tmp_path, monkeypatch, db_session, seed_users
):
    """WR-01 regression: `Transaction.__exit__` commits unconditionally even
    when an exception is raised inside the `with` block, so a `_materialize`
    exception partway through a multi-child tree still commits whatever was
    already appended before the raise, while `config.seeded` never gets set
    (the raise happens before that line). Pins the fixed retry behavior: the
    next `seed_room` call for the same doc_id must recognize the fragment is
    already non-empty, skip re-materializing on top of it (which would
    duplicate content), and still flip `config.seeded` true so the room
    isn't stuck retrying forever.

    `seed_room` is `async` (WR-02), so both calls run inside `anyio.run`
    scenarios, matching this suite's existing convention.
    """
    monkeypatch.chdir(tmp_path)
    doc_row = Document(
        id="seed-retry-1",
        title="T",
        content_html="<p>one</p><p>two</p><p>three</p>",
        owner_id=seed_users["alice"].id,
    )
    db_session.add(doc_row)
    db_session.commit()

    ydoc = Doc()
    real_materialize = collab_seeding._materialize
    call_count = {"n": 0}

    def _flaky_materialize(container, node):
        call_count["n"] += 1
        if call_count["n"] == 2:  # let the first top-level child land, then raise
            raise RuntimeError("boom mid-materialize")
        real_materialize(container, node)

    monkeypatch.setattr(collab_seeding, "_materialize", _flaky_materialize)

    async def first_attempt():
        await seed_room("seed-retry-1", ydoc)

    with pytest.raises(RuntimeError):
        anyio.run(first_attempt)

    frag = ydoc.get("default", type=XmlFragment)
    assert _already_seeded(ydoc) is False
    assert len(frag.children) == 1  # exactly the one child that landed before the raise

    monkeypatch.setattr(collab_seeding, "_materialize", real_materialize)

    async def retry():
        await seed_room("seed-retry-1", ydoc)  # must not duplicate the partial leftover

    anyio.run(retry)

    assert _already_seeded(ydoc) is True
    assert len(frag.children) == 1  # unchanged: retry skipped re-materializing
    gc.collect()


def test_failure_seed_room_leaves_fragment_exactly_empty_and_seeded_false(
    tmp_path, monkeypatch, db_session, seed_users
):
    """D-20: force `seed_room`'s converter to fail; afterwards `config.seeded`
    is false, the 'default' fragment is EXACTLY empty (`len(frag.children) ==
    0`, not merely "fewer than expected" -- Pitfall 1), and the DB
    `content_html` row is untouched. Patches `_html_to_prelim_tree` (the
    off-doc, fallible half) so the failure is injected before any doc
    mutation could occur, proving the two-phase build never partially
    commits on exception.

    `seed_room` is `async` (WR-02: its DB read + parse are offloaded via
    `asyncio.to_thread`), so this runs inside an `anyio.run` scenario,
    matching this suite's existing convention.
    """
    monkeypatch.chdir(tmp_path)
    doc_row = Document(
        id="seed-fail-1",
        title="T",
        content_html="<p>real content</p>",
        owner_id=seed_users["alice"].id,
    )
    db_session.add(doc_row)
    db_session.commit()

    def _boom(raw_html):
        raise RuntimeError("boom")

    monkeypatch.setattr(collab_seeding, "_html_to_prelim_tree", _boom)

    ydoc = Doc()

    async def scenario():
        await seed_room("seed-fail-1", ydoc)

    anyio.run(scenario)

    assert _already_seeded(ydoc) is False
    frag = ydoc.get("default", type=XmlFragment)
    assert len(frag.children) == 0

    db_session.expire_all()  # seed_room reads via its own SessionLocal
    saved = db_session.get(Document, "seed-fail-1")
    assert saved.content_html == "<p>real content</p>"
    gc.collect()


def test_exactly_once_seed_under_true_concurrency(tmp_path, monkeypatch, db_session, seed_users):
    """D-18a / Criterion 1: the true-concurrency test that would have caught
    the original multi-client seed race. A Document row exists with
    non-empty `content_html` and has never been collaboratively seeded. Two
    simultaneous `mgr.get(doc_id)` calls for that SAME never-seeded doc_id
    (started concurrently via `anyio.create_task_group` + two
    `tg.start_soon(...)`, not one awaited after the other) must result in
    the canonical room ydoc containing the seed content EXACTLY once, with
    `config.seeded` true -- proving Criterion 2 ("server-side by
    construction") holds under real concurrency, not just sequentially.

    This falls out of `_create_room` running the whole rehydrate-seed-
    subscribe sequence under `doc_id`'s own per-doc lock (D-08): whichever
    `get()` call wins the lock race builds (and seeds) the one room; the
    other blocks on the SAME lock and, once unblocked, finds the room
    already cached rather than building (and seeding) a second one -- which
    is also why both calls share one cached `RoomRecord` (`rec.count == 2`)
    instead of two independent rooms.
    """
    monkeypatch.chdir(tmp_path)
    doc_row = Document(
        id="race-1",
        title="T",
        content_html="<p>hello world</p>",
        owner_id=seed_users["alice"].id,
    )
    db_session.add(doc_row)
    db_session.commit()

    async def scenario():
        mgr = RoomManager()

        async def get_it():
            await mgr.get("race-1")

        async with anyio.create_task_group() as tg:
            tg.start_soon(get_it)
            tg.start_soon(get_it)

        rec = mgr._rooms_by_id["race-1"]
        assert rec.count == 2  # both gets shared one cached room, not two

        assert _already_seeded(rec.room.ydoc) is True
        html = ydoc_to_html(rec.room.ydoc)
        assert html.count("hello world") == 1  # seed content landed exactly once

        await mgr.release("race-1")
        await mgr.release("race-1")

    anyio.run(scenario)
    gc.collect()


def test_live_room_full_schema_round_trip_is_idempotent_on_second_pass(
    tmp_path, monkeypatch, db_session, seed_users
):
    """D-19/D-04: opening a never-seeded document's room via
    `room_manager.get(doc_id)` seeds it server-side (D-08); deriving HTML
    back from the LIVE room's ydoc equals `sanitize_html(FULL_SCHEMA_HTML)`
    -- the same idempotent-on-second-pass invariant as
    `test_convert_smoke_full_schema_round_trip_is_idempotent_on_second_pass`
    above, now proven through a real room (real rehydrate/seed/observe
    sequence) rather than a bare `Doc()` + direct `_materialize` call. This
    is NOT a byte-exact-ingest assertion -- a future reader must not
    "fix" this into a stronger byte-equality check (see that test's own
    docstring for why).
    """
    monkeypatch.chdir(tmp_path)
    doc_row = Document(
        id="roundtrip-1",
        title="T",
        content_html=FULL_SCHEMA_HTML,
        owner_id=seed_users["alice"].id,
    )
    db_session.add(doc_row)
    db_session.commit()

    async def scenario():
        mgr = RoomManager()
        room = await mgr.get("roundtrip-1")  # seeds server-side on creation (D-08)

        assert _already_seeded(room.ydoc) is True
        html = collab_snapshot.derive_snapshot_html(room.ydoc)
        assert html == sanitize_html(FULL_SCHEMA_HTML)

        await mgr.release("roundtrip-1")

    anyio.run(scenario)
    gc.collect()
