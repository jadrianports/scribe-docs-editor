# Submission — Scribe

A full-stack rich-text document editor with sharing, built for the take-home
assessment. **Deployed live on Render and runnable locally with one command.**

- **Live demo:** `https://<your-app>.onrender.com` *(paste the Render URL here once deployed)*
- **Run locally:** `docker compose up --build` → http://localhost:8000
- **Log in:** `alice@example.com` / `demo1234`

## What's included

| Item | Location |
|---|---|
| **Source code — backend** | [`backend/`](backend/) — FastAPI app, models, routers, tests |
| **Source code — frontend** | [`frontend/`](frontend/) — React + TipTap SPA |
| **README** (setup + run) | [`README.md`](README.md) |
| **Architecture note** | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| **AI workflow note** | [`docs/AI_WORKFLOW.md`](docs/AI_WORKFLOW.md) |
| **Deploy guide (Render)** | [`DEPLOY.md`](DEPLOY.md) |
| **Walkthrough video link** | [`VIDEO.md`](VIDEO.md) |
| **This submission index** | [`SUBMISSION.md`](SUBMISSION.md) |
| **Screenshots** | [`docs/screenshots/`](docs/screenshots/) |
| **One-command run** | [`docker-compose.yml`](docker-compose.yml) + [`Dockerfile`](Dockerfile) |

## How to run (for reviewers)

Fastest path — just open the **live demo** URL above and log in.

To run it yourself:

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

**Suggested review path:** sign in as Alice → open "Project Roadmap" → **Share** to see
the two grants → log out → sign in as **Bob** (edits the shared doc) → then **Carol**
(same doc opens read-only). This exercises owner, editor, and viewer in under two minutes.

## Feature checklist (all core areas)

- [x] Create, rename, edit, save, reopen documents
- [x] Rich text: bold, italic, underline, headings, bulleted + numbered lists
- [x] File upload (`.txt` / `.md` → new editable document, sanitized)
- [x] Sharing: owner grants viewer / editor access by email
- [x] Visible distinction between owned and shared documents
- [x] Access control enforced **server-side** (viewer read-only; non-collaborator 404-not-403)
- [x] Persistence across refresh and restarts (SQLite)
- [x] Validation + error handling (typed API errors, friendly UI states)
- [x] Automated tests (12 backend + 3 frontend), clean `tsc --noEmit`
- [x] One-command run reviewers can use (`docker compose up`)
- [x] **Live deployment** (single Docker service on Render)
- [x] Architecture note + AI workflow note
- [x] Stretch: export to Markdown and PDF

## Deployment note

Scribe is **deployed live on Render** (free tier, no credit card) as a single Docker
service built from this repo — FastAPI serves the built SPA and the API on one port, so
there is one thing to deploy and one URL to share. Step-by-step instructions are in
[`DEPLOY.md`](DEPLOY.md).

Render's free instance has an ephemeral disk, so the SQLite DB resets on restart — but the
app **re-seeds on every boot**, so the demo users and the pre-shared "Project Roadmap"
are always present and the sharing flow is always demonstrable; reviewer-created docs
persist until the next restart. Local runs persist normally via the `./data` volume.
Durable cloud storage (persistent volume or managed Postgres) is a documented next step.

## Status summary

- **Working:** every core feature above, verified via automated tests, a real-browser
  walkthrough of all three roles, and a clean-container `docker compose up`. Deployed live.
- **Deferred:** real-time collaboration, comments, version history, `.docx` upload,
  wider Markdown import (links / code), self-serve registration. Rationale in the
  architecture note.
- **Next 2–4 hours:** document version history → `.docx` import → wider Markdown import →
  real-time presence. (Details in the README's "Project status" section.)
