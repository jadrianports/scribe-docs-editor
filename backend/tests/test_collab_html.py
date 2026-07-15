"""Tests for the pure Y.XmlFragment -> HTML walk.

Construction mirrors the real shape TipTap's Collaboration extension produces
(confirmed empirically against `data/yjs.db` real editing history and against
`@tiptap/y-tiptap`'s `marksToAttributes()` source -- see app/collab/html.py's
module docstring for the full finding): marks are Yjs-formatted text ranges
via `XmlText.insert(index, text, attrs={...})` / exposed via `.diff()`, with
each active mark's attrs value being `{}` (empty dict) -- NOT `XmlText`
element-level `.attributes`, and NOT a boolean.
"""

from pycrdt import Doc, XmlElement, XmlFragment, XmlText

from app.collab.html import ydoc_to_html
from app.content import sanitize_html


def _fragment(doc: Doc) -> XmlFragment:
    return doc.get("default", type=XmlFragment)


def _paragraph(frag: XmlFragment, text: str) -> None:
    para = XmlElement("paragraph")
    frag.children.append(para)
    para.children.append(XmlText(text))


def test_paragraph_roundtrips():
    doc = Doc()
    frag = _fragment(doc)
    _paragraph(frag, "Hello world")

    assert ydoc_to_html(doc) == "<p>Hello world</p>"


def test_marks_render_as_fixed_order_nested_tags_and_are_sanitizer_stable():
    doc = Doc()
    frag = _fragment(doc)
    para = XmlElement("paragraph")
    frag.children.append(para)
    text = XmlText()
    para.children.append(text)

    # Mirrors the real attrs shape y-tiptap sends for parameterless marks:
    # `{markname: {}}`, not `{markname: True}`.
    text.insert(0, "plain ", None)
    text.insert(len(text), "bold", {"bold": {}})
    text.insert(len(text), " ", None)
    text.insert(len(text), "combo", {"bold": {}, "italic": {}, "underline": {}, "strike": {}})

    html = ydoc_to_html(doc)

    assert html == (
        "<p>plain <strong>bold</strong> "
        "<strong><em><u><s>combo</s></u></em></strong></p>"
    )
    assert sanitize_html(html) == html


def test_heading_level_and_lists():
    doc = Doc()
    frag = _fragment(doc)

    heading = XmlElement("heading", {"level": 2})
    frag.children.append(heading)
    heading.children.append(XmlText("Section"))

    bullet_list = XmlElement("bulletList")
    frag.children.append(bullet_list)
    for item_text in ["First", "Second"]:
        item = XmlElement("listItem")
        bullet_list.children.append(item)
        item_para = XmlElement("paragraph")
        item.children.append(item_para)
        item_para.children.append(XmlText(item_text))

    ordered_list = XmlElement("orderedList", {"start": 1})
    frag.children.append(ordered_list)
    item = XmlElement("listItem")
    ordered_list.children.append(item)
    item_para = XmlElement("paragraph")
    item.children.append(item_para)
    item_para.children.append(XmlText("Only"))

    html = ydoc_to_html(doc)

    # Note: no `start="1"` on <ol> -- content.ALLOWED_ATTRIBUTES is {}, so any
    # attribute we emitted would be stripped by sanitize_html and break
    # sanitizer-stability. The walk must never emit attributes at all.
    assert html == (
        "<h2>Section</h2>"
        "<ul><li><p>First</p></li><li><p>Second</p></li></ul>"
        "<ol><li><p>Only</p></li></ol>"
    )
    assert sanitize_html(html) == html


def test_heading_level_outside_schema_falls_back_safely():
    # Defensive: h1-h3 are the only headings in content.ALLOWED_TAGS. If a
    # level value ever drifted outside that (bug upstream, or a future
    # schema change), emitting <h4> etc. verbatim would get its tag stripped
    # by sanitize_html while its text content survives unwrapped -- silently
    # violating sanitizer-stability. The walk must never produce a tag
    # outside the allow-list.
    doc = Doc()
    frag = _fragment(doc)
    heading = XmlElement("heading", {"level": 9})
    frag.children.append(heading)
    heading.children.append(XmlText("Odd"))

    html = ydoc_to_html(doc)

    assert sanitize_html(html) == html


def test_blockquote_and_hard_break():
    doc = Doc()
    frag = _fragment(doc)
    quote = XmlElement("blockquote")
    frag.children.append(quote)
    para = XmlElement("paragraph")
    quote.children.append(para)
    para.children.append(XmlText("line one"))
    para.children.append(XmlElement("hardBreak"))
    para.children.append(XmlText("line two"))

    html = ydoc_to_html(doc)

    assert html == "<blockquote><p>line one<br>line two</p></blockquote>"
    assert sanitize_html(html) == html


def test_empty_paragraph_roundtrips():
    doc = Doc()
    frag = _fragment(doc)
    frag.children.append(XmlElement("paragraph"))

    html = ydoc_to_html(doc)

    assert html == "<p></p>"
    assert sanitize_html(html) == html


def test_injection_attempt_is_escaped_and_sanitizer_stable():
    doc = Doc()
    frag = _fragment(doc)
    _paragraph(frag, '<script>alert(1)</script> & "quoted" \'text\'')

    html = ydoc_to_html(doc)

    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert sanitize_html(html) == html


def test_heading_level_non_numeric_falls_back_to_h1():
    # The WS channel forwards raw Yjs bytes with no ProseMirror-schema
    # validation, so `level` can be any JSON-compatible value a client
    # writes -- e.g. a string. `int("abc")` raises ValueError; ydoc_to_html
    # must never propagate that (Task 7 calls it to build snapshots, so a
    # crash here breaks that document's snapshot).
    doc = Doc()
    frag = _fragment(doc)
    heading = XmlElement("heading", {"level": "abc"})
    frag.children.append(heading)
    heading.children.append(XmlText("Odd"))

    html = ydoc_to_html(doc)

    assert html == "<h1>Odd</h1>"
    assert sanitize_html(html) == html


def test_heading_level_none_falls_back_to_h1():
    # Same hazard, the other exception shape: `int(None)` raises TypeError
    # (not ValueError) -- must be caught too.
    doc = Doc()
    frag = _fragment(doc)
    heading = XmlElement("heading", {"level": None})
    frag.children.append(heading)
    heading.children.append(XmlText("Odd"))

    html = ydoc_to_html(doc)

    assert html == "<h1>Odd</h1>"
    assert sanitize_html(html) == html


def test_unmapped_node_misplaced_inside_paragraph_is_sanitizer_stable():
    # Reviewer repro (out-of-schema tree, no ProseMirror validation on the
    # WS channel): an unmapped node type -- falls back to <p> -- nested
    # inside a paragraph. The old uniform walk produced
    # "<p>before <p>@bob</p> after</p>": bleach's html5lib parser auto-
    # closes an open <p> when it meets a nested <p> start tag, so
    # sanitize_html(raw) came back as
    # "<p>before </p><p>@bob</p> after<p></p>" -- different from raw.
    # A block-shaped child must never be nested inside a <p>/<hN> wrapper.
    doc = Doc()
    frag = _fragment(doc)
    para = XmlElement("paragraph")
    frag.children.append(para)
    para.children.append(XmlText("before "))
    mention = XmlElement("mention")  # not in Scribe's schema -- unmapped
    para.children.append(mention)
    mention.children.append(XmlText("@bob"))
    para.children.append(XmlText(" after"))

    html = ydoc_to_html(doc)

    assert html == "<p>before </p><p>@bob</p><p> after</p>"
    assert sanitize_html(html) == html


def test_mapped_block_misplaced_inside_paragraph_is_sanitizer_stable():
    # Reviewer repro: a *mapped* block (bulletList -> <ul>) nested inside a
    # paragraph. Old output "<p>intro <ul><li><p>item</p></li></ul></p>"
    # was unstable for the same reason (nested <ul> forces the parser to
    # close the outer <p> first).
    doc = Doc()
    frag = _fragment(doc)
    para = XmlElement("paragraph")
    frag.children.append(para)
    para.children.append(XmlText("intro "))
    bullet_list = XmlElement("bulletList")
    para.children.append(bullet_list)
    item = XmlElement("listItem")
    bullet_list.children.append(item)
    item_para = XmlElement("paragraph")
    item.children.append(item_para)
    item_para.children.append(XmlText("item"))

    html = ydoc_to_html(doc)

    assert html == "<p>intro </p><ul><li><p>item</p></li></ul>"
    assert sanitize_html(html) == html


def test_heading_level_infinite_falls_back_to_h1():
    # A third exception shape, on top of the non-numeric-string/None cases
    # above: `int(float("inf"))` raises OverflowError, which the earlier
    # `except (TypeError, ValueError)` fix does not cover. Confirmed
    # empirically (`int(raw)` where `raw = dict(node.attributes)["level"]`
    # is the float `inf`, not a string) before writing this test. Task 7
    # calls ydoc_to_html to build room-empty snapshots -- a crash here would
    # break that document's snapshot, so this must fall back to h1 same as
    # the other two malformed-level shapes.
    doc = Doc()
    frag = _fragment(doc)
    heading = XmlElement("heading", {"level": float("inf")})
    frag.children.append(heading)
    heading.children.append(XmlText("Odd"))

    html = ydoc_to_html(doc)

    assert html == "<h1>Odd</h1>"
    assert sanitize_html(html) == html


def test_heading_misplaced_inside_heading_is_sanitizer_stable():
    # Same inline-context hazard, the heading half: html5lib pops an open
    # heading when it meets ANOTHER heading start tag (empirically verified
    # -- unlike <p>, a heading tolerates other block content nested inside
    # it without restructuring, but not a nested heading), so a
    # heading-inside-heading is the one heading shape that's unstable
    # pre-fix: "<h1>outer <h2>inner</h2> tail</h1>" sanitizes to
    # "<h1>outer </h1><h2>inner</h2> tail" (bare trailing text, dropped
    # wrapper) -- different from raw.
    doc = Doc()
    frag = _fragment(doc)
    outer = XmlElement("heading", {"level": 1})
    frag.children.append(outer)
    outer.children.append(XmlText("outer "))
    inner = XmlElement("heading", {"level": 2})
    outer.children.append(inner)
    inner.children.append(XmlText("inner"))
    outer.children.append(XmlText(" tail"))

    html = ydoc_to_html(doc)

    assert html == "<h1>outer </h1><h2>inner</h2><h1> tail</h1>"
    assert sanitize_html(html) == html
