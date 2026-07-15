import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from starlette.middleware.sessions import SessionMiddleware

from .db import SessionLocal, init_db
from .routers import auth, documents, export, shares, upload
from .seed import seed

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
FRONTEND_DIST = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist")
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    db = SessionLocal()
    try:
        seed(db)
    finally:
        db.close()
    yield


app = FastAPI(title="Scribe API", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site="lax", https_only=False)

app.include_router(auth.router, prefix="/api")
app.include_router(documents.router, prefix="/api")
app.include_router(shares.router, prefix="/api")
app.include_router(upload.router, prefix="/api")
app.include_router(export.router, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok"}


# --- Serve the built SPA in production (single-service deploy) ---
# In dev/tests the dist folder does not exist, so this block is skipped and the
# API runs on its own (Vite dev server proxies /api during local development).
if os.path.isdir(FRONTEND_DIST):

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        candidate = os.path.join(FRONTEND_DIST, full_path)
        if full_path and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(FRONTEND_DIST, "index.html"))
