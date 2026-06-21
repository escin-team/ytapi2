#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# YouTube Music API — One-command setup
# Usage:  bash setup.sh
# ─────────────────────────────────────────────────────────────
set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[✓]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[✗]${NC} $*"; exit 1; }

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  YouTube Music Streaming API — Setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── Check requirements ─────────────────────────────────────────
command -v docker         >/dev/null 2>&1 || error "Docker not found. Install: https://docs.docker.com/get-docker/"
command -v docker compose >/dev/null 2>&1 || \
  command -v docker-compose >/dev/null 2>&1 || \
  error "docker compose not found. Update Docker Desktop or install docker-compose."

info "Docker found: $(docker --version)"

# ── Create .env if missing ─────────────────────────────────────
if [ ! -f .env ]; then
    cp .env.example .env
    warn ".env created from .env.example"
    warn "Edit .env and fill in SESSION_SECRET, then re-run this script"
    warn "  Or add Cloudinary/OAuth keys via the Admin Dashboard after start"
else
    info ".env already exists"
fi

# ── Generate SESSION_SECRET if empty ──────────────────────────
if grep -q "^SESSION_SECRET=$" .env || grep -q "^SESSION_SECRET=change-me" .env; then
    SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || \
             openssl rand -hex 32 2>/dev/null || \
             cat /dev/urandom | tr -dc 'a-f0-9' | head -c 64)
    sed -i "s|^SESSION_SECRET=.*|SESSION_SECRET=${SECRET}|" .env
    info "Generated random SESSION_SECRET"
fi

# ── Pull images (optional speedup) ────────────────────────────
echo ""
info "Pulling base images…"
docker pull python:3.12-slim --quiet && \
docker pull oven/bun:1-slim --quiet  && \
docker pull nginx:1.27-alpine --quiet || warn "Image pull failed, will try during build"

# ── Build & start ─────────────────────────────────────────────
echo ""
info "Building images…"
docker compose build --parallel

echo ""
info "Starting services…"
docker compose up -d

# ── Wait for healthy ──────────────────────────────────────────
echo ""
warn "Waiting for services to be healthy…"
for i in $(seq 1 30); do
    HEALTH=$(docker compose ps --format json 2>/dev/null | \
             python3 -c "import sys,json; data=[json.loads(l) for l in sys.stdin if l.strip()]; \
             unhealthy=[s['Name'] for s in data if s.get('Health','') not in ('healthy','')]; \
             print(len(unhealthy))" 2>/dev/null || echo "?")
    if [ "$HEALTH" = "0" ]; then
        break
    fi
    sleep 2
    printf "."
done
echo ""

# ── Done ──────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
info "Setup complete!"
echo ""
echo "  🌐 API:       http://localhost"
echo "  🔑 Admin:     http://localhost/admin   (default PIN: 27122002)"
echo "  📄 Swagger:   http://localhost/docs"
echo "  ❤️  Health:    http://localhost/health"
echo ""
echo "  First login → Admin → Cloudinary → tambah akun"
echo "  First login → Admin → OAuth Setup → import JSON"
echo ""
echo "  Logs:   docker compose logs -f"
echo "  Stop:   docker compose down"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
