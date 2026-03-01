# ═══════════════════════════════════════════════════════════════
# AgenticEngine V2 — Production Dockerfile
# ═══════════════════════════════════════════════════════════════
# Multi-stage build:
#   Stage 1 (builder): Install dependencies
#   Stage 2 (runtime): Minimal image with non-root user
#
# Build:   docker build -t agentic-engine:2.0 .
# Run:     docker run -p 8000:8000 agentic-engine:2.0
# ═══════════════════════════════════════════════════════════════

# ── Stage 1: Builder ─────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# System deps for psycopg2 compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 2: Runtime ─────────────────────────────────────────
FROM python:3.12-slim AS runtime

# libpq needed at runtime for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 tini && rm -rf /var/lib/apt/lists/*

# Security: non-root user
RUN groupadd -r engine && useradd -r -g engine -d /app -s /sbin/nologin engine

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application
COPY engine/ engine/
COPY alembic.ini .
COPY tests/ tests/

# Own everything
RUN chown -R engine:engine /app

USER engine

# Environment
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" \
    || exit 1

EXPOSE 8000

ENTRYPOINT ["tini", "--"]

# Default: run migrations then start API
CMD ["sh", "-c", "echo 'DEBUG: DATABASE_URL='$DATABASE_URL; echo 'DEBUG: APP_ENV='$APP_ENV; echo 'DEBUG: PORT='$PORT; python -m alembic upgrade heads 2>&1 || echo 'WARNING: migrations failed, starting anyway'; python -m engine.api_server"]
