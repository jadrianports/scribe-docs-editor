# syntax=docker/dockerfile:1

# --- Stage 1: build the React frontend ---
FROM node:22-bookworm-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Stage 2: Python runtime serving the API + built SPA on a single port ---
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATABASE_URL=sqlite:////data/scribe.db \
    SECRET_KEY=change-me-in-production
WORKDIR /app
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ ./backend/
# Built SPA from stage 1 -> FastAPI serves it (see app/main.py FRONTEND_DIST).
COPY --from=frontend /app/frontend/dist ./frontend/dist
VOLUME ["/data"]
EXPOSE 8000
WORKDIR /app/backend
# Honor $PORT when the platform injects one (e.g. Koyeb), default to 8000 locally.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
