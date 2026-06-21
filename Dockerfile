# ─────────────────────────────────────────────────────────────
# YouTube Music Streaming API — Python FastAPI
# Hugging Face Spaces Docker (port 7860)
# ─────────────────────────────────────────────────────────────

FROM python:3.12-slim

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        ca-certificates \
        gcc \
        g++ \
        libssl-dev \
        libffi-dev \
        python3-dev \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Force install critical runtime deps BEFORE COPY ──────────
# This is a new layer not present in any prior build cache.
# Everything after this line will be rebuilt fresh.
RUN pip install --no-cache-dir "redis>=5.0.0" "curl-cffi>=0.7.4"

# ── Install remaining Python deps ─────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir --prefer-binary -r requirements.txt

# ── Copy source ───────────────────────────────────────────────
COPY src/ src/
COPY main.py .

# ── Runtime config ───────────────────────────────────────────
ENV PYTHONPATH=src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    API_PORT=7860 \
    YTDL_CLIENT="" \
    YTDL_NO_COOKIES=true \
    YTDL_VERIFY_SSL=false \
    RATE_LIMIT_ENABLED=false \
    REDIS_ENABLED=false

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -sf http://93.115.101.146:15394/health || exit 1

CMD ["uvicorn", "main:app", \
     "--host", "93.115.101.146", \
     "--port", "15394", \
     "--workers", "1", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*"]