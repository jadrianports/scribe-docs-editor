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
"""

import html as html_lib

from pycrdt import Doc, XmlElement, XmlFragment, XmlText

# ProseMirror heading level -> HTML tag. Only 1-3 are in the editor schema
# (frontend caps `StarterKit.configure({ heading: { levels: [1, 2, 3] } })`)
# and content.ALLOWED_TAGS only allows h1-h3; an out-of-range level falls
# back to h1 rather than emit a tag sanitize_html would strip (which would
# silently unwrap the heading's text instead of just clamping its level).
_HEADING_TAGS = {1: "h1", 2: "h2", 3: "h3"}

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
    inner = "".join(_node_html(child) for child in node.children)
    if node.tag == "heading":
        level = int(dict(node.attributes).get("level", 1))
        tag = _HEADING_TAGS.get(level, "h1")
        return f"<{tag}>{inner}</{tag}>"
    if node.tag == "hardBreak":
        return "<br>"
    tag = _BLOCK_TAGS.get(node.tag, "p")
    return f"<{tag}>{inner}</{tag}>"
