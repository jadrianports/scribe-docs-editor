# AI-Native Workflow Note

This is an honest account of how I used AI to build Scribe — where it helped, what
I changed or rejected, and how I verified the result. The goal was leverage without
surrendering engineering judgment.

## Tools used

- **Claude Code** (Anthropic's agentic CLI) as the primary pair-programmer — used
  for design discussion, scaffolding, writing code and tests, and driving the app.
- **Context7** (live documentation MCP) to pull *current* library docs instead of
  relying on the model's training data — important because several libraries here
  (TipTap 3, Tailwind 4) had breaking setup changes.
- **Playwright** (browser automation) to drive the real UI during verification.

## Where AI materially sped up the work

- **Boilerplate and wiring.** SQLAlchemy models, Pydantic schemas, the React Query
  data layer, Tailwind-styled components, the Dockerfile — the mechanical 60% of a
  full-stack app. This is where an assistant earns its keep, and it collapsed hours
  of typing into minutes.
- **Staying at the design altitude.** I could keep my attention on the decisions
  that mattered (the sharing model, the storage format, the 404-vs-403 leak rule)
  while delegating the transcription of those decisions into code.
- **Test authoring.** The access-control tests — owner CRUD, viewer-can't-edit,
  editor-can't-delete, the non-collaborator 404 — were fast to enumerate and encode,
  which made it cheap to *keep* the authorization logic honest.

## What I changed or rejected

AI output is a strong first draft, not a finished answer. Concrete interventions:

- **Caught a version-specific bug before it happened.** My instinct was to add
  `@tiptap/extension-underline` alongside StarterKit. Checking the current TipTap 3
  docs via Context7 showed StarterKit v3 **already bundles Underline** (and Link),
  so adding it separately would double-register the extension and throw at runtime.
  I dropped the extra dependency and instead *disabled* the StarterKit nodes I don't
  expose, so the editor's schema matches the server's sanitizer allow-list exactly.
- **Rejected stale setup guidance.** Older Tailwind instructions (PostCSS +
  `tailwind.config.js` + `@tailwind` directives) are wrong for v4. I verified the v4
  path (`@tailwindcss/vite` plugin + a single `@import "tailwindcss"`) against live
  docs and used that instead.
- **Tightened the sharing contract.** The first pass treated re-sharing an existing
  user as an error. I changed it to an **upsert** (re-sharing updates the role and
  returns 200) because that's the behavior a user actually expects, and made
  self-sharing the explicit 400 case instead.
- **Made "no access" return 404, not 403.** A naive implementation leaks document
  existence by returning 403 for docs you're not on. I specified the non-leaking
  behavior and backed it with a test.
- **Trimmed dependencies.** I declined a server-side PDF renderer (WeasyPrint and
  its native libraries) in favor of client-side print-to-PDF, keeping the image
  lean for a feature the browser already handles.

## How I verified correctness, UX, and reliability

I did not take "the code looks right" as done. Verification was layered:

1. **Automated tests (12 backend + 3 frontend), all green.** The backend tests hit
   the real API through FastAPI's TestClient against a temp database and assert the
   access-control matrix and upload sanitization (including that an injected
   `<script>` is stripped).
2. **A boot-path smoke test.** Tests skip the app lifespan, so I separately booted
   the real app to confirm the startup **seed** runs and the seeded shares produce
   the intended owner/editor/viewer relationships.
3. **Driving the actual UI in a browser.** Using Playwright I logged in as all three
   seeded users and confirmed the real experience: Alice edits and the change
   **persists across a full page reload** (proving DB persistence, not just local
   state); the autosave indicator reaches "All changes saved"; Bob (editor) sees the
   doc under "Shared with me" with a "Can edit" badge and can edit it; **Carol
   (viewer) opens it read-only — disabled title, no toolbar, no Share button.**
4. **Testing the reviewer's exact command.** I ran `docker compose up --build`
   against a clean build and verified health, login, seeded owned+shared documents,
   and SPA serving all worked in the container — because "works on my machine" isn't
   a deliverable.

## Honest assessment of AI's role

AI made me faster, not more careless. The parts that determine whether this project
is any good — the scope cuts, the access-control model, the storage-format decision,
and the insistence on verifying behavior in a browser and a container — were mine.
The parts AI accelerated were the ones where speed is pure upside. Every automated
test in this repo passes, and I watched the core flows work with my own eyes before
calling anything done.
