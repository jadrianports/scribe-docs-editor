"""Tests for the server-side HTML -> Y.Doc seed converter and `seed_room`
orchestrator (D-06).

Unit-level only for this plan (10-04): the converter round-trip in
isolation, `seed_room`'s idempotence (D-18b), and its failure invariant
(D-20). The live-room concurrency proof (D-18a) and the full-schema
round-trip through a real room (D-19) land in plan 10-05, once
`rooms.py`'s `_create_room` actually calls `seed_room`.
"""

import gc

from pycrdt import Doc, XmlFragment
from app.collab import seeding as collab_seeding
from app.collab.html import ydoc_to_html
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


def test_idempotent_seed_room_second_call_is_a_noop(tmp_path, monkeypatch, db_session, seed_users):
    """D-18b: calling `seed_room` twice on the same `ydoc` is a no-op the
    second time -- the first call seeds and flips `config.seeded`, the
    second returns immediately via the D-09 gate without touching the
    fragment again (no duplication, no second insert).
    """
    monkeypatch.chdir(tmp_path)
    doc_row = Document(
        id="seed-1", title="T", content_html="<p>hello</p>", owner_id=seed_users["alice"].id
    )
    db_session.add(doc_row)
    db_session.commit()

    ydoc = Doc()
    seed_room("seed-1", ydoc)
    frag = ydoc.get("default", type=XmlFragment)
    first_pass_html = ydoc_to_html(ydoc)
    assert _already_seeded(ydoc) is True
    assert first_pass_html == "<p>hello</p>"
    assert len(frag.children) == 1

    seed_room("seed-1", ydoc)  # second call -- must be a no-op (D-18b)

    assert ydoc_to_html(ydoc) == first_pass_html
    assert len(frag.children) == 1
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
    seed_room("seed-fail-1", ydoc)

    assert _already_seeded(ydoc) is False
    frag = ydoc.get("default", type=XmlFragment)
    assert len(frag.children) == 0

    db_session.expire_all()  # seed_room reads via its own SessionLocal
    saved = db_session.get(Document, "seed-fail-1")
    assert saved.content_html == "<p>real content</p>"
    gc.collect()
