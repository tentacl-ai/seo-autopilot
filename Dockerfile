FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Build deps for some wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first (better caching)
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy source
COPY seo_autopilot/ ./seo_autopilot/
COPY alembic/ ./alembic/
COPY alembic.ini setup.py ./
COPY projects.yaml.example ./projects.yaml.example

# Reports, DB, logs live in mounted volumes
RUN mkdir -p /app/reports /app/data /app/logs \
    && ln -s /app/data/projects.yaml /app/projects.yaml || true

ENV DATABASE_URL="sqlite+aiosqlite:////app/data/seo_autopilot.db" \
    PROJECT_CONFIG_PATH="/app/data/projects.yaml" \
    LOG_FILE="/app/logs/seo-autopilot.log" \
    API_HOST="0.0.0.0" \
    API_PORT="8002"

EXPOSE 8002

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD curl -f http://localhost:8002/api/health || exit 1

CMD ["python", "-m", "uvicorn", "seo_autopilot.api.main:app", \
     "--host", "0.0.0.0", "--port", "8002"]
