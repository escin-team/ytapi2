"""Main FastAPI application."""

import time
import logging
import pathlib
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from youtube_search.config import get_settings
from youtube_search.api.v1 import search, download, playlist, docs
from youtube_search.api.v1.search_prefetch import router as prefetch_router
from youtube_search.api.v1 import oauth
from youtube_search.admin import router as admin_router
from youtube_search.admin import db as admin_db
from youtube_search.mcp.router import router as mcp_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

settings = get_settings()

app = FastAPI(
    title="YouTube Music Streaming API",
    description=(
        "Zero-cost YouTube music streaming API.  "
        "**Admin dashboard:** [/admin](/admin) · "
        "**OAuth setup:** [/oauth/device/setup](/oauth/device/setup)"
    ),
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Whitelist + analytics middleware ──────────────────────────────────────────

_API_PREFIX = "/api/"

@app.middleware("http")
async def whitelist_and_track(request: Request, call_next):
    start = time.monotonic()

    # ── Domain whitelist (only enforced on /api/* routes) ──────────────────
    if request.url.path.startswith(_API_PREFIX):
        enabled = await admin_db.get_enabled_domains()
        if enabled:  # empty set → open access
            origin_header = (
                request.headers.get("origin", "")
                or request.headers.get("referer", "")
            )
            import urllib.parse
            raw = origin_header.strip().rstrip("/")
            try:
                parsed = urllib.parse.urlparse(raw)
                origin_host = parsed.netloc or parsed.path
            except Exception:
                origin_host = raw

            origin_host = origin_host.lower().split(":")[0]

            if origin_host and origin_host not in enabled:
                response = JSONResponse(
                    status_code=403,
                    content={
                        "detail": (
                            f"Origin '{origin_host}' is not in the API whitelist. "
                            "Contact the admin to add your domain."
                        )
                    },
                )
                elapsed = int((time.monotonic() - start) * 1000)
                await admin_db.record_request(
                    request.method, request.url.path,
                    403, origin_host, elapsed,
                )
                return response

    # ── Forward request ────────────────────────────────────────────────────
    response = await call_next(request)
    elapsed = int((time.monotonic() - start) * 1000)

    path = request.url.path
    if path.startswith(_API_PREFIX) or path.startswith("/oauth/"):
        origin = (
            request.headers.get("origin", "")
            or request.headers.get("referer", "")
        ).strip()
        import urllib.parse
        try:
            parsed = urllib.parse.urlparse(origin)
            origin_host = parsed.netloc or parsed.path or ""
        except Exception:
            origin_host = ""

        try:
            await admin_db.record_request(
                request.method, path,
                response.status_code,
                origin_host.lower().split(":")[0],
                elapsed,
            )
        except Exception:
            pass

    return response


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "youtube-search-api",
        "version": "1.0.0",
        "port": settings.api_port,
        "cache": "in-memory" if not settings.redis_enabled else "redis",
        "cloudinary_accounts": len(settings.cloudinary_accounts)
    }

@app.get("/")
async def root():
    return {
        "message": "YouTube Music Streaming API",
        "docs": "/docs",
        "admin": "/admin",
        "health": "/health",
        "oauth_setup": "/oauth/device/setup",
        "mcp": "/mcp/health",
    }

app.include_router(search.router)
app.include_router(prefetch_router)
app.include_router(download.router)
app.include_router(playlist.router)
app.include_router(docs.router)
app.include_router(oauth.router)
app.include_router(admin_router.router)
app.include_router(mcp_router)

# ── Local file storage (fallback when Cloudinary is not configured) ───────────
_LOCAL_FILES_DIR = pathlib.Path(__file__).parent / "local_files"
_LOCAL_FILES_DIR.mkdir(exist_ok=True)
app.mount("/local", StaticFiles(directory=str(_LOCAL_FILES_DIR)), name="local_files")


# ── Startup ───────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup_event():
    await admin_db.init_db()
    logger.info("Admin DB initialised at %s", admin_db.DB_PATH)
    logger.info("Starting YouTube Music API on port %s", settings.api_port)
    logger.info("Redis enabled: %s", settings.redis_enabled)
    logger.info("Cloudinary accounts: %d", len(settings.cloudinary_accounts))
    logger.info("Download timeout: %s seconds", settings.download_timeout)
    logger.info("Admin dashboard: /admin  (default PIN: 27122002)")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=settings.api_host, port=settings.api_port, reload=False)
