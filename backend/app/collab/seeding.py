"""Server-side HTML -> Y.Doc seed converter and orchestrator (D-06).

Owns the whole seeding concern for a room: the should-we-seed gate, the DB
read of the document's last-saved `content_html`, the HTML->CRDT conversion,
and the `config.seeded` write. `rooms.py`'s `_create_room` calls
`await seed_room(doc_id, ydoc)` once, after `ydoc_observed.wait()` and
before the dirty-subscription is registered (D-08, plan 10-05) -- under the
same per-doc_id lock that makes this exactly-once by construction.

WR-02: `seed_room` is `async` so its DB read and CPU-bound lxml parse (both
off-doc, thread-safe -- see `_read_and_convert`) can be offloaded to a
worker thread via `asyncio.to_thread`, the same pattern `rooms.py` already
uses for its persist calls. Without this, a slow SQLite read or a large
document's parse would run directly on the event loop, inside `_create_room`'s
startup critical section -- stalling every OTHER doc_id's `get()`/`release()`
and WS message pumping for the duration, not just this one's. Only the
doc-mutating half (the transaction/materialize/config-flip) stays on the
event-loop thread, since pycrdt objects are thread-affine and must only ever
be touched from the thread that owns them.

`html.py`'s outbound walk (`ydoc_to_html`) *is* the spec for this inbound
converter (D-01): its tag/mark/heading tables are imported and inverted at
module load (D-07) so a change to those tables teaches the seeder by
construction rather than by a second, independently-maintained table
silently drifting out of sync.

Two-phase build-then-integrate (RESEARCH.md Pattern 1, load-bearing for
D-12/D-20): `pycrdt.Transaction.__exit__` commits unconditionally -- it does
not roll back on an exception raised inside a `with doc.transaction():`
block. So ALL fallible work (lxml parsing, tag/mark lookups, building the
prelim tree) happens off-doc, in `_html_to_prelim_tree`, entirely in plain
Python objects (`_PrelimElement`/`_PrelimText`) that touch no `ydoc`. Only
after that succeeds does `seed_room` open one transaction and materialize
the already-validated tree -- the only doc-touching statements are
`_materialize`'s `container.children.append(...)` / `XmlText.insert(...)`
calls and the final `config[seeded] = True`. If the off-doc build raises,
`seed_room` logs and returns without ever touching `ydoc` (D-12): the
fragment stays exactly empty and `config.seeded` stays false.

Mark representation (mirrors html.py's own docstring exactly, inverted):
marks are detected by tag identity while descending the lxml tree
(`<strong>/<em>/<u>/<s>` -> `_TAG_TO_MARK`), and written as per-run
`XmlText.insert(index, text, attrs={...})` calls with each active mark a key
carrying `{}` as its value -- never `True` -- so the outbound reader's
key-presence check (`"bold" in attrs`) sees them. Marks require an
*integrated* `XmlText` (`.insert`/`.format` need `self.integrated`), so
unlike the rest of the prelim tree, per-run marks are applied after
integration, from an already-fully-computed, already-validated
`(text, attrs)` pair list -- negligible risk of raising at that point
(Pattern 1's one caveat).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from lxml import html as lxml_html
from lxml.etree import _Comment, _ProcessingInstruction
from pycrdt import Doc, Map, XmlElement, XmlFragment, XmlText

from ..db import SessionLocal
from ..models import Document
from .html import _BLOCK_TAGS, _HEADING_TAGS, _MARK_TAGS

log = logging.getLogger(__name__)

# D-23: centralised map name / flag key -- defined once here so the writer
# (this module) and every reader (snapshot.py, plan 10-05) can never drift on
# a typo between "config"/"seeded" string literals.
CONFIG_MAP_NAME = "config"
SEEDED_KEY = "seeded"

# D-07: reverse maps built by inverting html.py's own tables at module load.
# Adding/renaming a tag in html.py's tables teaches this seeder about it by
# construction -- these are NOT a second, independently-maintained table.
_TAG_TO_BLOCK = {html_tag: node_tag for node_tag, html_tag in _BLOCK_TAGS.items()}
_LEVEL_TO_HEADING = {html_tag: level for level, html_tag in _HEADING_TAGS.items()}
_TAG_TO_MARK = {html_tag: mark for mark, html_tag in _MARK_TAGS.items()}

_HEADING_HTML_TAGS = frozenset(_HEADING_TAGS.values())  # {"h1", "h2", "h3"}
_BLOCK_HTML_TAGS = frozenset(_BLOCK_TAGS.values())  # {"p", "ul", "ol", "li", "blockquote"}


@dataclass
class _PrelimElement:
    """An off-doc, not-yet-integrated block node (Pitfall 1).

    `tag`/`attrs` are already fully resolved (fallbacks applied, `level`
    already a genuine int) by construction time, so materializing this later
    cannot fail for schema reasons -- all the fallible work already happened
    building this value.
    """

    tag: str
    attrs: dict[str, int]
    children: list["_PrelimElement | _PrelimText"] = field(default_factory=list)


@dataclass
class _PrelimText:
    """An off-doc placeholder for one `XmlText` leaf and its formatted runs.

    `runs` is an ordered list of `(text, attrs)` pairs; `attrs` uses key
    presence (`{"bold": {}}`), never truthiness, mirroring html.py's
    outbound `_run_html` convention in reverse.
    """

    runs: list[tuple[str, dict[str, dict]]]


def _read_content_html(doc_id: str) -> str | None:
    """Read `Document.content_html` for `doc_id` via its own session.

    Mirrors `persist_snapshot_html` exactly (D-13): its own `SessionLocal()`,
    `try/finally db.close()`, and a silent `None` on a missing row (a
    document deleted out from under a live room) rather than raising.
    """
    db = SessionLocal()
    try:
        document = db.get(Document, doc_id)
        return document.content_html if document is not None else None
    finally:
        db.close()


def _already_seeded(ydoc: Doc) -> bool:
    """D-09 -- the should-we-seed gate, the same map/key `snapshot.py` reads.

    Always reached via `ydoc.get(name, type=T)` (get-or-create-and-cast),
    never a fresh `Map()` construct-and-assign (Pitfall 4).
    """
    return bool(ydoc.get(CONFIG_MAP_NAME, type=Map).get(SEEDED_KEY))


def _parse_fragment(raw_html: str):
    """Parse `raw_html` leniently (D-02).

    NEVER use lxml's strict-XML parsing submodule for this (Pitfall 2):
    `content_html` has un-self-closed void tags (`<br>`) and multiple
    top-level sibling elements, both of which violate the well-formed-XML
    requirements that submodule enforces.
    Leading text is allowed, not rejected (CR-01): `sanitize_html` (bleach)
    strips disallowed tags but never *adds* a block wrapper around bare
    text, so `content_html` written through the app's own
    `PATCH /documents/{doc_id}` endpoint can legitimately start with bare,
    non-block-wrapped text -- rejecting that shape (the old
    `no_leading_text=True`) turned a normal write into a permanent, silent
    seed failure. Without that flag, `fragments_fromstring` hands leading/
    orphan text back as a plain `str` in the returned list;
    `_convert_block_siblings` wraps it in its own paragraph, the same
    treatment already given to orphaned *tail* text between siblings
    (D-03).
    """
    return lxml_html.fragments_fromstring(raw_html)


def _is_block_tag(tag: str) -> bool:
    """True for html tags that become their own nested prelim block element
    (paragraph/heading/list/blockquote) when walked. Deliberately excludes
    `br`: hardBreak is inline-safe in html.py's own model (`_is_inline_safe`)
    despite being its own `XmlElement` leaf, and is dispatched by its own
    branch in `_walk_children`, not this check.
    """
    return tag in _HEADING_HTML_TAGS or tag in _BLOCK_HTML_TAGS


def _is_real_element(node) -> bool:
    """False for a comment or processing-instruction node (WR-04); True for
    a genuine HTML element.

    `lxml_html.fragments_fromstring` hands comment/PI nodes back alongside
    real elements wherever they appear -- as top-level siblings
    (`_convert_block_siblings`) or as children while walking `.text`/`.tail`
    (`_walk_children`'s `walk_inline`) -- and neither `.tag` (a factory
    function, not a string, for these node kinds) nor any lookup keyed on it
    raises for one. Without this check, a comment/PI's `.tag` falls through
    every tag-dispatch to the "unknown -> paragraph"/"unknown inline ->
    text" default, and its raw `.text` gets read as if it were real content
    -- silently injecting e.g. an HTML comment's text into the document.
    Not reachable through any current write path (`sanitize_html`/bleach,
    `strip=True`, strips comments before `content_html` is ever stored), but
    a real gap in a converter with "no compiler-enforced round-trip
    guarantee" (CLAUDE.md) against any future write path that stores
    `content_html` without going through `sanitize_html`.
    """
    return not isinstance(node, (_Comment, _ProcessingInstruction))


def _walk_children(el) -> list["_PrelimElement | _PrelimText"]:
    """Walk `el`'s child nodes (lxml's `.text`/`.tail` model) building the
    list of prelim children for one container -- the single traversal used
    for every block-level element (`p`/heading/`li`/`blockquote`/`ul`/`ol`).

    Marks accumulate through nested `<strong>/<em>/<u>/<s>` into run attrs
    (key presence -- D-01, mirrors html.py's `_run_html` in reverse). `<br>`
    flushes the current run and becomes its own hardBreak sibling. Any
    recognized block-tag child (D-03: `_is_block_tag`) recurses via
    `_convert_block_element`; anything else is treated as inline content,
    descended into for its text (D-03: unknown inline -> plain text, keep
    all descendant text, never raise for schema reasons).

    Whitespace-only text is dropped when this container has at least one
    real block-tag child anywhere (D-05 -- "directly between block tags",
    e.g. the newline/indentation text between sibling `<li>` tags); kept
    otherwise, including for a container with only a hardBreak child, since
    `br` does not count as a "block" child for this rule.
    """
    has_block_children = any(_is_block_tag(child.tag) for child in el)
    out: list[_PrelimElement | _PrelimText] = []
    runs: list[tuple[str, dict]] = []

    def flush() -> None:
        if not runs:
            return
        all_whitespace = all(not text.strip() for text, _attrs in runs)
        if not (has_block_children and all_whitespace):
            out.append(_PrelimText(list(runs)))
        runs.clear()

    def walk_inline(node, active_marks: tuple[str, ...]) -> None:
        if node.text:
            runs.append((node.text, {mark: {} for mark in active_marks}))
        for child in node:
            if not _is_real_element(child):
                # WR-04: skip a comment/PI node's own "content" entirely --
                # never descend into it or read its .text -- but its .tail
                # (real surrounding text) is still genuine content to keep.
                if child.tail:
                    runs.append((child.tail, {m: {} for m in active_marks}))
                continue
            mark = _TAG_TO_MARK.get(child.tag)
            if mark is not None:
                walk_inline(child, active_marks + (mark,))
            elif child.tag == "br":
                flush()
                out.append(_PrelimElement("hardBreak", {}))
            elif _is_block_tag(child.tag):
                flush()
                out.append(_convert_block_element(child))
            else:
                # D-03: unknown inline element -> plain text, keep its own
                # descendant text under the same marks as its surroundings.
                walk_inline(child, active_marks)
            if child.tail:
                runs.append((child.tail, {mark: {} for mark in active_marks}))

    walk_inline(el, ())
    flush()
    return out


def _is_inline_prelim(node: "_PrelimElement | _PrelimText") -> bool:
    """True if `node` is inline content that cannot stand alone as a
    `listItem` child. Mirrors html.py's `_is_inline_safe` in reverse: only
    text runs and `hardBreak` are inline; every other prelim element is a
    block node.
    """
    return isinstance(node, _PrelimText) or node.tag == "hardBreak"


def _normalize_list_item_children(
    children: list["_PrelimElement | _PrelimText"],
) -> list["_PrelimElement | _PrelimText"]:
    """Wrap a `listItem`'s inline content in a `paragraph` so the node
    satisfies ProseMirror's content spec for list items.

    Unlike `paragraph`/`heading` -- textblocks that legitimately hold text
    directly -- TipTap StarterKit's `ListItem` declares `content:
    "paragraph block*"`. A `listItem` holding a raw text run is therefore
    schema-invalid, and ProseMirror **silently drops the whole node** when
    the Y.Doc syncs into the editor: a seeded `<ul><li>a</li></ul>` renders
    as nothing at all, with no error on either side.

    That failure is invisible to a backend-only round-trip test, because
    html.py's `ydoc_to_html` happily walks the malformed shape back to
    correct `<ul><li>a</li></ul>` HTML -- the two halves stay consistent
    inverses of each other while the real editor shows an empty document.
    Hence this normalization lives here, at the one place that builds
    `listItem` nodes, and `test_collab_seeding.py` asserts the resulting
    shape directly rather than only asserting the HTML round-trip.

    Consecutive inline nodes collapse into one paragraph; block children
    (a nested `bulletList`, an explicit `<li><p>...</p></li>`) pass through
    untouched. A leading block child still gets an empty paragraph in front
    of it, since the spec requires the *first* child to be a paragraph.
    """
    normalized: list[_PrelimElement | _PrelimText] = []
    run: list[_PrelimElement | _PrelimText] = []

    def flush_run() -> None:
        if run:
            normalized.append(_PrelimElement("paragraph", {}, list(run)))
            run.clear()

    for child in children:
        if _is_inline_prelim(child):
            run.append(child)
        else:
            flush_run()
            normalized.append(child)
    flush_run()

    if not normalized or (
        isinstance(normalized[0], _PrelimElement) and normalized[0].tag != "paragraph"
    ):
        normalized.insert(0, _PrelimElement("paragraph", {}))
    return normalized


def _convert_block_element(el) -> _PrelimElement:
    """Convert one block-level lxml element to a prelim block node -- the
    inverse of html.py's `_element_html` (D-01/D-03)."""
    if el.tag == "br":
        return _PrelimElement("hardBreak", {})
    if el.tag in _HEADING_HTML_TAGS:
        level = _LEVEL_TO_HEADING.get(el.tag, 1)
        return _PrelimElement("heading", {"level": level}, _walk_children(el))
    node_tag = _TAG_TO_BLOCK.get(el.tag, "paragraph")  # D-03: unknown element -> paragraph
    children = _walk_children(el)
    if node_tag == "listItem":
        children = _normalize_list_item_children(children)
    return _PrelimElement(node_tag, {}, children)


def _convert_block_siblings(elements) -> list[_PrelimElement]:
    """Convert the top-level sibling elements `lxml.html.fragments_fromstring`
    returns into prelim block nodes, applying D-05's whitespace-only-tail
    drop between top-level siblings; genuine (non-whitespace) orphaned text
    falls back to its own paragraph (D-03), never raising.

    `elements` may itself start with a plain `str` (CR-01): without
    `no_leading_text=True`, genuine top-level leading text -- the shape
    `content_html` written via `PATCH /documents/{doc_id}` can legitimately
    have, since `sanitize_html` never adds a block wrapper -- comes back
    from lxml as a bare string rather than raising. It gets the exact same
    treatment as orphaned tail text below: wrapped in its own paragraph if
    non-whitespace, dropped if whitespace-only.

    A top-level element may also be a comment/PI node (WR-04) -- skipped
    entirely (never converted, unlike a real element), but its `.tail` (real
    surrounding text) still gets the same tail-wrapping treatment as any
    other top-level sibling's tail.
    """
    blocks: list[_PrelimElement] = []
    for el in elements:
        if isinstance(el, str):
            if el.strip():
                blocks.append(_PrelimElement("paragraph", {}, [_PrelimText([(el, {})])]))
            continue
        if _is_real_element(el):
            blocks.append(_convert_block_element(el))
        tail = el.tail
        if tail and tail.strip():
            blocks.append(_PrelimElement("paragraph", {}, [_PrelimText([(tail, {})])]))
    return blocks


def _html_to_prelim_tree(raw_html: str) -> list[_PrelimElement]:
    """The off-doc, fallible half of the seed (Pattern 1): parses `raw_html`
    and returns a list of top-level prelim block nodes. Touches no `ydoc`;
    any exception here (malformed input, an lxml parse error) is caught by
    `seed_room`, which never lets it reach the doc-touching half.
    """
    return _convert_block_siblings(_parse_fragment(raw_html))


def _read_and_convert(doc_id: str) -> list[_PrelimElement] | None:
    """The whole off-doc, thread-safe half of the seed (WR-02): the SQLite
    read (`_read_content_html`) and the CPU-bound lxml parse/build
    (`_html_to_prelim_tree`), run together so `seed_room` can offload both
    to one worker thread via a single `asyncio.to_thread` call. Touches no
    pycrdt object -- safe to run on any thread.

    Returns None if the document row is gone (mirrors `seed_room`'s own
    no-op posture for that case); raises on a genuine conversion failure,
    which `seed_room` catches back on the event-loop thread the same way it
    always has.
    """
    raw_html = _read_content_html(doc_id)
    if raw_html is None:
        return None
    return _html_to_prelim_tree(raw_html)


def _materialize(container, node: "_PrelimElement | _PrelimText") -> None:
    """Integrate one off-doc prelim node into `container` (an already-
    integrated `XmlFragment` or `XmlElement`) -- the only doc-touching half
    (Pitfall 1). Recurses for `_PrelimElement.children`; a `_PrelimText`'s
    runs are applied via `.insert(index, text, attrs)` on the now-integrated
    `XmlText` leaf (marks require an integrated node), from the already-
    validated `(text, attrs)` pairs the off-doc build computed.
    """
    if isinstance(node, _PrelimText):
        text_node = container.children.append(XmlText())
        index = 0
        for text, attrs in node.runs:
            text_node.insert(index, text, attrs or None)
            index += len(text)
        return
    element = container.children.append(XmlElement(node.tag, node.attrs))
    for child in node.children:
        _materialize(element, child)


async def seed_room(doc_id: str, ydoc: Doc) -> None:
    """Seed `ydoc`'s "default" fragment from `doc_id`'s last-saved
    `content_html`, exactly once.

    No-ops if already seeded (D-09, also makes this idempotent -- D-18b) or
    if the document row is gone (deleted out from under a live room,
    mirroring `persist_snapshot_html`'s own no-op posture). On a conversion
    failure, logs and returns without ever touching `ydoc` (D-12): the
    fragment stays exactly empty and `config.seeded` stays false (D-20).
    On success, `config.seeded` flips true even for an empty `content_html`
    (D-10) -- every room that serves a client has been seeded.

    WR-01: `Transaction.__exit__` commits unconditionally even when an
    exception is raised inside the `with` block (module docstring), so an
    exception from `_materialize` mid-tree is not rolled back -- whatever
    was already appended before the raise stays committed (and, since this
    runs after `ydoc_observed`, gets persisted) while `config.seeded` never
    gets set (the raise happens before that line). Without the `len(...) ==
    0` guard below, the *next* `seed_room` call for this doc_id would see
    "not yet seeded," rehydrate that partial leftover, and materialize the
    *full* tree again on top of it -- duplicated content. Guarding on the
    fragment already being non-empty makes a retry a safe no-op-on-content
    (still flips `config.seeded` true, so the room isn't stuck retrying
    forever) instead of a duplicating one. This does not change the
    exception itself propagating out of `seed_room` on the failing call
    (still uncaught here by design, per this function's own materialize
    section never being wrapped in try/except) -- it only makes the
    following retry's behavior safe.

    WR-02: the DB read and lxml parse (`_read_and_convert`) are offloaded to
    a worker thread via `asyncio.to_thread` -- neither touches `ydoc`, so
    this is safe off the event-loop thread. Only the doc-mutating half below
    (the transaction/materialize/config-flip) stays on the thread that owns
    `ydoc`, since pycrdt objects are thread-affine.
    """
    if _already_seeded(ydoc):
        return
    try:
        built_children = await asyncio.to_thread(_read_and_convert, doc_id)
    except Exception:
        log.error("seed conversion failed for doc %r", doc_id, exc_info=True)
        return
    if built_children is None:  # document row gone (deleted out from under a live room)
        return
    with ydoc.transaction():
        frag = ydoc.get("default", type=XmlFragment)
        if len(frag.children) == 0:  # WR-01: skip re-materializing a partial leftover on retry
            for child in built_children:
                _materialize(frag, child)
        ydoc.get(CONFIG_MAP_NAME, type=Map)[SEEDED_KEY] = True  # D-10: flip only after a clean build
