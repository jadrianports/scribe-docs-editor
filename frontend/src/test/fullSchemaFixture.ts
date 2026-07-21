/**
 * Full-schema input HTML mirroring `backend/tests/test_collab_html.py:259`
 * (`test_full_schema_roundtrip_is_sanitizer_stable`), the cross-language
 * counterpart this fixture mirrors (D-12, comment convention matches D-03).
 *
 * `FULL_SCHEMA_HTML` is fed as INPUT to `editor.commands.setContent(...)` in
 * the seed tests. It exercises every one of the 13 tags in
 * `backend/app/content.py:15`'s `ALLOWED_TAGS`
 * (p, br, strong, em, u, s, h1, h2, h3, ul, ol, li, blockquote) --
 * explicitly INCLUDING `s` (strike) and `blockquote`, which have no Toolbar
 * button and arrive only via Markdown upload/paste, making them the most
 * likely to be silently dropped by an accidental StarterKit reconfigure.
 *
 * IMPORTANT -- vocabulary differs on the way out: the seed assertion checks
 * `ydoc.getXmlFragment(...)` node NAMES (heading level=1, bold, italic,
 * bulletList/listItem, hardBreak, etc.), not the HTML tag strings this
 * fixture is written in. Do not assert against `editor.getHTML()` output
 * either -- StarterKit's `TrailingNode` extension appends a trailing empty
 * `<p></p>` to `getHTML()`'s return value, so a fragment-node-name assertion
 * is the only stable target (D-05/D-12).
 */
export const FULL_SCHEMA_HTML = `
  <p>plain <strong>bold</strong> <em>italic</em> <u>underline</u> <s>strike</s> <strong><em><u><s>combo</s></u></em></strong><br>after break</p>
  <h1>Heading 1</h1>
  <h2>Heading 2</h2>
  <h3>Heading 3</h3>
  <ul><li><p>First</p></li><li><p>Second</p></li></ul>
  <ol><li><p>Ordered</p></li></ol>
  <blockquote><p>Quoted</p></blockquote>
`
