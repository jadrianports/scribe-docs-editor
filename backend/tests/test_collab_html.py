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
