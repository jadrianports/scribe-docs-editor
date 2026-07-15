# Walkthrough Video

**Link:** _TODO — paste your unlisted Loom / YouTube URL here before submitting._

A 3–5 minute walkthrough. Below is a shot-by-shot choreography: each beat has a
timestamp, an **[ON SCREEN]** cue (what to click / show) and a **[SAY]** line (the
narration). Times are targets — aim to land the whole run at ~4:00. A 15-second
pre-flight checklist is at the bottom; do it before you hit record.

---

## Shot list

### (0:00) — Intro (~20s)
- **[ON SCREEN]** The live demo open in a browser at the Koyeb URL (login screen), tab
  visible. Have `docker compose up` running in a terminal you can flash briefly.
- **[SAY]** "This is Scribe — a rich-text document editor with sharing, export, and
  file upload. It's **deployed live on Koyeb**, and it also runs locally with a single
  command — `docker compose up` on port 8000. Same single Docker image both places:
  FastAPI serves the React app and the API on one port. Let me walk through it."

### (0:20) — Log in, create, rename, format, autosave (~60s)
- **[ON SCREEN]** Log in as `alice@example.com` / `demo1234`. On the dashboard, click
  **New document**. Rename the title inline (e.g. "Demo Notes"). Type a sentence, then
  select text and click **Bold**, **Italic**, **Underline**; add an **H1** and an
  **H2**; add a **bulleted** list and a **numbered** list.
- **[SAY]** "I'll log in as Alice and create a document. I can rename it inline… and the
  toolbar gives me the essentials — bold, italic, underline, two heading levels, bullet
  and numbered lists. Watch the top-right: every change autosaves — it goes from
  *Saving…* to **All changes saved**, no save button. Content is stored as sanitized
  HTML, so it round-trips exactly when I reopen it."

### (1:20) — Upload a Markdown file (~40s)
- **[ON SCREEN]** Back to the dashboard. Click **Upload**, pick a prepared `.md` file.
  It opens as a new document with headings / bold / lists converted to rich text.
- **[SAY]** "From the dashboard I can also upload a `.txt` or `.md` file — up to a
  megabyte — and it becomes a new editable document. The Markdown structure is converted
  into rich text. One honest note: import preserves **basic formatting** — headings,
  bold, italic, lists, blockquotes. Links and code blocks are deliberately stripped,
  because the editor's schema and the server's sanitizer are aligned to one safe subset —
  that's what keeps formatting lossless and blocks stored XSS."

### (2:00) — Sharing and the owned-vs-shared split (~45s)
- **[ON SCREEN]** Open **Project Roadmap**. Click **Share**. Show the two existing
  grants: **Bob — editor**, **Carol — viewer**. Return to the dashboard and point at the
  **My documents** vs **Shared with me** sections.
- **[SAY]** "Here's the sharing model. Alice owns 'Project Roadmap' and has shared it —
  Bob as an **editor**, Carol as a **viewer**. An owner shares by email and picks the
  role. And the dashboard cleanly separates **My documents** from **Shared with me**, so
  you always know what you own versus what's shared with you."

### (2:45) — Roles enforced (log in as Carol, read-only) (~30s)
- **[ON SCREEN]** Log out. Log in as `carol@example.com` / `demo1234`. Open the same
  **Project Roadmap** from "Shared with me". Show it opens **read-only**: no toolbar, the
  title field is disabled, no Share button.
- **[SAY]** "Now log out and come back as Carol, the viewer. She opens the exact same
  document — and it's **read-only**: no toolbar, the title's disabled, no Share. And this
  isn't just hidden in the UI — access is **enforced on the server**. A viewer who tries
  to edit through the API is rejected, and a user with no access gets a 404, so the API
  never even reveals a document exists."

### (3:15) — Persistence + export (~25s)
- **[ON SCREEN]** Reload the page to show the document (and any earlier edit) is still
  there. Open the **Export** menu → show **Markdown** download, then **PDF** (Print →
  Save as PDF).
- **[SAY]** "A quick reload proves this is real persistence — it's coming from the
  database, not local state. And I can export any document: **Markdown**, generated
  server-side, or **PDF** via a dedicated print stylesheet."

### (3:40) — Scope cuts + how AI built and verified it (~20s)
- **[ON SCREEN]** Back on the dashboard, or a slide showing the repo / docs. Optionally
  flash `docs/AI_WORKFLOW.md`.
- **[SAY]** "I deliberately scoped this: no real-time collaboration or version history —
  autosave is last-write-wins, and that's documented. On the AI side — I used Claude Code
  to build it, but the interesting part is verification. A **real-browser test caught a
  bug unit tests couldn't**: the toolbar buttons did nothing on click, because mousedown
  was collapsing the editor selection — fixed by preventing default on mousedown. And a
  **parallel review pass caught a path-traversal security bug** in the static file server
  before it went public. AI accelerated the build *and* the adversarial testing; the
  design decisions stayed mine. Thanks for watching."

---

## 15-second pre-flight checklist (do this before recording)

- [ ] **Fresh start:** `docker compose up --build` running clean (or the live Koyeb URL
      loaded and healthy).
- [ ] **Browser ready:** pointed at the **live URL** or **http://localhost:8000**,
      **logged out**, cache/other tabs closed.
- [ ] **Window sized:** browser sized so the toolbar, autosave indicator, and dashboard
      sections are all visible; zoom ~110% for legibility.
- [ ] **Demo file ready:** a small `.md` file with a heading, bold, and a list on the
      desktop for the upload beat.
- [ ] **Credentials handy:** `alice@example.com` and `carol@example.com`, both
      `demo1234`. Mic on, notifications off.
