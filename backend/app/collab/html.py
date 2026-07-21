"""Pure Y.XmlFragment -> HTML walk for TipTap-authored collaborative documents.

Converts the shared CRDT document tree (`ydoc.get("default", type=XmlFragment)`,
the fragment TipTap's Collaboration extension binds to the ProseMirror doc) into
raw HTML matching Scribe's editor schema. The caller runs the result through
`app.content.sanitize_html` before storing/serving it (this module has no I/O
of its own and does not sanitize); the contract here is that the output is
*already* stable under that sanitizer -- nothing this walk emits is stripped
or altered by `content.ALLOWED_TAGS` / `content.ALLOWED_ATTRIBUTES`.

Mark representation -- empirically confirmed, not assumed
-----------------------------------------------------------
This was the one open question flagged for this task: does pycrdt's XmlText
expose TipTap marks (bold/italic/underline/strike) as per-node attributes, or
as Yjs formatted ranges within the text? Confirmed **formatted ranges**, via
two independent checks:

1. Source of `@tiptap/y-tiptap` (the library backing `@tiptap/extension-
   collaboration`, `frontend/node_modules/@tiptap/y-tiptap/dist/y-tiptap.cjs`,
   `marksToAttributes()` ~line 1327): for each active ProseMirror mark it sets
   `pattrs[mark.type.name] = mark.attrs` and applies that via
   `ytext.applyDelta([{retain, attributes}, ...])` -- i.e. marks are written
   as Yjs Text/XmlText delta-formatted runs, keyed by plain mark name (no hash
   suffix, since these marks are mutually self-exclusive, not "overlapping"
   in y-tiptap's sense). Critically, the *value* is `mark.attrs`, which for a
   parameterless mark (Bold/Italic/Underline/Strike all take no options) is
   `{}` -- an empty dict, not `True`.
2. Loading real editing history from `data/yjs.db` (`ScribeYStore`) plus a
   synthetic round-trip through pycrdt 0.14.1's actual encode/`apply_update`
   confirmed how this surfaces on the Python side: `XmlText.attributes` is
   always empty (that view is for XML-attribute-style metadata, e.g.
   `heading`'s `level`, unrelated to character formatting) -- the per-run
   marks only show up via `XmlText.diff()`, which returns
   `[(text_chunk, attrs | None), ...]`, `attrs` being e.g.
   `{"bold": {}, "italic": {}}` for a run with both marks active.

Two consequences that would otherwise silently drop every mark:
  - Detect marks by **key presence** in a chunk's attrs (`name in attrs`),
    never by truthiness of the value -- `bool({})` is `False` in Python, so
    `attrs.get("bold")` is falsy even when bold *is* active.
  - Yjs's attrs map has no stable iteration order (verified: it changed
    across an encode/decode round-trip of the same content in-process), so
    multi-mark nesting order on one run is driven by our own fixed
    `_MARK_ORDER`, never by iterating the attrs dict itself.

Sanitizer-stability for out-of-schema trees
--------------------------------------------
The WS channel forwards raw Yjs update bytes with no ProseMirror-schema
validation, so the fragment can contain shapes a TipTap client would never
produce -- e.g. a block node (mapped or unmapped) nested inside a
`paragraph`/`heading`. A uniform recursive walk that always nests a child's
rendered HTML inside its parent's wrapper tag breaks stability for exactly
that case: `<p>`/`<hN>` have a phrasing-content-only model, and bleach's
HTML parser (html5lib) enforces part of that at parse time by auto-closing
an open `<p>` (or heading, on a *nested heading* start tag specifically --
verified empirically that a heading otherwise tolerates non-heading block
content nested inside it) as soon as it meets a nested block-starting tag.
So e.g. `<p>before <p>@bob</p> after</p>` (an unmapped node, which falls
back to `<p>`, misplaced inside a paragraph) re-parses+serializes to
`<p>before </p><p>@bob</p> after<p></p>` -- different from the input.

Fix: `_element_html` resolves each element's own output tag first, and if
that tag is phrasing-content-only (`p`/`h1`/`h2`/`h3` -- `_PHRASING_ONLY_
TAGS`), its children are rendered by `_wrapped_inline_html`, which walks
them looking for anything that isn't inline-safe (plain text or a
`hardBreak`). Inline-safe content is buffered and wrapped normally; a
block-shaped child is never nested inside the wrapper -- it's hoisted out
as its own sibling, splitting the buffered inline content into multiple
same-tag wrappers around it (`<p>before </p><p>@bob</p><p> after</p>`)
instead. This keys off the *resolved output tag*, not the source node's
own tag name, so it uniformly covers mapped blocks (`bulletList` -> `ul`),
unmapped nodes (fall back to `p`), and nested headings alike, at any
recursion depth -- verified against bleach directly for every shape this
produces (see `backend/tests/test_collab_html.py`).

Every other wrapper (`blockquote`, `li`, `ul`, `ol`, and the fragment root
itself) has a flow-content model that tolerates nested block content
without the parser restructuring it (also verified directly against
bleach), so their children keep the original simple concatenation --
no hoisting needed there.
"""

import html as html_lib

from pycrdt import Doc, XmlElement, XmlFragment, XmlText

# Shared surface (backend/app/collab/seeding.py imports and inverts the five
# tables below -- D-07): renaming, removing, or changing the shape of any
# table in this block without updating seeding.py's reverse maps
# (_TAG_TO_BLOCK, _LEVEL_TO_HEADING) silently breaks the server-side seed
# path. They stay defined here, at module level, under these exact names --
# D-07 forbids moving them out of html.py; seeding.py does a plain
# cross-module import, not a re-export. This is a documentation-contract
# only: nothing below this comment changes ydoc_to_html's behavior.

# ProseMirror heading level -> HTML tag. Only 1-3 are in the editor schema
# (frontend caps `StarterKit.configure({ heading: { levels: [1, 2, 3] } })`)
# and content.ALLOWED_TAGS only allows h1-h3; an out-of-range OR non-numeric
# OR missing/None level all fall back to h1 (`_heading_level` returns None
# for anything `int()` can't parse, and `.get(None, "h1")` below misses the
# dict same as an out-of-range int does) rather than emit a tag
# sanitize_html would strip (which would silently unwrap the heading's text
# instead of just clamping its level) or raise out of ydoc_to_html entirely.
_HEADING_TAGS = {1: "h1", 2: "h2", 3: "h3"}

# Output tags with a phrasing-content-only model: nesting a block-shaped
# child inside one of these is the specific shape bleach's HTML parser
# restructures on sanitization. See the module docstring's
# "Sanitizer-stability for out-of-schema trees" section.
_PHRASING_ONLY_TAGS = {"p", "h1", "h2", "h3"}

# ProseMirror block node name -> HTML tag, aligned with content.ALLOWED_TAGS.
# `heading` and `hardBreak` are handled separately (level lookup / void tag).
_BLOCK_TAGS = {
    "paragraph": "p",
    "bulletList": "ul",
    "orderedList": "ol",
    "listItem": "li",
    "blockquote": "blockquote",
}

# Outer -> inner nesting order applied to stacked marks on one text run.
# The order itself is an arbitrary (but fixed) choice -- these are
# independent inline styles, so <strong><em>x</em></strong> and
# <em><strong>x</strong></em> render identically -- what matters is that it
# is deterministic and does not depend on Yjs's unordered attrs map.
_MARK_ORDER = ["bold", "italic", "underline", "strike"]
_MARK_TAGS = {"bold": "strong", "italic": "em", "underline": "u", "strike": "s"}


def ydoc_to_html(ydoc: Doc) -> str:
    """Render the shared ProseMirror doc fragment as raw (unsanitized) HTML.

    Pure function: reads `ydoc`, performs no I/O, and does not mutate it.
    """
    frag = ydoc.get("default", type=XmlFragment)
    return "".join(_node_html(child) for child in frag.children)


def _node_html(node: object) -> str:
    if isinstance(node, XmlText):
        return _text_html(node)
    if isinstance(node, XmlElement):
        return _element_html(node)
    return ""  # unrecognized node kind (e.g. an XmlFragment subdoc) -- drop, don't crash


def _text_html(node: XmlText) -> str:
    """Render one text node's formatted runs (see module docstring)."""
    return "".join(_run_html(text, attrs) for text, attrs in node.diff())


def _run_html(text: object, attrs: dict | None) -> str:
    escaped = html_lib.escape(str(text))
    active = attrs or {}
    for mark in reversed(_MARK_ORDER):
        if mark in active:  # key presence, not truthiness -- see module docstring
            tag = _MARK_TAGS[mark]
            escaped = f"<{tag}>{escaped}</{tag}>"
    return escaped


def _element_html(node: XmlElement) -> str:
    if node.tag == "hardBreak":
        return "<br>"
    if node.tag == "heading":
        tag = _HEADING_TAGS.get(_heading_level(node), "h1")
    else:
        tag = _BLOCK_TAGS.get(node.tag, "p")
    if tag in _PHRASING_ONLY_TAGS:
        return _wrapped_inline_html(node.children, tag)
    inner = "".join(_node_html(child) for child in node.children)
    return f"<{tag}>{inner}</{tag}>"


def _heading_level(node: XmlElement) -> int | None:
    """Parsed `level` attribute, or None if it's missing, non-numeric, or
    otherwise un-parseable. Never raises: the WS channel forwards raw Yjs
    bytes with no ProseMirror-schema validation, so `level` can be any
    JSON-compatible value a client writes (a string, None, ...), and
    Task 7 calls ydoc_to_html to build snapshots -- a crash here must never
    propagate. Callers look up the result in `_HEADING_TAGS`, which falls
    back to h1 for None same as it does for an out-of-range int.

    Catches `Exception` broadly rather than enumerating types one at a time:
    `int(raw)` was first confirmed to raise `ValueError` (non-numeric string)
    and `TypeError` (None); Task 7 found a third, distinct shape empirically
    -- `int(float("inf"))` raises `OverflowError`, which neither of those
    covers. Given the "Never raises" contract above and that every malformed
    `level` found so far has raised its own exception type, catching broadly
    here is more robust than continuing to whack-a-mole individual exception
    classes as new malformed inputs surface.
    """
    raw = dict(node.attributes).get("level", 1)
    try:
        return int(raw)
    except Exception:
        return None


def _is_inline_safe(node: object) -> bool:
    """True if `node` can be nested directly inside a <p>/<hN> wrapper
    without landing inside the one shape bleach's HTML parser restructures
    on sanitization. Only plain text and `hardBreak` render as inline/void
    output; every other element renders as a block tag (see module
    docstring) and must not be nested in a phrasing-only wrapper.
    """
    if isinstance(node, XmlText):
        return True
    return isinstance(node, XmlElement) and node.tag == "hardBreak"


def _wrapped_inline_html(children, tag: str) -> str:
    """Render `children` as the content of a <tag>...</tag> wrapper (`tag`
    is always one of `_PHRASING_ONLY_TAGS`), hoisting any block-shaped
    child out as its own top-level sibling instead of nesting it inside
    `tag` -- see the module docstring's "Sanitizer-stability for
    out-of-schema trees" section for why that nesting must never happen.

    Buffered inline content is split into multiple `<tag>...</tag>` runs
    around each hoisted block, e.g. a paragraph containing
    text/block/text renders as `<p>text</p>{block}<p>text</p>` rather than
    `<p>text{block}text</p>`. A run is only emitted if it has content, or
    if there were no block children at all (preserving a single empty
    `<tag></tag>` for a genuinely childless paragraph/heading).
    """
    pieces: list[str] = []
    buffer = ""
    saw_block = False
    for child in children:
        if _is_inline_safe(child):
            buffer += _node_html(child)
            continue
        saw_block = True
        if buffer:
            pieces.append(f"<{tag}>{buffer}</{tag}>")
            buffer = ""
        pieces.append(_node_html(child))
    if buffer or not saw_block:
        pieces.append(f"<{tag}>{buffer}</{tag}>")
    return "".join(pieces)
