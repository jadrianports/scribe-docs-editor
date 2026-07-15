# Scribe

A small full-stack **rich-text document editor** with **real-time collaborative
editing**: create, rename, edit (together, live, with each other's cursors visible),
autosave, and reopen documents; upload a `.txt` / `.md` file to start a new one;
share with viewer / editor roles; export to Markdown or PDF. Built as a focused
product slice — a usable editing flow, a sharing model that is actually enforced,
and the engineering scaffolding (validation, tests, one-command run) around them.

- **Run locally:** `docker compose up --build` → **http://localhost:8000**
- **Log in with:** `alice@example.com` / `demo1234` (two more accounts below)
- **Deploy-ready:** a one-click Render Blueprint ([`render.yaml`](render.yaml) +
  [`DEPLOY.md`](DEPLOY.md)) is included, but no instance is hosted right now —
  delivery is local-first (see [Deployment](#deployment) for the reasoning).

![Dashboard](docs/screenshots/scribe-dashboard.png)

---

## Features

- **Rich-text editing** — bold, italic, underline, three heading levels, and
  bulleted / numbered lists, powered by [TipTap](https://tiptap.dev). Content is
  stored as sanitized HTML, so formatting round-trips exactly when you reopen a
  document.
- **Real-time collaborative editing** — multiple people can have the same document
  open at once and see each other's edits and cursors live. Conflicts resolve with a
  conflict-free **CRDT merge** (Yjs, via the Rust-backed `pycrdt` — no separate Node
  service), not last-write-wins, so simultaneous edits to the same paragraph both
  survive. A presence indicator shows who else is currently viewing, and the viewer /
  editor split is enforced **inside the collaboration socket itself** — a viewer
  receives every live edit but the server silently drops anything they try to write,
  not just a disabled toolbar.
- **Create, rename, autosave** — new documents and inline title editing, with
  debounced autosave and a live `Saving… / All changes saved` indicator (Ctrl / Cmd-S
  forces an immediate save). Document **content** isn't saved this way anymore — it
  syncs live through the collaboration connection above, so there's nothing to
  "save": once you're connected, every keystroke is already synced and durably
  persisted.
- **File upload** — upload a **`.txt`** or **`.md`** file (≤ 1 MB) to turn it into
  a new editable document. Markdown structure is converted to rich text; the file
  picker and this README both state the supported types. Other types, and files
  over the limit, are rejected server-side with a clear message.
- **Sharing** — a document owner shares with another user by email and picks a
  role: **Viewer** (read-only) or **Editor** (can edit and rename). The dashboard
  separates **My documents** from **Shared with me**, each item tagged with your role.
- **Export** — download a document as **Markdown** (server-side), or
  **Print → Save as PDF** via a dedicated print stylesheet (client-side).
- **Auth** — lightweight session-cookie login (bcrypt hashing) with three seeded
  demo accounts.

---

## Quick start (Docker — recommended)

Requires Docker Desktop. From the repository root:

```bash
docker compose up --build
```

Then open **http://localhost:8000**.

The SQLite database is created and seeded automatically on first boot and stored in
`./data/` on your host, so **locally your documents survive `docker compose down` and
restarts**. To start completely fresh, delete the `./data/` folder.

---

## Deployment

Delivery is **local-first**: `docker compose up --build` plus this public repo. There
is **no hosted instance running right now** — a deliberate choice so reviewers get a
reliable local run instead of depending on a free tier that cold-starts and resets.

That said, the app is **deploy-ready as a single service**: FastAPI serves the built
SPA *and* the API on one port, and the container honors the platform's `$PORT`. A
committed **[`render.yaml`](render.yaml)** Blueprint plus **[`DEPLOY.md`](DEPLOY.md)**
make a live Render deploy a ~5-minute, one-click step whenever it's wanted — no code
changes, just an "Apply" in Render. (An earlier draft targeted Koyeb, but Koyeb was
acquired by Mistral and shut down its self-serve deploy product; `DEPLOY.md` notes
this.)

Real-time collaboration travels with that single-service deploy for free. The
collaboration WebSocket trusts a **same-origin** request automatically, and this
deploy is always same-origin (the SPA and the API share one
`https://<name>.onrender.com` URL) — so multi-user editing works with zero extra
configuration. `ALLOWED_WS_ORIGINS` (see [`DEPLOY.md`](DEPLOY.md)) only needs setting
for a split-origin setup, e.g. a separately-hosted frontend pointed at this API.

Because the deploy would use SQLite on a free ephemeral disk, it **re-seeds on every
boot**, so the demo users and the pre-shared "Project Roadmap" always come back and the
sharing flow is always demonstrable. Local runs persist normally via the `./data`
volume. Durable cloud storage (a persistent volume or managed Postgres via
`DATABASE_URL`) is a documented next step, not a rewrite.

---

## Seeded demo accounts

All three accounts share the password **`demo1234`** (demo-only, seeded in
`backend/app/seed.py`).

| Email | Name | Starts with |
|---|---|---|
| `alice@example.com` | Alice | Owns "Welcome to Scribe" and "Project Roadmap"; shares the roadmap with Bob & Carol |
| `bob@example.com` | Bob | Owns "Bob's Meeting Notes"; is an **editor** on Alice's roadmap |
| `carol@example.com` | Carol | Is a **viewer** on Alice's roadmap |

**To see sharing end to end:** log in as **Alice** → open "Project Roadmap" → click
**Share** to see Bob (editor) and Carol (viewer). Then log out and log in as **Bob**
(can edit the shared roadmap) or **Carol** (opens read-only, no toolbar).

---

## Running locally without Docker

Two terminals. **Backend** (Python 3.12):

```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload      # serves the API on http://localhost:8000
```

**Frontend** (Node 20+):

```bash
cd frontend
npm install
npm run dev                        # Vite dev server on http://localhost:5173
```

Open **http://localhost:5173**. The Vite dev server proxies `/api` to the backend on
port 8000, so both halves work together with hot reload. (In Docker, and in any
container deploy, there is no proxy — FastAPI serves the pre-built frontend directly on
the single port.)

---

## Running the tests

**Backend** — 46 tests covering access control (viewer-vs-editor, non-collaborator
404-not-403), upload conversion + sanitization, Markdown export, auth, and real-time
collaboration (WebSocket auth/origin/access checks, CRDT convergence across
concurrent clients, Yjs→HTML derivation including sanitizer-stability, and SQLite
persistence across room restarts):

```bash
cd backend
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pytest -v
```

**Frontend** — unchanged by this feature: still 3 unit tests for the permission
logic, plus a clean `tsc --noEmit`. Collaboration is exercised by the backend's
real-socket tests above and by manual two-browser verification, not new frontend
unit tests:

```bash
cd frontend
npm test
```

---

## Tech stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2, Pydantic v2 |
| Auth | Signed-cookie sessions, bcrypt password hashing |
| Storage | SQLite (file-based; no external service) |
| Content safety | `bleach` allow-list sanitization on every write / upload |
| Frontend | React 19 + TypeScript, Vite, TipTap 3 (StarterKit), Tailwind CSS v4, React Query |
| Real-time collab | Yjs CRDT — `pycrdt`, `pycrdt-websocket`, `pycrdt-store`, `websockets` (backend: one Python service, no Node); `yjs`, `y-websocket`, `@tiptap/extension-collaboration`, `@tiptap/extension-collaboration-caret` (frontend) |
| Packaging | One multi-stage Docker image, one service on one port (honors `$PORT`) |

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the reasoning behind these
choices and [`docs/AI_WORKFLOW.md`](docs/AI_WORKFLOW.md) for how AI was used (including
two real bugs it caught that the unit tests could not).

---

## Known limitations (stated honestly)

- **Markdown import preserves *basic* formatting only** — headings, bold, italic,
  lists, and blockquotes. Links, code blocks, images, and tables are stripped by the
  sanitizer. This is deliberate: the editor's schema and the server sanitizer are
  intentionally aligned to **one safe subset**, which is also what makes formatting
  round-trip losslessly and blocks stored XSS. Widening import (links / code) is a
  listed next step.
- **Real-time collaboration's export/view snapshot can lag active editing** —
  `content_html` (what Markdown/PDF export and the plain document view read) is
  refreshed when the *last* editor leaves a document's live session, not
  continuously while people are still editing it. Exporting a document that's
  currently being co-edited returns the content as of the last time everyone had
  closed it, not the in-progress edits. A live or periodic snapshot while a room
  stays open is a documented next step, not an oversight.
- **A rare seed race on a document's first-ever collaborative session** — two
  clients opening a document that has never been opened for collaboration before,
  at the exact same instant, could both observe "not yet seeded" and each insert
  the document's saved content before either one's seeded flag has propagated to
  the other. Low-probability (it needs two people to open the same
  brand-new-to-collaboration document within the same network round-trip) and not
  a correctness risk beyond a cosmetic doubled paragraph — Yjs still merges both
  inserts deterministically, it just isn't the single clean copy the seeding guard
  is meant to produce.
- **Single instance only** — collaboration rooms live in one process's memory
  (`RoomManager`); there's no cross-instance fan-out yet. Running more than one
  backend instance behind a load balancer would split a document's collaborators
  across separate rooms that never see each other's edits. A Redis-backed fan-out
  is the documented path to horizontal scaling (see `docs/ARCHITECTURE.md`), not a
  rewrite.
- **No hosted instance is running** — delivery is local-first (see
  [Deployment](#deployment)). The included one-click Render deploy would use a free
  ephemeral disk that re-seeds on boot; local runs persist via the `./data` volume.

---

## Project status

**Working end to end:**
- Create / rename / edit / autosave / reopen with formatting preserved
- **Real-time collaborative editing** — multiple people editing one document at once
  with conflict-free CRDT merge (Yjs via `pycrdt`), live per-user cursors, a presence
  indicator, and the viewer role enforced **inside the collaboration socket**, not
  just the UI
- `.txt` and `.md` upload → new document (converted + sanitized)
- Sharing by email with viewer / editor roles; owned vs shared clearly separated
- Access control enforced **server-side** (viewers can't edit; non-collaborators get 404)
- Markdown + PDF export
- Persistence across refresh and restarts (SQLite volume locally)
- Automated tests (46 backend + 3 frontend), clean `tsc --noEmit`, one-command Docker run
- **Deploy-ready** as a single service — one-click Render Blueprint
  ([`render.yaml`](render.yaml) + [`DEPLOY.md`](DEPLOY.md)); delivered local-first (not
  currently hosted)

**Intentionally deprioritized:**
- Comments and version history — each is a project in itself.
- `.docx` upload — needs a heavier converter for marginal demonstration value.
- Self-serve registration / password reset — accounts are seeded to demonstrate
  multi-user sharing without account-lifecycle management.

**What I'd build next with another 2–4 hours:**
1. **Document version history** — snapshot on save + a restore panel (the schema
   already isolates content on the document row, so this is additive).
2. **`.docx` upload** via a converter such as `mammoth`, to broaden the import story.
3. **Widen Markdown import** to preserve links and code blocks (extending the editor
   schema + sanitizer allow-list together).
4. **A live/periodic HTML snapshot during active co-editing** — right now
   `content_html` (what export and the plain document view read) only refreshes once
   a document's collaboration room empties; snapshotting periodically while it's
   still open would close that lag without changing the Y.Doc source-of-truth model.

---

## Supported upload types

Only **`.txt`** and **`.md`**, up to **1 MB**. This limit is enforced on the server
and surfaced in the UI (the file picker filters to these types; unsupported or
oversized files return a clear error).
