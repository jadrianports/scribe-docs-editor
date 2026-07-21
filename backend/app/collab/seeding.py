"""Server-side HTML -> Y.Doc seed converter and orchestrator (D-06).

Owns the whole seeding concern for a room: the should-we-seed gate, the DB
read of the document's last-saved `content_html`, the HTML->CRDT conversion,
and the `config.seeded` write. `rooms.py`'s `_create_room` calls
`seed_room(doc_id, ydoc)` once, after `ydoc_observed.wait()` and before the
dirty-subscription is registered (D-08, plan 10-05) -- under the same
per-doc_id lock that makes this exactly-once by construction.

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

import logging
from dataclasses import dataclass, field

from lxml import html as lxml_html
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
    `no_leading_text=True` raises on genuine top-level leading text --
    `content_html` (bleach-sanitized, always block-wrapped) should never
    start with bare text, so that shape is a parse failure worth surfacing
    (caught by `seed_room`'s try/except) rather than silently swallowing.
    """
    return lxml_html.fragments_fromstring(raw_html, no_leading_text=True)


def _is_block_tag(tag: str) -> bool:
    """True for html tags that become their own nested prelim block element
    (paragraph/heading/list/blockquote) when walked. Deliberately excludes
    `br`: hardBreak is inline-safe in html.py's own model (`_is_inline_safe`)
    despite being its own `XmlElement` leaf, and is dispatched by its own
    branch in `_walk_children`, not this check.
    """
    return tag in _HEADING_HTML_TAGS or tag in _BLOCK_HTML_TAGS


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


def _convert_block_element(el) -> _PrelimElement:
    """Convert one block-level lxml element to a prelim block node -- the
    inverse of html.py's `_element_html` (D-01/D-03)."""
    if el.tag == "br":
        return _PrelimElement("hardBreak", {})
    if el.tag in _HEADING_HTML_TAGS:
        level = _LEVEL_TO_HEADING.get(el.tag, 1)
        return _PrelimElement("heading", {"level": level}, _walk_children(el))
    node_tag = _TAG_TO_BLOCK.get(el.tag, "paragraph")  # D-03: unknown element -> paragraph
    return _PrelimElement(node_tag, {}, _walk_children(el))


def _convert_block_siblings(elements) -> list[_PrelimElement]:
    """Convert the top-level sibling elements `lxml.html.fragments_fromstring`
    returns into prelim block nodes, applying D-05's whitespace-only-tail
    drop between top-level siblings; genuine (non-whitespace) orphaned text
    falls back to its own paragraph (D-03), never raising.
    """
    blocks: list[_PrelimElement] = []
    for el in elements:
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


def seed_room(doc_id: str, ydoc: Doc) -> None:
    """Seed `ydoc`'s "default" fragment from `doc_id`'s last-saved
    `content_html`, exactly once.

    No-ops if already seeded (D-09, also makes this idempotent -- D-18b) or
    if the document row is gone (deleted out from under a live room,
    mirroring `persist_snapshot_html`'s own no-op posture). On a conversion
    failure, logs and returns without ever touching `ydoc` (D-12): the
    fragment stays exactly empty and `config.seeded` stays false (D-20).
    On success, `config.seeded` flips true even for an empty `content_html`
    (D-10) -- every room that serves a client has been seeded.
    """
    if _already_seeded(ydoc):
        return
    raw_html = _read_content_html(doc_id)
    if raw_html is None:
        return
    try:
        built_children = _html_to_prelim_tree(raw_html)
    except Exception:
        log.error("seed conversion failed for doc %r", doc_id, exc_info=True)
        return
    with ydoc.transaction():
        frag = ydoc.get("default", type=XmlFragment)
        for child in built_children:
            _materialize(frag, child)
        ydoc.get(CONFIG_MAP_NAME, type=Map)[SEEDED_KEY] = True  # D-10: flip only after a clean build
