# AI-Native Workflow Note

An honest account of how I used AI to build Scribe — where it helped, what it caught,
what I changed or rejected, and how I verified the result. The goal was leverage
without surrendering engineering judgment: AI did the mechanical work *and*, crucially,
ran adversarial verification that caught real bugs a human skimming code would likely
have shipped.

## Tools used

- **Claude Code** (Anthropic's agentic CLI) as the primary pair-programmer — design
  discussion, scaffolding, writing code and tests, and driving the app.
- **Context7 MCP** (live documentation) to pull *current* library docs instead of
  relying on training data — decisive here because TipTap 3 and Tailwind v4 both had
  breaking setup changes.
- **Playwright MCP** (real-browser automation) to drive the actual UI during user
  acceptance testing (UAT), not just assert against the DOM in a unit runner.
- **Two AI review agents run in parallel** during the UAT pass, reading the diff
  adversarially for security and integration defects.

## Where AI materially sped up the work

- **Boilerplate and wiring.** SQLAlchemy models, Pydantic schemas, the React Query data
  layer, Tailwind-styled components, the Dockerfile — the mechanical 60% of a full-stack
  app. It collapsed hours of typing into minutes.
- **Staying at design altitude.** I kept my attention on the decisions that matter (the
  sharing model, the storage format, the 404-vs-403 leak rule) while delegating the
  transcription of those decisions into code.
- **Test authoring.** The access-control cases — owner CRUD, viewer-can't-edit,
  editor-vs-viewer, the non-collaborator 404, upload sanitization — were fast to
  enumerate and encode, which made it cheap to *keep* the authorization logic honest.

## Version-specific issues Context7 caught (before they became bugs)

- **TipTap 3 StarterKit already bundles Underline.** My instinct was to add
  `@tiptap/extension-underline` alongside StarterKit. The current TipTap 3 docs (via
  Context7) showed StarterKit v3 already includes it — adding it separately would
  **double-register the extension and throw at runtime**. I dropped the extra dependency
  and instead disabled the StarterKit nodes I don't expose, so the editor schema matches
  the server's sanitizer allow-list exactly.
- **Tailwind v4 has a different setup than v3.** Older guidance (PostCSS +
  `tailwind.config.js` + `@tailwind` directives) is wrong for v4. I verified the v4 path
  (`@tailwindcss/vite` plugin + a single `@import "tailwindcss"`) against live docs and
  used that, rejecting the stale instructions.

## The bug a real browser caught that unit tests structurally could not

This is the clearest example of AI doing verification a human review would miss.

**Symptom (found in Playwright UAT):** every formatting toolbar button did **nothing**
when clicked. Bold, Italic, Underline, H1/H2, lists — all inert.

**Root cause:** clicking a button fires `mousedown`, which moved focus out of the
`contenteditable` and **collapsed the editor's text selection before the `onClick`
command ran** — so the formatting command applied to an empty selection.

**Isolation evidence:** keyboard shortcuts (Ctrl+B) worked while button clicks didn't.
That single asymmetry pinpointed focus/selection loss on mousedown as the cause, not the
commands themselves. A unit test that calls the command directly would pass — the bug
only exists in the real focus model of a live browser.

**Fix:** `onMouseDown` `preventDefault()` on each button so focus stays in the editor
(and hoisted the button component out of the render body). Re-verified in-browser that
Bold / Italic / Underline / H1 / lists then applied and the active-state highlighting
lit up correctly. The fix and the reason are commented at
`frontend/src/components/Toolbar.tsx`.

## Two parallel review agents, two more real bugs

Running two AI review passes in parallel during UAT surfaced two defects of the classic
"state / integration bug unit tests miss" kind — both fixed before shipping:

- **Path-traversal in the SPA static-file fallback.** FastAPI serves the built SPA under
  a catch-all route; a crafted `/../../` path could escape the `dist` directory and read
  arbitrary files. Fixed with a `realpath` containment check that serves a file only if
  the resolved path stays inside `dist`, else falls back to `index.html`
  (`backend/app/main.py`).
- **Stale-cache reopen bug.** After autosave, React Query still held the *pre-edit*
  document. Navigating away and reopening the doc in-session showed stale content, and
  the next edit could **overwrite saved work**. Fixed by writing each save response back
  into the query cache (`queryClient.setQueryData` in `frontend/src/pages/EditorPage.tsx`,
  fed by the autosave hook's `onSaved` callback).

## What I changed or rejected

AI output is a strong first draft, not a finished answer. Concrete interventions:

- **Dropped the redundant Underline dependency** (see Context7 note above).
- **Rejected the Tailwind v3 setup** in favor of the verified v4 path.
- **Made "no access" return 404, not 403**, so the API never leaks document existence —
  and backed it with a test.
- **Made re-sharing an upsert.** The first pass treated re-sharing an existing user as an
  error; I changed it to update the role and return 200 (the behavior a user expects) and
  made **self-sharing** the explicit 400 case instead.
- **Declined a server-side PDF renderer** (WeasyPrint + native libs) in favor of
  client-side print-to-PDF, keeping the image lean for a feature the browser handles.

## How I verified correctness, UX, and reliability

I did not take "the code looks right" as done. Verification was layered:

1. **Automated tests — 16 backend + 3 frontend, all green**, plus a clean `tsc --noEmit`.
   Backend tests hit the real API through FastAPI's TestClient against a temp database and
   assert the access-control matrix (incl. 404-not-403 and viewer-vs-editor) and upload
   sanitization (including that an injected `<script>` is stripped). Frontend tests cover
   the permission logic as pure functions.
2. **A boot-path smoke test.** Tests skip the app lifespan, so I separately booted the
   real app to confirm the startup **seed** runs and produces the intended
   owner / editor / viewer relationships (Alice owns, Bob edits, Carol views the roadmap).
3. **A full browser UAT across all three roles.** Using Playwright I exercised login, the
   dashboard's owned-vs-shared split, rich-text editing, autosave, **persistence across a
   full page reload** (proving DB persistence, not just local state), sharing add / revoke,
   viewer read-only enforcement (disabled title, no toolbar, no Share), and `.md`
   upload → converted document.
4. **The reviewer's exact command.** I ran `docker compose up --build` against a clean
   container and verified health, login, seeded owned + shared documents, and SPA serving
   all worked — because "works on my machine" isn't a deliverable.

## Honest assessment of AI's role

AI made me faster **and** more thorough, not more careless. It accelerated the mechanical
work where speed is pure upside, and — more importantly — it ran adversarial verification
(a real-browser UAT plus parallel security/integration review) that caught three bugs a
human skimming the diff would likely have shipped: the dead toolbar, a path-traversal
hole, and a data-loss stale-cache. The judgment that determines whether this project is
any good stayed mine: the scope cuts, the access-control model, the 404-vs-403 leak rule,
and the storage-format decision. Every automated test in this repo passes, and I watched
the core flows work in a real browser and a clean container before calling anything done.
