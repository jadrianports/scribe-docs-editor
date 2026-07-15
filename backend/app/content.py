"""Content conversion + sanitization.

Canonical stored format is sanitized HTML. Everything written to a document
(edits and uploads) passes through `sanitize_html`, so injected markup such as
<script> can never be stored or served.
"""

import html as html_lib

import bleach
import markdown as markdown_lib
from markdownify import markdownify

# Allow-list matches the TipTap editor schema (StarterKit + Underline).
ALLOWED_TAGS = ["p", "br", "strong", "em", "u", "s", "h1", "h2", "h3", "ul", "ol", "li", "blockquote"]
ALLOWED_ATTRIBUTES: dict = {}

ALLOWED_EXTENSIONS = {".txt", ".md"}
MAX_UPLOAD_BYTES = 1_000_000  # 1 MB


def sanitize_html(raw_html: str) -> str:
    return bleach.clean(raw_html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES, strip=True)


def txt_to_html(text: str) -> str:
    """Turn plain text into paragraphs, preserving intra-block line breaks."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = [block.strip() for block in normalized.split("\n\n")]
    paragraphs = []
    for block in blocks:
        if not block:
            continue
        escaped = html_lib.escape(block).replace("\n", "<br>")
        paragraphs.append(f"<p>{escaped}</p>")
    return "".join(paragraphs) or "<p></p>"


def md_to_html(text: str) -> str:
    rendered = markdown_lib.markdown(text, extensions=["extra", "sane_lists"])
    return sanitize_html(rendered)


def html_to_markdown(raw_html: str) -> str:
    return markdownify(raw_html, heading_style="ATX").strip()
