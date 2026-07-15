# Deploying Scribe to Koyeb (free, no credit card)

Scribe is a **single Docker service** — FastAPI serves the built React SPA *and*
the API on one port — so there is only one thing to deploy and one URL to share.
The container honors the platform's `$PORT` (falling back to 8000 locally), so it
runs on Koyeb unmodified.

## One-time deploy (~5 minutes)

1. Make sure the repo is pushed to GitHub (it is:
   `https://github.com/jadrianports/scribe-docs-editor`).
2. Go to **https://app.koyeb.com** and sign up (GitHub login; no card required).
3. **Create Web Service → GitHub** → authorize Koyeb → pick the
   **`scribe-docs-editor`** repo, branch **`main`**.
4. **Builder:** Koyeb auto-detects the **Dockerfile** — leave it on Dockerfile.
5. **Instance:** choose the **Free** (`nano`) instance.
6. **Exposed port:** set to **8000** (Koyeb injects `$PORT`; the container reads it).
   Set the **health check** to **HTTP `GET /api/health`** (optional but nicer than
   the default TCP check).
7. **Environment variables:** add one secret so session cookies are signed with a
   real key:
   - `SECRET_KEY` = a long random string. Generate one with:
     ```bash
     python -c "import secrets; print(secrets.token_hex(32))"
     ```
8. Click **Deploy**. The first build takes ~3–5 min (it compiles the frontend and
   installs the backend). When it goes healthy you get a public URL like
   `https://scribe-docs-editor-<org>.koyeb.app`.
9. Open the URL and log in with a seeded account (`alice@example.com` /
   `demo1234`). The database is seeded automatically on first boot.

## Persistence note (important, and by design)

The Koyeb free instance has an **ephemeral filesystem**, so the SQLite database at
`/data/scribe.db` resets whenever the service restarts or redeploys. This is fine
for a review because the app **re-seeds on every boot** — the demo users and the
pre-shared "Project Roadmap" always come back, so the sharing flow is always
demonstrable. Documents a reviewer creates during a session persist until the next
restart. For durable storage you would attach a Koyeb persistent volume (or point
`DATABASE_URL` at managed Postgres); that was out of scope for this take-home.

## Updating the deployment

Koyeb auto-redeploys on every push to `main` (if you enabled autodeploy), or click
**Redeploy** in the Koyeb dashboard.

## Environment variables reference

| Variable | Purpose | Default |
|---|---|---|
| `PORT` | Port the server binds to | `8000` (Koyeb sets this) |
| `SECRET_KEY` | Signs the session cookie — set a real random value in prod | `dev-secret-change-me` |
| `DATABASE_URL` | SQLAlchemy URL | `sqlite:////data/scribe.db` |
