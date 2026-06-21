"""Docs and setup page redirects."""

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

router = APIRouter(tags=["Docs"])


@router.get("/docs")
async def docs_redirect():
    """Redirect /docs ke Swagger UI."""
    return RedirectResponse(url="/docs")


@router.get("/setup", include_in_schema=False)
async def setup_redirect():
    """Shortcut: /setup → /oauth/device/setup (interactive OAuth setup page)."""
    return RedirectResponse(url="/oauth/device/setup")
