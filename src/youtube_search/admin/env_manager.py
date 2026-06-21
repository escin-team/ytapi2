"""
Safe .env file read/write + settings cache invalidation.

Rules:
- Never log or expose raw values of api_secret / api_key in server logs.
- Write atomically: write to .env.tmp then rename, so a crash mid-write
  never corrupts the file.
- After any write, clear the pydantic-settings lru_cache so the next
  call to get_settings() picks up the new values without a server restart.
"""

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

ENV_PATH = Path(os.getenv("DOTENV_PATH", ".env"))


# ── Low-level .env helpers ────────────────────────────────────────────────────

def _read_env_lines() -> list[str]:
    if not ENV_PATH.exists():
        return []
    return ENV_PATH.read_text(encoding="utf-8").splitlines(keepends=True)


def _set_env_key(key: str, value: str) -> None:
    """Insert or replace KEY=value in .env.  Writes atomically."""
    lines = _read_env_lines()
    pattern = re.compile(rf"^{re.escape(key)}\s*=", re.IGNORECASE)
    new_line = f'{key}={value}\n'
    replaced = False
    new_lines = []
    for line in lines:
        if pattern.match(line):
            new_lines.append(new_line)
            replaced = True
        else:
            new_lines.append(line)
    if not replaced:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines.append("\n")
        new_lines.append(new_line)

    _atomic_write(ENV_PATH, "".join(new_lines))


def _get_env_key(key: str) -> str | None:
    """Read a single key from .env (does NOT override os.environ)."""
    pattern = re.compile(rf"^{re.escape(key)}\s*=\s*(.*)$", re.IGNORECASE)
    for line in _read_env_lines():
        m = pattern.match(line.rstrip())
        if m:
            val = m.group(1).strip()
            # Strip surrounding quotes
            if (val.startswith('"') and val.endswith('"')) or \
               (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            return val
    return None


def _atomic_write(path: Path, content: str) -> None:
    """Write content to a temp file next to path, then rename."""
    tmp = path.with_suffix(".env.tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


# ── Settings cache invalidation ───────────────────────────────────────────────

def _reload_settings() -> None:
    """Clear the lru_cache so the next get_settings() reads .env fresh."""
    try:
        from youtube_search.config import get_settings
        get_settings.cache_clear()
    except Exception:
        pass


# ── Cloudinary account management ─────────────────────────────────────────────

def _load_accounts() -> list[dict]:
    """Load accounts from os.environ first, fall back to .env file."""
    raw = os.environ.get("CLOUDINARY_ACCOUNTS_JSON") or _get_env_key("CLOUDINARY_ACCOUNTS_JSON") or "[]"
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_accounts(accounts: list[dict]) -> None:
    """Persist accounts to .env and reload settings + os.environ."""
    serialized = json.dumps(accounts, separators=(",", ":"))
    # Quote the JSON so .env parsers don't choke on special chars
    quoted = f'"{serialized}"'
    _set_env_key("CLOUDINARY_ACCOUNTS_JSON", quoted)
    # Keep os.environ in sync so pydantic-settings sees the new value
    os.environ["CLOUDINARY_ACCOUNTS_JSON"] = serialized
    _reload_settings()


# ── Public API ────────────────────────────────────────────────────────────────

def list_accounts() -> list[dict]:
    """Return accounts with api_secret masked for display."""
    accounts = _load_accounts()
    result = []
    for i, acc in enumerate(accounts):
        result.append({
            "index": i,
            "cloud_name": acc.get("cloud_name", ""),
            "api_key": acc.get("api_key", ""),
            "api_secret_masked": _mask(acc.get("api_secret", "")),
            "has_secret": bool(acc.get("api_secret", "")),
        })
    return result


def add_account(cloud_name: str, api_key: str, api_secret: str) -> dict:
    """Add a new Cloudinary account.  Raises ValueError on duplicate cloud_name."""
    cloud_name = cloud_name.strip()
    api_key = api_key.strip()
    api_secret = api_secret.strip()
    if not all([cloud_name, api_key, api_secret]):
        raise ValueError("cloud_name, api_key, and api_secret are all required")
    accounts = _load_accounts()
    if any(a.get("cloud_name") == cloud_name for a in accounts):
        raise ValueError(f"Account '{cloud_name}' already exists")
    accounts.append({"cloud_name": cloud_name, "api_key": api_key, "api_secret": api_secret})
    _save_accounts(accounts)
    return {"cloud_name": cloud_name, "api_key": api_key, "api_secret_masked": _mask(api_secret)}


def update_account(index: int, cloud_name: str, api_key: str, api_secret: str | None) -> dict:
    """Update an existing account by index.  Pass api_secret=None to keep existing."""
    accounts = _load_accounts()
    if index < 0 or index >= len(accounts):
        raise IndexError(f"No account at index {index}")
    acc = accounts[index]
    acc["cloud_name"] = cloud_name.strip() or acc["cloud_name"]
    acc["api_key"] = api_key.strip() or acc["api_key"]
    if api_secret and api_secret.strip():
        acc["api_secret"] = api_secret.strip()
    accounts[index] = acc
    _save_accounts(accounts)
    return {"cloud_name": acc["cloud_name"], "api_key": acc["api_key"],
            "api_secret_masked": _mask(acc["api_secret"])}


def remove_account(index: int) -> str:
    """Remove account by index.  Returns the cloud_name that was removed."""
    accounts = _load_accounts()
    if index < 0 or index >= len(accounts):
        raise IndexError(f"No account at index {index}")
    removed = accounts.pop(index)
    _save_accounts(accounts)
    return removed.get("cloud_name", "")


def reorder_accounts(new_order: list[int]) -> list[dict]:
    """Reorder accounts by specifying new index order."""
    accounts = _load_accounts()
    if sorted(new_order) != list(range(len(accounts))):
        raise ValueError("new_order must be a permutation of existing indices")
    reordered = [accounts[i] for i in new_order]
    _save_accounts(reordered)
    return list_accounts()


def test_account(index: int) -> dict:
    """Ping Cloudinary API to verify credentials.  Returns {ok, message}."""
    accounts = _load_accounts()
    if index < 0 or index >= len(accounts):
        raise IndexError(f"No account at index {index}")
    acc = accounts[index]
    try:
        import cloudinary
        import cloudinary.api
        cloudinary.config(
            cloud_name=acc["cloud_name"],
            api_key=acc["api_key"],
            api_secret=acc["api_secret"],
        )
        result = cloudinary.api.ping()
        return {"ok": True, "message": f"Connected ✓ (status: {result.get('status', 'ok')})"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


def _mask(secret: str) -> str:
    """Show first 4 chars then *** — enough to identify, not enough to steal."""
    if not secret:
        return ""
    visible = secret[:4]
    return f"{visible}{'*' * min(8, len(secret) - 4)}"
