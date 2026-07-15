# Walkthrough Video

**Link:** _TODO — paste your unlisted Loom / YouTube URL here before submitting._

A 3–5 minute walkthrough covering:

- The main user flow (log in → create/edit → upload → share → reopen)
- What works end to end
- What was intentionally deprioritized and why
- Key implementation decisions (HTML storage, server-enforced roles, single-service
  packaging)
- How AI supported the workflow

## Suggested recording script (~4 min)

1. **(0:00) Intro** — "Scribe: a rich-text editor with sharing. Local-first,
   `docker compose up`, open localhost:8000."
2. **(0:20) Editing** — Log in as Alice. Open "Welcome to Scribe". Toggle bold /
   italic / underline, add a heading and a list. Point out the `Saving… → All
   changes saved` indicator. Rename the doc inline.
3. **(1:15) Upload** — Back to dashboard → Upload a `.md` file → it becomes a new
   editable document with its formatting converted.
4. **(2:00) Sharing** — Open "Project Roadmap" → Share → show Bob (editor) and
   Carol (viewer). Note owned vs shared on the dashboard.
5. **(2:45) Roles in action** — Log out → log in as **Carol** → the same doc opens
   **read-only** (no toolbar, disabled title). Mention access is enforced on the
   server, not just hidden in the UI.
6. **(3:15) Persistence + export** — Reload to show the edit persisted; Export →
   Markdown / PDF.
7. **(3:40) Decisions + AI** — 20 seconds on the scope cuts and how AI (Claude Code
   + live docs) sped up boilerplate while I owned the design and verification.
