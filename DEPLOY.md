# Deploying Scribe to Render (free, no credit card)

Scribe is a **single Docker service** — FastAPI serves the built React SPA *and*
the API on one port — so there is only one thing to deploy and one URL to share.
The container honors the platform's injected `$PORT`, so it runs on Render
unmodified, and a [`render.yaml`](render.yaml) Blueprint is committed so setup is
essentially one click.

> **Note on Koyeb:** an earlier draft targeted Koyeb, but Koyeb has been acquired
> by Mistral and shut down its self-serve deploy dashboard, so it is no longer a
> usable target. Render is the equivalent free, no-card, deploy-from-GitHub host,
> and the same `$PORT`-aware Docker image runs there without changes.

## One-time deploy (~5 minutes)

1. Make sure the repo is pushed to GitHub (it is:
   `https://github.com/jadrianports/scribe-docs-editor`).
2. Go to **https://dashboard.render.com** and sign up (GitHub login; no card
   required for free web services).
3. Click **New +  → Blueprint**, connect your GitHub, and select the
   **`scribe-docs-editor`** repo. Render reads [`render.yaml`](render.yaml) and
   shows a **Docker web service on the Free plan** with `SECRET_KEY` auto-generated
   and a health check on `/api/health`. Click **Apply**.
   - *(Alternative without the Blueprint:* **New + → Web Service** → connect the
     repo → Render auto-detects the **Dockerfile** → choose the **Free** instance →
     **Create Web Service**. It will still generate/prompt for `SECRET_KEY`.)*
4. The first build takes ~3–5 min (it compiles the frontend and installs the
   backend). When it goes live you get a public URL like
   `https://scribe-docs-editor.onrender.com`.
5. Open the URL and log in with a seeded account (`alice@example.com` /
   `demo1234`). The database is created and seeded automatically on first boot.

## Persistence note (important, and by design)

Render's free instance has an **ephemeral filesystem and spins down after ~15
minutes of inactivity** (the next request cold-starts in ~50s). The SQLite
database at `/data/scribe.db` therefore resets on each cold start or redeploy.
This is fine for a review because the app **re-seeds on every boot** — the demo
users and the pre-shared "Project Roadmap" always come back, so the sharing flow
is always demonstrable. Documents a reviewer creates persist until the next
spin-down. For durable storage you would attach a Render Disk (paid) or point
`DATABASE_URL` at managed Postgres; that was out of scope for this take-home.

## Updating the deployment

Render auto-redeploys on every push to `main` (enabled by default for Blueprint
services), or click **Manual Deploy** in the Render dashboard.

## Environment variables reference

| Variable | Purpose | Default |
|---|---|---|
| `PORT` | Port the server binds to | Render injects it; container falls back to `8000` locally |
| `SCRIBE_ENV` | Production hardening gate: `production` makes a missing `SECRET_KEY` fatal at startup, marks the session cookie `Secure`, and turns on the `SCRIBE_DATA_DIR` startup warning below | The image sets `SCRIBE_ENV=production`; unset (or any other value) means dev. This is production-opt-in via deploy config — nothing to set for a local run |
| `SECRET_KEY` | Signs the session cookie | Render generates a random value. **The image no longer bakes in a fallback key** — any other host that deploys this image and forgets to set `SECRET_KEY` while `SCRIBE_ENV=production` fails to start (raises at boot) instead of running with a forgeable key. Only in dev (`SCRIBE_ENV` unset) does the app fall back to `dev-secret-change-me` |
| `DATABASE_URL` | SQLAlchemy URL | `sqlite:////data/scribe.db` |
| `ALLOWED_WS_ORIGINS` | Extra allow-listed origins for the collaboration WebSocket (comma-separated) | `http://localhost:5173,http://localhost:8000` |

`SCRIBE_ENV=production` also drives the `SCRIBE_DATA_DIR` startup check (see the
[Persistence note](#persistence-note-important-and-by-design) above): the resolved absolute
`yjs.db` path is always logged, and unset/relative paths get a loud warning in production — which
is why Render free's `SCRIBE_DATA_DIR=/data` (set and writable, just ephemeral) never triggers it.

## Real-time collaboration and this deploy

**Works out of the box, no configuration needed.** The collaboration WebSocket
(`/api/collab/{doc_id}`) accepts a request whenever its `Origin` is **same-origin**
with the request's own `Host` header — and this single-service deploy is always
same-origin, since Render serves the SPA and the API from the one
`https://<name>.onrender.com` URL. `ALLOWED_WS_ORIGINS` above only matters if you
split the frontend and backend across two different origins (e.g. hosting the SPA
somewhere else and pointing it at this API) — add that other origin to the list in
that case. You do not need to set it for the Blueprint deploy described above.
