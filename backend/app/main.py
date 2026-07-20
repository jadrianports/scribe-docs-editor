import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from starlette.middleware.sessions import SessionMiddleware

from .collab.rooms import room_manager
from .config import is_production, resolve_secret_key, validate_data_dir
from .db import SessionLocal, init_db
from .routers import auth, collab, documents, export, shares, upload
from .seed import seed

SECRET_KEY = resolve_secret_key()
FRONTEND_DIST = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist")
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_data_dir()
    init_db()
    db = SessionLocal()
    try:
        seed(db)
    finally:
        db.close()
    yield
    # Flush every dirty collab room's snapshot before the process exits --
    # turns a controlled restart/shutdown into zero collab-session data loss
    # (a SIGKILL still costs at most one snapshot-ticker interval).
    await room_manager.shutdown_flush()


app = FastAPI(title="Scribe API", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware, secret_key=SECRET_KEY, same_site="lax", https_only=is_production()
)

app.include_router(auth.router, prefix="/api")
app.include_router(documents.router, prefix="/api")
app.include_router(shares.router, prefix="/api")
app.include_router(upload.router, prefix="/api")
app.include_router(export.router, prefix="/api")
app.include_router(collab.router, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok"}


# --- Serve the built SPA in production (single-service deploy) ---
# In dev/tests the dist folder does not exist, so this block is skipped and the
# API runs on its own (Vite dev server proxies /api during local development).
if os.path.isdir(FRONTEND_DIST):
    _DIST_ROOT = os.path.realpath(FRONTEND_DIST)

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        # Serve a real built asset only if the resolved path stays inside the
        # dist directory; otherwise fall back to index.html for SPA client-side
        # routing. The containment check prevents path-traversal (e.g. a request
        # for "../../etc/passwd") from escaping the static root.
        candidate = os.path.realpath(os.path.join(_DIST_ROOT, full_path))
        inside_dist = candidate == _DIST_ROOT or candidate.startswith(_DIST_ROOT + os.sep)
        if full_path and inside_dist and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(_DIST_ROOT, "index.html"))
