# Architecture Note

This document explains what I prioritized, the key technical decisions, and the
tradeoffs I accepted under a deliberately limited scope.

## What I prioritized, and why

The brief rewards **depth in a few areas over shallow coverage everywhere**, so I
spent my time on the things a reviewer can actually judge:

1. **A coherent editing flow.** Rich text that round-trips losslessly, autosave that
   feels live, and a title / rename / reopen loop that just works. This is the
   product's core, so it got the most polish.
2. **Sharing that is actually enforced, not simulated.** Access control lives in one
   server-side helper and is covered by tests. The UI reflects roles (a viewer
   literally cannot edit), but the UI is not the enforcement boundary — the API is.
3. **Engineering scaffolding that shows how I work.** Request validation, a consistent
   error contract, a meaningful test suite, a genuine one-command run
   (`docker compose up`) I verified against a clean container, and a **deploy-ready
   single-service image** (a committed one-click Render Blueprint; delivery is
   local-first, with no instance currently hosted).

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

In development the two halves run separately (Vite on 5173 proxies `/api` to Uvicorn
on 8000). In production a **multi-stage Docker image** builds the SPA with Node, then
hands the static bundle to FastAPI, which serves both the API and the app on **one
port** (honoring `$PORT`, default 8000). One service, one URL, one thing to deploy —
which is exactly what makes the committed [Render Blueprint](../DEPLOY.md) a one-click
deploy with no orchestration, if and when a live instance is wanted.

(The diagram above shows the REST shape; real-time collaboration is a second, parallel
entry point into the same FastAPI process — a WebSocket at `/api/collab/{doc_id}` —
covered in decision #3 below.)

## Key decisions and tradeoffs

### 1. Store content as sanitized HTML (not ProseMirror JSON)
TipTap can persist either its internal JSON or HTML. I chose **HTML** because it is
language-agnostic: the Python backend can *produce* it (converting uploaded Markdown)
and *consume* it (converting to Markdown on export) without running a JavaScript
ProseMirror runtime on the server. The cost is that HTML is a looser format than the
editor's structured JSON. I mitigate that with a strict **`bleach` allow-list** applied
on every write and upload — only the exact tags the editor produces survive
(`p, br, strong, em, u, s, h1–h3, ul, ol, li, blockquote`, no attributes). This doubles
as XSS defense: a `<script>` in an uploaded file is stripped before it is ever stored
(there's a test for exactly this).

**The deliberate consequence:** the editor schema and the sanitizer allow-list are one
aligned subset. That is what lets formatting round-trip losslessly (nothing the editor
emits is ever stripped, and nothing that survives sanitization is un-renderable), and
it is why **Markdown import preserves basic formatting only** — headings, bold, italic,
lists, blockquotes. Links, code blocks, images, and tables are stripped on import
because they are outside the subset. Widening that subset (schema + sanitizer together)
is the tracked next step, not an accident.

### 2. Access control in one place, with a 404-not-403 leak rule
Every document route resolves access through a single `require_document(min_role)`
dependency that computes an effective role (`owner` > `editor` > `viewer`) and enforces
a minimum. Two deliberate behaviors:
- A user with **no** relationship to a document gets **404**, not 403 — so the API
  never confirms the existence of documents you can't see.
- A user who **can read but not write** gets **403** when they try to mutate a document
  they can see — the distinction a real product needs.

Centralizing this means the rules are tested once and can't drift per-endpoint.

### 3. Real-time collaboration: the Y.Doc is authoritative, HTML is derived
Editing is now genuinely multiplayer. The frontend's TipTap editor binds to a shared
Yjs document (`@tiptap/extension-collaboration` + `@tiptap/extension-collaboration-caret`)
synced over a WebSocket at `/api/collab/{doc_id}` into a per-document room (a pycrdt
`YRoom`), one per currently-open document, managed by a small in-process
`RoomManager` that ref-counts connections and starts/stops rooms on demand. Every
editor in a room sees every other editor's keystrokes and cursor live, and Yjs's CRDT
resolves concurrent edits by **merging** them rather than picking a winner, so two
people typing in the same paragraph at once both keep their words.

This inverts where "the real content" lives. The **Yjs update stream is now the
durable source of truth** for a document's body, persisted incrementally to
`data/yjs.db` via `pycrdt-store`'s `SQLiteYStore` (one file, keyed per document — the
same zero-ops SQLite philosophy as decision 4). `documents.content_html` — what
export, the plain document view, and every other non-collaborative reader sees — is
now a **derived, sanitized snapshot**:
it's re-rendered from the live Y.Doc (`ydoc_to_html`) and pushed back through the
*same* `sanitize_html` allow-list every other write path already uses (decision 1's
invariant holds here too — not a second, divergent sanitizer) when a document's last
editor disconnects and its room empties. A document that's never been opened for
collaboration is seeded into its Y.Doc from that same `content_html` — but that seed
happens **server-side**: `RoomManager._create_room` converts the stored HTML into the
room's Y.Doc and flips its `seeded` flag once, under that document's own per-doc lock,
before any client connection is accepted. The client has no seeding logic of its own at
all — it only ever reads the already-seeded shared doc — so the seed genuinely happens
exactly once, by construction, not by a client-side guard racing another client's guard.

Access control rides along unchanged: the WebSocket authenticates off the same session
cookie and authorizes through the same `effective_role` used everywhere else (decision
2), then selects one of two channel adapters — a normal read-write channel for
editor/owner, or a **read-only channel that silently drops a viewer's mutations at the
transport layer** (not just a disabled toolbar) for viewer. A viewer sees every live
edit; they just can't make one, and it's enforced server-side — same principle as the
REST 403, applied to a socket instead of a route.

**Still one Python service, no Node.** `pycrdt` (a Rust-backed Yjs implementation with
Python bindings) plus `pycrdt-websocket` and `pycrdt-store` do the CRDT work inside the
same FastAPI process — there is no separate Node-based Yjs server to run, deploy, or
keep in sync with the Python backend, which is what keeps the "one service, one thing
to deploy" story (above) true for this feature too.

**The honest tradeoffs**, stated as plainly as decision 1's:
- **Export/view can lag active editing, bounded to one interval.** `content_html`
  is refreshed on a periodic tick — every `SCRIBE_SNAPSHOT_INTERVAL` seconds
  (default 15) while a room stays dirty — plus on room-empty and on a graceful
  shutdown flush. So exporting or plainly viewing a document while people are
  still actively co-editing it returns content that's fresh to within one tick
  interval, not the exact in-progress edit; it's a bounded, stated tradeoff, not
  an exact/continuous guarantee. A hard kill (SIGKILL, no chance to run the
  shutdown flush) still costs at most one tick interval, never the whole
  session.
- **The multi-client seed race is closed by construction, not mitigated.** An earlier
  version of this design seeded a never-collaborated-on document from the client, the
  first time any editor opened it — which meant two clients opening the same
  brand-new-to-collaboration document at the exact same instant could both observe
  "not yet seeded" and each insert the saved content, since Yjs merges concurrent
  structural inserts rather than deduplicating them. Seeding moved server-side (into
  `RoomManager._create_room`, under the per-doc lock, before any client connects) so
  there is exactly one seed writer and it always runs before the first client is
  served — there is no window left in which two openers can race each other.
- **A transport failure never costs a room its unsaved edits.** A client's socket
  failing mid-broadcast is caught and swallowed at the channel adapter, which drops
  that one client and nothing else — the room fans its sends out through its own
  shared, long-lived task group, so letting an exception escape a single client's
  send would cancel every sibling task in that group and take the whole room down
  with it. If a room's background task dies for some other reason, its record is
  **marked crashed** and kept in place rather than discarded, so the connection's
  existing teardown path (`release()`) still finds it and still derives and persists
  the room's final snapshot — exactly one writer runs at teardown either way. What
  this does not change: a hard kill (SIGKILL, no chance to run the shutdown flush)
  still costs at most one tick interval, same as the tradeoff above.
- **Single instance only.** Rooms live in one process's memory; nothing fans updates
  out across instances. Fine for the single-service deploy this project ships
  (decision 4 already keeps everything to one process), but it's the one piece of this
  design that would need real infrastructure work — a Redis-backed fan-out — before
  running behind more than one backend instance (see "If this were going to
  production," below).

Title edits are the one thing that still goes through the old path: they debounce
800 ms and PATCH through the REST API exactly as before (`Saving… / All changes saved`
indicator, Ctrl-S to force-save, each response written back into the React Query
cache), because a document's title was never part of the shared Yjs body content.

### 4. SQLite, seeded on boot, no migration tool
For a single-service take-home, SQLite is the right amount of database: zero setup, one
file, trivially shippable in a container volume. I create tables on startup and seed
demo data only if the users table is empty (idempotent), which is also what would keep a
Render deploy working across its ephemeral-disk restarts. I deliberately did **not**
add Alembic — migrations are the correct call for a living schema, but here they'd be
scaffolding without a payoff.

### 5. PDF export on the client, Markdown on the server
Markdown export is a pure HTML→MD transform, so it lives on the server (`markdownify`).
For PDF I used the **browser's print-to-PDF** against a dedicated print stylesheet
rather than a server-side renderer like WeasyPrint. That avoids pulling heavy native
libraries (Cairo / Pango) into the image for a feature the browser already produces
well. The tradeoff is that PDF styling is bound by what print CSS can express — fine
for a text document.

### 6. The single-service SPA fallback is containment-checked
Because FastAPI serves the built SPA under a catch-all route, that route resolves the
requested path with `realpath` and serves a file only if it stays inside the `dist`
directory, otherwise it falls back to `index.html`. This is what lets client-side
routing work while preventing a crafted `/../../` request from escaping the static root
(a path-traversal read) — a small but real hardening step for an app packaged to be
publicly deployable.

## Where the boundaries are

Each unit has one job and a narrow interface: `content.py` (conversion + sanitization),
`access.py` (authorization), `auth.py` (sessions + hashing), and one router per
resource. On the frontend, the authorization rules are mirrored in a tiny pure-function
module (`lib/permissions`) that's unit-tested, so the UI's role logic is verified
independently of React.

The collaboration layer follows the same one-job-per-file split rather than growing
into one large module: `collab/rooms.py` (room lifecycle and ref-counting),
`collab/channel.py` (the read-write vs. read-only WebSocket adapter), `collab/html.py`
(Y.Doc → HTML), `collab/snapshot.py` (the sanitized write-back), and `collab/ystore.py`
(SQLite persistence) — with `routers/collab.py` itself staying thin, handling only
auth/origin/role selection before handing off to a room, the same way every REST
router stays thin around `access.py`.

## Deliberate scope cuts

- **No comments or version history** — each is a project in itself. Real-time
  collaboration (decision 3, above) closes the concurrency gap version history was
  originally meant to hedge against; version history is still cut, but now purely as
  a "restore an earlier draft" feature in its own right, not a stand-in for multiplayer.
- **`.txt` / `.md` upload only** — `.docx` needs a heavier converter for marginal
  demonstration value.
- **Basic-formatting Markdown import** — links / code / tables are outside the aligned
  editor-schema-plus-sanitizer subset (decision 1); widening it is a tracked next step.
- **Seeded accounts, no registration flow** — enough to demonstrate multi-user sharing
  without building account-lifecycle management.

## If this were going to production

The first changes I'd make: give a deployed instance **durable storage** (a Render
persistent volume or managed Postgres via `DATABASE_URL`) so data survives restarts;
move sessions to a shared store and keep `SECRET_KEY` as a real rotated secret (the
Render Blueprint already generates one; the compose file still uses the dev default);
add **version history** to make saves non-destructive; and put the access-control helper
behind a full integration matrix per role × endpoint rather than the representative
cases tested today.

Real-time collaboration adds one more: **horizontal scaling for the collab rooms
themselves.** `RoomManager` today is a single process's in-memory dict — correct and
sufficient for one instance, but two backend instances behind a load balancer would
split a document's collaborators into two rooms that never see each other's edits. The
standard fix is a Redis-backed fan-out (`y-redis`, or hand-rolling the equivalent
pub/sub bridge between `YRoom`s on different instances) so every instance shares one
logical room per document regardless of which instance a given client's WebSocket
lands on. I'd reach for that before ever running more than one instance of this
service — everything else in this list is already safe to scale horizontally
(documents and sessions already live in the shared database), but collab rooms
specifically are not.
