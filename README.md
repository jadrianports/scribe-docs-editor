# Scribe

A small full-stack document editor: create, edit, and share rich-text documents.
Built as a focused product slice — a usable editing flow, a clear sharing model,
and the engineering scaffolding (validation, tests, one-command run) around them.

![Dashboard](docs/screenshots/scribe-dashboard.png)

---

## Features

- **Rich-text editing** — bold, italic, underline, three heading levels, and
  bulleted / numbered lists, powered by [TipTap](https://tiptap.dev). Content is
  stored as sanitized HTML, so formatting is preserved exactly when you reopen a
  document.
- **Create, rename, autosave** — new documents, inline title editing, and
  debounced autosave with a live `Saving… / All changes saved` indicator
  (Ctrl/Cmd-S forces an immediate save).
- **File upload** — upload a **`.txt`** or **`.md`** file (≤ 1 MB) to turn it into
  a new editable document. Markdown structure is converted to rich text; the file
  picker and this README both state the supported types. Other types are rejected
  with a clear message.
- **Sharing** — a document owner shares with another user by email and picks a
  role: **Viewer** (read-only) or **Editor** (can edit and rename). The dashboard
  separates **My documents** from **Shared with me**, each item tagged with your
  role.
- **Export** — download a document as **Markdown**, or **Print → Save as PDF** via
  a dedicated print stylesheet.
- **Auth** — lightweight session-cookie login with three seeded demo accounts.

---

## Quick start (Docker — recommended)

Requires Docker Desktop. From the repository root:

```bash
docker compose up --build
```

Then open **http://localhost:8000**.

The SQLite database is created and seeded automatically on first boot and stored
in `./data/` on your host, so your documents survive `docker compose down` and
restarts. To start completely fresh, delete the `./data/` folder.

---

## Seeded demo accounts

All three accounts share the password **`demo1234`** (demo-only, seeded in
`backend/app/seed.py`).

| Email | Name | Starts with |
|---|---|---|
| `alice@example.com` | Alice | Owns "Welcome to Scribe" and "Project Roadmap"; shares the roadmap with Bob & Carol |
| `bob@example.com` | Bob | Owns "Bob's Meeting Notes"; is an **editor** on Alice's roadmap |
| `carol@example.com` | Carol | Is a **viewer** on Alice's roadmap |

**To see sharing end to end:** log in as **Alice** and open "Project Roadmap" →
click **Share** to see Bob (editor) and Carol (viewer). Then log out and log in as
**Bob** (can edit the shared roadmap) or **Carol** (opens read-only, no toolbar).

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

Open **http://localhost:5173**. The Vite dev server proxies `/api` to the backend
on port 8000, so both halves work together with hot reload. (In Docker there is no
proxy — FastAPI serves the pre-built frontend directly on port 8000.)

---

## Running the tests

**Backend** — 12 tests covering access control, upload conversion + sanitization,
and auth:

```bash
cd backend
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pytest -v
```

**Frontend** — unit tests for the permission logic:

```bash
cd frontend
npm test
```

---

## Tech stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2, Pydantic v2 |
| Auth | Starlette signed-cookie sessions, bcrypt password hashing |
| Storage | SQLite (file-based; no external service) |
| Content safety | `bleach` allow-list sanitization on every write |
| Frontend | React 19 + TypeScript, Vite, TipTap, Tailwind CSS v4, React Query |
| Packaging | Multi-stage Docker image, one service on one port |

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the reasoning behind these
choices and [`docs/AI_WORKFLOW.md`](docs/AI_WORKFLOW.md) for how AI was used.

---

## Project status

**Working end to end:**
- Create / rename / edit / autosave / reopen with formatting preserved
- `.txt` and `.md` upload → new document (with sanitization)
- Sharing by email with viewer/editor roles; owned vs shared clearly separated
- Access control enforced server-side (viewers can't edit; non-collaborators get 404)
- Markdown + PDF export
- Persistence across refresh and restarts (SQLite volume)
- Automated tests (12 backend + 3 frontend) and one-command Docker run

**Intentionally deprioritized** (see the architecture note for why):
- No live hosted URL — delivery is local-first via Docker + this repo. The app is a
  single service, so hosting it later is a small step, not a rewrite.
- No real-time collaboration, comments, or version history.
- `.docx` upload is not supported (only `.txt` / `.md`).
- Self-serve registration / password reset — accounts are seeded.

**What I'd build next with another 2–4 hours:**
1. **Document version history** — snapshot on save + a restore panel (the schema
   already isolates content on the document row, so this is additive).
2. **`.docx` upload** via `mammoth` to broaden the import story.
3. A concurrency-safe save (last-write-wins is a documented limitation today).

---

## Supported upload types

Only **`.txt`** and **`.md`**, up to **1 MB**. This limit is enforced on the server
and surfaced in the UI (the file picker filters to these types; unsupported files
return a clear error).
