# ─────────────────────────────────────────────────────────────────────────────
# Dockerfile — Dirigera MQTT Bridge
#
# Target platform : Raspberry Pi 5 (linux/arm64)
# Base image      : python:3.12-slim (Debian Bookworm slim, ARM64)
# Python version  : 3.12 (matches Pi 5 OS default, supports asyncio features)
#
# Build stages:
#   1. builder  — installs all Python dependencies into /install
#   2. runtime  — minimal image, copies only the installed packages and app
#
# The two-stage build keeps the final image small by excluding pip, build
# tools, and compiler toolchain from the runtime layer.
#
# Build command (from project root):
#   docker build -t dirigera-mqtt-bridge:latest .
#
# Run command (Docker Compose handles this — see docker-compose.yml):
#   docker run --rm --env-file .env dirigera-mqtt-bridge:latest
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: builder ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

# Build-time metadata
LABEL stage="builder"

# Prevent Python from writing .pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# WORKDIR /build
WORKDIR /app

# Install system build dependencies needed to compile any C extensions
# (e.g. aiohttp uses optional C accelerators on ARM64)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy only dependency files first to maximise Docker layer cache hits.
# requirements.txt changes less often than application code.
COPY requirements.txt .

# Install all Python dependencies into a dedicated prefix so they can be
# copied cleanly to the runtime stage without pip itself.
RUN pip install --prefix=/install --no-cache-dir -r requirements.txt

# ha_mqtt_sdk is installed from PyPI via requirements.txt above.
# No local sdk_src/ directory needed


# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Runtime metadata
LABEL maintainer="Dick Kouwenhoven" \
      description="Dirigera ↔ Home Assistant MQTT Bridge" \
      org.opencontainers.image.title="dirigera-mqtt-bridge" \
      org.opencontainers.image.version="1.0.0"

# Runtime environment
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH="/install/bin:$PATH"

# Create a non-root user for security.
# Running as root inside a container is unnecessary and increases attack surface.
RUN groupadd --gid 1001 bridge && \
    useradd --uid 1001 --gid bridge --shell /bin/bash --create-home bridge

WORKDIR /app

# Copy installed Python packages from builder stage
COPY --from=builder /install /usr/local

# Copy application source
COPY --chown=bridge:bridge app/     ./app/
COPY --chown=bridge:bridge main.py  ./main.py

# Switch to non-root user
USER bridge

# Health check — verify the Python environment is functional.
# Does not check network connectivity (the orchestrator handles reconnect).
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import ha_mqtt_sdk, app.config; print('ok')" || exit 1

# The .env file is NOT copied into the image — it is mounted at runtime
# via docker-compose env_file directive. This prevents secrets from being
# baked into the image layer history.

# Entrypoint — run main.py directly via Python
CMD ["python", "main.py"]
