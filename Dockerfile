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

# ── Install Python deps ───────────────────────────────────────
COPY requirements.txt .

# Install curl-cffi first (C extension, needs build deps above)
# --prefer-binary pakai pre-built wheel jika tersedia (lebih cepat & aman)
RUN pip install --no-cache-dir --prefer-binary "curl-cffi>=0.7.4" || \
    echo "curl-cffi optional — lanjut tanpa impersonation"

# Install semua deps dari requirements.txt
RUN pip install --no-cache-dir --prefer-binary -r requirements.txt

# ── Copy source ───────────────────────────────────────────────
COPY src/ src/
COPY main.py .

# ── Runtime config ───────────────────────────────────────────
ENV PYTHONPATH=src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    API_PORT=8080 \
    YTDL_CLIENT="" \
    YTDL_NO_COOKIES=true \
    YTDL_VERIFY_SSL=false \
    RATE_LIMIT_ENABLED=false \
    REDIS_ENABLED=false

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -sf http://localhost:7860/health || exit 1

CMD ["uvicorn", "main:app", \
     "--host", "0.0.0.0", \
     "--port", "8080", \
     "--workers", "1", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*"]
