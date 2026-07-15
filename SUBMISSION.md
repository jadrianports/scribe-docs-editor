# Submission — Scribe

A full-stack rich-text document editor with sharing, built for the take-home
assessment.

## What's included

| Item | Location |
|---|---|
| **Source code — backend** | [`backend/`](backend/) — FastAPI app, models, routers, tests |
| **Source code — frontend** | [`frontend/`](frontend/) — React + TipTap SPA |
| **README** (setup + run) | [`README.md`](README.md) |
| **Architecture note** | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| **AI workflow note** | [`docs/AI_WORKFLOW.md`](docs/AI_WORKFLOW.md) |
| **This submission index** | [`SUBMISSION.md`](SUBMISSION.md) |
| **Walkthrough video link** | [`VIDEO.md`](VIDEO.md) |
| **Screenshots** | [`docs/screenshots/`](docs/screenshots/) |
| **One-command run** | [`docker-compose.yml`](docker-compose.yml) + [`Dockerfile`](Dockerfile) |

## How to run (for reviewers)

```bash
docker compose up --build
# then open http://localhost:8000
```

That's the whole setup — the database is created and seeded automatically. Full
local-dev (non-Docker) instructions are in the README.

## Credentials for reviewing the sharing flow

All seeded accounts use the password **`demo1234`**.

| Email | Role in the demo |
|---|---|
| `alice@example.com` | Owner — owns two docs, shares "Project Roadmap" with Bob & Carol |
| `bob@example.com` | **Editor** on Alice's "Project Roadmap" |
| `carol@example.com` | **Viewer** on Alice's "Project Roadmap" |

**Suggested review path:** sign in as Alice → open "Project Roadmap" → **Share** to
see the two grants → log out → sign in as **Bob** (edits the shared doc) → then
**Carol** (same doc opens read-only). This exercises owner, editor, and viewer in
under two minutes.

## Feature checklist (all core areas)

- [x] Create, rename, edit, save, reopen documents
- [x] Rich text: bold, italic, underline, headings, bulleted + numbered lists
- [x] File upload (`.txt` / `.md` → new editable document, sanitized)
- [x] Sharing: owner grants viewer/editor access by email
- [x] Visible distinction between owned and shared documents
- [x] Persistence across refresh and restarts (SQLite)
- [x] Validation + error handling (typed API errors, friendly UI states)
- [x] Automated tests (12 backend + 3 frontend)
- [x] One-command deployment reviewers can run (`docker compose up`)
- [x] Architecture note + AI workflow note
- [x] Stretch: export to Markdown and PDF

## Deployment note

Delivery is **local-first**: this repository plus `docker compose up`. I did not
stand up a hosted URL — that tradeoff is explained in the architecture note. Because
the app runs as a single container on one port, hosting it on any container platform
is a small, additive step.

## Status summary

- **Working:** every core feature above, verified via automated tests, a browser
  walkthrough of all three roles, and a clean-container `docker compose up`.
- **Incomplete / deferred:** hosted URL, real-time collaboration, comments, version
  history, `.docx` upload, self-serve registration.
- **Next 2–4 hours:** document version history, then `.docx` import. (Details in the
  README's "Project status" section.)
