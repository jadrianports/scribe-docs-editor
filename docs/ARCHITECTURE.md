# Architecture Note

This document explains what I prioritized, the key technical decisions, and the
tradeoffs I accepted under a deliberately limited scope.

## What I prioritized, and why

The brief rewards **depth in a few areas over shallow coverage everywhere**, so I
spent my time on the three things a reviewer can actually judge:

1. **A coherent editing flow.** Rich text that round-trips losslessly, autosave
   that feels live, and a title/rename/reopen loop that just works. This is the
   product's core, so it got the most polish.
2. **Sharing that is actually enforced, not simulated.** Access control lives in
   one server-side helper and is covered by tests. The UI reflects roles (a viewer
   literally cannot edit), but the UI is not the enforcement boundary — the API is.
3. **Engineering scaffolding that shows how I work.** Request validation, a
   consistent error contract, a meaningful test suite, and a genuine one-command
   run (`docker compose up`) that I verified against a clean container.

Everything else was consciously cut or deferred (see the end of this note).

## System shape

```
┌─────────────────────────────┐         ┌──────────────────────────────┐
│  React SPA (TipTap editor)  │  /api   │  FastAPI                     │
│  React Query + Router       │ ──────► │  auth · documents · shares   │
│                             │ cookie  │  upload · export             │
└─────────────────────────────┘         │        │                     │
                                        │        ▼                     │
                                        │  SQLAlchemy → SQLite (file)  │
                                        └──────────────────────────────┘
```

In development the two run separately (Vite on 5173 proxies `/api` to Uvicorn on
8000). In production a **multi-stage Docker image** builds the SPA with Node, then
hands the static bundle to FastAPI, which serves both the API and the app on **one
port**. One service, one URL, one thing to deploy.

## Key decisions and tradeoffs

### 1. Store content as sanitized HTML (not ProseMirror JSON)
TipTap can persist either its internal JSON or HTML. I chose **HTML** because it is
language-agnostic: the Python backend can *produce* it (converting uploaded
Markdown) and *consume* it (converting to Markdown on export) without running a
JavaScript ProseMirror runtime on the server. The cost is that HTML is a looser
format than the editor's structured JSON. I mitigate that with a strict **`bleach`
allow-list** applied on every write and upload — only the exact tags the editor
produces survive (`p, br, strong, em, u, s, h1–h3, ul, ol, li, blockquote`, no
attributes). This doubles as XSS defense: a `<script>` in an uploaded file is
stripped before it is ever stored (there's a test for exactly this).

### 2. Access control in one place, with a 404-not-403 leak rule
Every document route resolves access through a single `require_document(min_role)`
dependency that computes an effective role (`owner` > `editor` > `viewer`) and
enforces a minimum. Two deliberate behaviors:
- A user with **no** relationship to a document gets **404**, not 403 — so the API
  never confirms the existence of documents you can't see.
- A user who **can read but not write** gets **404 on read of an invisible doc but
  403 when they try to mutate a visible one** — the distinction a real product needs.

Centralizing this means the rules are tested once and can't drift per-endpoint.

### 3. Autosave with a visible status, last-write-wins
Editing feels most "document-like" when saving is invisible, so edits debounce for
800 ms and PATCH automatically, with a `Saving… / All changes saved / Save failed`
indicator and Ctrl-S to force-save. The honest tradeoff: on a document two editors
have open at once, this is **last-write-wins** — there's no operational-transform or
CRDT merge. Real-time collaboration was explicitly out of scope; I'd address the
concurrency gap with version history (below) before true multiplayer.

### 4. SQLite, seeded on boot, no migration tool
For a single-service take-home, SQLite is the right amount of database: zero setup,
one file, trivially shippable in a container volume. I create tables on startup and
seed demo data only if the users table is empty (idempotent). I deliberately did
**not** add Alembic — migrations are the correct call for a living schema, but here
they'd be scaffolding without a payoff.

### 5. PDF export on the client, Markdown on the server
Markdown export is a pure HTML→MD transform, so it lives on the server
(`markdownify`). For PDF I used the **browser's print-to-PDF** against a dedicated
print stylesheet rather than a server-side renderer like WeasyPrint. That avoids
pulling heavy native libraries (Cairo/Pango) into the image for a feature whose
output the browser already produces well. The tradeoff is that PDF styling is bound
by what print CSS can express — fine for a text document.

## Where the boundaries are

Each unit has one job and a narrow interface:
`content.py` (conversion + sanitization), `access.py` (authorization),
`auth.py` (sessions + hashing), and one router per resource. On the frontend, the
authorization rules are mirrored in a tiny pure-function module (`lib/permissions`)
that's unit-tested, so the UI's role logic is verified independently of React.

## Deliberate scope cuts

- **No hosted deployment.** The brief lists a live URL; I chose local-first
  delivery (Docker + repo) and made hosting a documented, low-effort follow-up
  rather than spend the time on infra. This is the one place I traded a listed
  deliverable for depth elsewhere — a conscious bet, called out honestly.
- **No real-time collaboration, comments, or version history** — each is a project
  in itself.
- **`.txt` / `.md` upload only** — `.docx` needs a heavier converter for marginal
  demonstration value.
- **Seeded accounts, no registration flow** — enough to demonstrate multi-user
  sharing without building account lifecycle management.

## If this were going to production

The first three changes I'd make: move sessions to a shared store and rotate
`SECRET_KEY` out of the compose file into real secrets; add version history to make
saves non-destructive; and put the access-control helper behind an integration test
matrix per role × endpoint rather than the representative cases tested today.
