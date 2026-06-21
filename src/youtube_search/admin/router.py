"""Admin dashboard routes — login, dashboard, and JSON API endpoints."""

import asyncio
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Cookie, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from youtube_search.admin import auth, db

router = APIRouter(tags=["Admin"])

# ── Startup hook (call from main.py) ─────────────────────────────────────────

async def startup() -> None:
    await db.init_db()

# ── Auth helpers ─────────────────────────────────────────────────────────────

def _is_logged_in(request: Request) -> bool:
    token = request.cookies.get(auth.COOKIE_NAME)
    return auth.validate_session_cookie(token)

def _require_login(request: Request):
    if not _is_logged_in(request):
        raise HTTPException(status_code=302, headers={"Location": "/login"})

# ── Login page ────────────────────────────────────────────────────────────────

_LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Admin Login</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  background:#0b0d14;color:#e2e8f0;min-height:100vh;
  display:flex;align-items:center;justify-content:center}
.card{background:#13151f;border:1px solid #1e2235;border-radius:16px;
  padding:44px 40px;width:100%;max-width:400px;
  box-shadow:0 24px 64px rgba(0,0,0,.6)}
.logo{display:flex;align-items:center;gap:12px;margin-bottom:32px}
.logo-icon{width:42px;height:42px;border-radius:10px;
  background:linear-gradient(135deg,#ff4040,#c00);
  display:flex;align-items:center;justify-content:center;font-size:20px}
.logo-text{font-size:18px;font-weight:700;color:#fff}
.logo-sub{font-size:12px;color:#475569}
h1{font-size:22px;font-weight:700;color:#fff;margin-bottom:6px}
.sub{font-size:14px;color:#64748b;margin-bottom:28px}
label{display:block;font-size:13px;color:#94a3b8;margin-bottom:6px;font-weight:500}
input[type=password]{width:100%;padding:12px 14px;background:#0b0d14;
  border:1px solid #1e2235;border-radius:8px;color:#fff;font-size:22px;
  letter-spacing:6px;outline:none;transition:border .15s}
input[type=password]:focus{border-color:#3b82f6}
input[type=password]::placeholder{letter-spacing:1px;font-size:14px;color:#334155}
.btn{width:100%;padding:13px;border-radius:8px;border:none;
  background:linear-gradient(135deg,#3b82f6,#2563eb);
  color:#fff;font-size:15px;font-weight:600;cursor:pointer;margin-top:20px;
  transition:opacity .15s}
.btn:hover{opacity:.85}
.error{background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.3);
  color:#f87171;border-radius:8px;padding:10px 14px;font-size:13px;margin-top:14px}
.hint{font-size:11px;color:#334155;text-align:center;margin-top:16px}
</style>
</head>
<body>
<div class="card">
  <div class="logo">
    <div class="logo-icon">▶</div>
    <div>
      <div class="logo-text">YouTube Music API</div>
      <div class="logo-sub">Admin Dashboard</div>
    </div>
  </div>
  <h1>Sign In</h1>
  <p class="sub">Enter your admin PIN to continue.</p>
  <form method="post" action="/login">
    <label for="pin">Admin PIN</label>
    <input id="pin" name="pin" type="password" placeholder="Enter PIN" autofocus autocomplete="current-password"/>
    %ERROR%
    <button class="btn" type="submit">Continue →</button>
  </form>
  <p class="hint">Session expires after 8 hours.</p>
</div>
</body>
</html>"""


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page(request: Request):
    if _is_logged_in(request):
        return RedirectResponse("/admin", status_code=302)
    return HTMLResponse(_LOGIN_HTML.replace("%ERROR%", ""))


@router.post("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_submit(request: Request, pin: str = Form(...)):
    stored = await db.get_pin_hash()
    if not db.verify_pin(pin, stored):
        error = '<div class="error">Incorrect PIN. Please try again.</div>'
        return HTMLResponse(_LOGIN_HTML.replace("%ERROR%", error), status_code=401)
    response = RedirectResponse("/admin", status_code=302)
    token = auth.create_session_cookie()
    response.set_cookie(
        auth.COOKIE_NAME, token,
        httponly=True, samesite="strict", max_age=8 * 3600,
    )
    return response


@router.get("/logout", include_in_schema=False)
async def logout():
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie(auth.COOKIE_NAME)
    return response


# ── Dashboard HTML ────────────────────────────────────────────────────────────

_DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Admin Dashboard</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0b0d14;--surface:#13151f;--border:#1e2235;--text:#e2e8f0;
  --sub:#64748b;--accent:#3b82f6;--green:#22c55e;--red:#ef4444;--yellow:#eab308;
}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  background:var(--bg);color:var(--text);display:flex;min-height:100vh}

/* ── Sidebar ── */
.sidebar{width:220px;flex-shrink:0;background:var(--surface);
  border-right:1px solid var(--border);display:flex;flex-direction:column;
  padding:24px 0;position:fixed;top:0;left:0;height:100vh;overflow-y:auto}
.sidebar-logo{display:flex;align-items:center;gap:10px;padding:0 20px;margin-bottom:28px}
.logo-icon{width:36px;height:36px;border-radius:8px;
  background:linear-gradient(135deg,#ff4040,#c00);
  display:flex;align-items:center;justify-content:center;font-size:17px;flex-shrink:0}
.logo-text{font-size:15px;font-weight:700}
.logo-sub{font-size:10px;color:var(--sub)}
nav a{display:flex;align-items:center;gap:10px;padding:10px 20px;
  font-size:14px;color:var(--sub);text-decoration:none;border-radius:0;
  transition:background .1s,color .1s;cursor:pointer}
nav a:hover,nav a.active{background:rgba(59,130,246,.1);color:var(--accent)}
nav a .icon{font-size:16px;width:20px;text-align:center}
.sidebar-sep{height:1px;background:var(--border);margin:12px 16px}
.sidebar-bottom{margin-top:auto;padding:16px}
.btn-logout{width:100%;padding:9px;border-radius:8px;border:1px solid var(--border);
  background:transparent;color:var(--sub);font-size:13px;cursor:pointer;
  transition:border .15s,color .15s}
.btn-logout:hover{border-color:var(--red);color:var(--red)}

/* ── Main ── */
.main{margin-left:220px;flex:1;padding:32px;max-width:1100px}
.page{display:none}.page.active{display:block}
.page-title{font-size:22px;font-weight:700;margin-bottom:4px}
.page-sub{font-size:14px;color:var(--sub);margin-bottom:28px}

/* ── Cards ── */
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:16px;margin-bottom:28px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:20px}
.card-label{font-size:12px;color:var(--sub);text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px}
.card-value{font-size:28px;font-weight:700}
.card-sub{font-size:12px;color:var(--sub);margin-top:4px}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:6px}
.dot-green{background:var(--green);box-shadow:0 0 6px var(--green)}
.dot-red{background:var(--red);box-shadow:0 0 6px var(--red)}
.dot-yellow{background:var(--yellow);box-shadow:0 0 6px var(--yellow)}

/* ── Table section ── */
.section{background:var(--surface);border:1px solid var(--border);border-radius:12px;margin-bottom:24px}
.section-head{display:flex;align-items:center;justify-content:space-between;
  padding:18px 20px;border-bottom:1px solid var(--border)}
.section-title{font-size:15px;font-weight:600}
table{width:100%;border-collapse:collapse}
th{padding:10px 16px;text-align:left;font-size:12px;color:var(--sub);
  text-transform:uppercase;letter-spacing:.04em;background:rgba(255,255,255,.02)}
td{padding:12px 16px;font-size:13px;border-top:1px solid var(--border);vertical-align:middle}
tr:hover td{background:rgba(255,255,255,.02)}
.badge{display:inline-flex;align-items:center;padding:3px 10px;border-radius:999px;font-size:12px;font-weight:500}
.badge-green{background:rgba(34,197,94,.12);color:var(--green);border:1px solid rgba(34,197,94,.25)}
.badge-red{background:rgba(239,68,68,.12);color:var(--red);border:1px solid rgba(239,68,68,.25)}
.badge-blue{background:rgba(59,130,246,.12);color:var(--accent);border:1px solid rgba(59,130,246,.25)}
.badge-yellow{background:rgba(234,179,8,.12);color:var(--yellow);border:1px solid rgba(234,179,8,.25)}
.btn-icon{background:transparent;border:1px solid var(--border);border-radius:6px;
  padding:5px 10px;color:var(--sub);cursor:pointer;font-size:12px;transition:.15s}
.btn-icon:hover{border-color:var(--red);color:var(--red)}
.btn-icon.toggle:hover{border-color:var(--accent);color:var(--accent)}

/* ── Add domain form ── */
.add-form{display:flex;gap:10px;padding:16px 20px;border-top:1px solid var(--border)}
.add-form input{flex:1;padding:9px 12px;background:var(--bg);border:1px solid var(--border);
  border-radius:8px;color:var(--text);font-size:13px;outline:none}
.add-form input:focus{border-color:var(--accent)}
.add-form .note{width:180px}
.btn-add{padding:9px 18px;border-radius:8px;border:none;
  background:var(--accent);color:#fff;font-size:13px;font-weight:600;cursor:pointer;white-space:nowrap}
.btn-add:hover{opacity:.85}

/* ── Analytics log ── */
.log-row-2xx td{color:#94a3b8}
.log-row-4xx td,.log-row-5xx td{color:#fca5a5}
.mono{font-family:"SF Mono","Fira Code",monospace;font-size:12px}

/* ── OAuth card ── */
.oauth-card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:24px;margin-bottom:24px;display:flex;align-items:center;justify-content:space-between;gap:20px;flex-wrap:wrap}
.oauth-info{flex:1}
.oauth-title{font-size:15px;font-weight:600;margin-bottom:6px}
.oauth-sub{font-size:13px;color:var(--sub);line-height:1.5}
.btn-primary{padding:10px 20px;border-radius:8px;border:none;
  background:linear-gradient(135deg,#3b82f6,#2563eb);
  color:#fff;font-size:14px;font-weight:600;cursor:pointer;white-space:nowrap;text-decoration:none;display:inline-block}
.btn-primary:hover{opacity:.85}

/* ── Change PIN ── */
.pin-form{display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end}
.pin-form input{padding:9px 12px;background:var(--bg);border:1px solid var(--border);
  border-radius:8px;color:var(--text);font-size:14px;outline:none;width:180px}
.pin-form input:focus{border-color:var(--accent)}
.toast{position:fixed;bottom:24px;right:24px;background:#22c55e;color:#fff;
  padding:12px 20px;border-radius:10px;font-size:14px;font-weight:500;
  box-shadow:0 8px 24px rgba(0,0,0,.4);transform:translateY(80px);opacity:0;
  transition:transform .3s,opacity .3s;z-index:999}
.toast.show{transform:translateY(0);opacity:1}
.toast.error{background:var(--red)}
.empty{padding:32px;text-align:center;color:var(--sub);font-size:13px}
.spinner{display:inline-block;animation:spin .7s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
</style>
</head>
<body>

<!-- ── Sidebar ── -->
<aside class="sidebar">
  <div class="sidebar-logo">
    <div class="logo-icon">▶</div>
    <div>
      <div class="logo-text">YT Music API</div>
      <div class="logo-sub">Admin Panel</div>
    </div>
  </div>
  <nav>
    <a class="active" onclick="showPage('status')"    id="nav-status">    <span class="icon">📊</span> Server Status</a>
    <a             onclick="showPage('whitelist')"  id="nav-whitelist">  <span class="icon">🛡️</span> Whitelist</a>
    <a             onclick="showPage('analytics')"  id="nav-analytics">  <span class="icon">📈</span> Analytics</a>
    <a             onclick="showPage('oauth')"      id="nav-oauth">      <span class="icon">🔑</span> OAuth Setup</a>
    <a             onclick="showPage('cloudinary')" id="nav-cloudinary">  <span class="icon">☁️</span> Cloudinary</a>
    <a             onclick="showPage('tester')"     id="nav-tester">     <span class="icon">🧪</span> API Tester</a>
    <a             onclick="showPage('settings')"   id="nav-settings">   <span class="icon">⚙️</span> Settings</a>
    <div class="sidebar-sep"></div>
    <a href="/docs" target="_blank"><span class="icon">📄</span> Swagger Docs</a>
    <a href="/health" target="_blank"><span class="icon">❤️</span> Health JSON</a>
  </nav>
  <div class="sidebar-bottom">
    <button class="btn-logout" onclick="location.href='/logout'">← Sign Out</button>
  </div>
</aside>

<!-- ── Main ── -->
<main class="main">

  <!-- STATUS PAGE -->
  <div id="page-status" class="page active">
    <div class="page-title">Server Status</div>
    <div class="page-sub">Live health and configuration overview.</div>
    <div id="statusCards" class="cards">
      <div class="card"><div class="card-label">Loading…</div></div>
    </div>
    <div class="section" style="margin-bottom:24px">
      <div class="section-head"><span class="section-title">Service Details</span>
        <button class="btn-icon" onclick="loadStatus()">↻ Refresh</button>
      </div>
      <table><thead><tr><th>Key</th><th>Value</th></tr></thead>
      <tbody id="statusDetails"><tr><td colspan="2" class="empty">Loading…</td></tr></tbody>
      </table>
    </div>
  </div>

  <!-- WHITELIST PAGE -->
  <div id="page-whitelist" class="page">
    <div class="page-title">Domain Whitelist</div>
    <div class="page-sub">Only listed domains can call <code>/api/*</code>. Empty list = open access (all origins allowed).</div>
    <div class="section">
      <div class="section-head">
        <span class="section-title">Allowed Domains</span>
        <span id="whitelistMode" class="badge badge-yellow">Loading…</span>
      </div>
      <table><thead><tr><th>Domain</th><th>Note</th><th>Added</th><th>Status</th><th>Actions</th></tr></thead>
      <tbody id="whitelistTable"><tr><td colspan="5" class="empty"><span class="spinner">⟳</span> Loading…</td></tr></tbody>
      </table>
      <div class="add-form">
        <input id="newDomain" placeholder="example.com or https://example.com" />
        <input id="newNote" class="note" placeholder="Note (optional)" />
        <button class="btn-add" onclick="addDomain()">+ Add Domain</button>
      </div>
    </div>
  </div>

  <!-- ANALYTICS PAGE -->
  <div id="page-analytics" class="page">
    <div class="page-title">Analytics</div>
    <div class="page-sub">API request log and statistics.</div>
    <div id="analyticsCards" class="cards">
      <div class="card"><div class="card-label">Loading…</div></div>
    </div>
    <div class="section" style="margin-bottom:24px">
      <div class="section-head">
        <span class="section-title">Top Endpoints</span>
        <button class="btn-icon" onclick="loadAnalytics()">↻ Refresh</button>
      </div>
      <table><thead><tr><th>Path</th><th>Requests</th></tr></thead>
      <tbody id="topPaths"><tr><td colspan="2" class="empty">Loading…</td></tr></tbody>
      </table>
    </div>
    <div class="section">
      <div class="section-head"><span class="section-title">Recent Requests (last 50)</span></div>
      <table><thead><tr><th>Time</th><th>Method</th><th>Path</th><th>Status</th><th>Origin</th><th>ms</th></tr></thead>
      <tbody id="recentLog"><tr><td colspan="6" class="empty">Loading…</td></tr></tbody>
      </table>
    </div>
  </div>

  <!-- OAUTH PAGE -->
  <div id="page-oauth" class="page">
    <div class="page-title">OAuth Setup</div>
    <div class="page-sub">Connect a Google account so the API can bypass YouTube rate limits.</div>
    <div id="oauthStatus" class="oauth-card">
      <div class="oauth-info"><div class="oauth-title"><span class="spinner">⟳</span> Checking status…</div></div>
    </div>
    <div class="oauth-card">
      <div class="oauth-info">
        <div class="oauth-title">🔑 Connect with Google (Device Flow)</div>
        <div class="oauth-sub">No redirect required — enter a short code on Google's site. Works headlessly.</div>
      </div>
      <a href="/oauth/device/setup" target="_blank" class="btn-primary">Open Setup UI →</a>
    </div>
    <div class="oauth-card">
      <div class="oauth-info">
        <div class="oauth-title">📄 Swagger OAuth Endpoints</div>
        <div class="oauth-sub">Use <code>POST /oauth/device/start</code>, poll with <code>POST /oauth/device/poll</code>, check with <code>GET /oauth/device/status</code>.</div>
      </div>
      <a href="/docs#/OAuth%202.0%20%E2%80%94%20Device%20Code%20Flow" target="_blank" class="btn-primary">View in Docs →</a>
    </div>
    <!-- Google OAuth JSON import -->
    <div class="section" style="margin-top:20px">
      <div class="section-head">
        <span class="section-title">📥 Import Google OAuth Credentials</span>
        <a href="https://console.cloud.google.com/apis/credentials" target="_blank" style="font-size:12px;color:var(--accent);text-decoration:none">Google Console ↗</a>
      </div>
      <div style="padding:16px 20px">
        <div id="oauthCredsStatus" style="margin-bottom:14px"></div>
        <div style="font-size:13px;color:var(--sub);margin-bottom:12px;line-height:1.6">
          Download <strong>client_secret_*.json</strong> dari Google Cloud Console (tipe <em>TV and Limited Input devices</em>),
          lalu drag &amp; drop atau paste JSON-nya di bawah. <code>client_id</code> dan <code>client_secret</code>
          otomatis tersimpan ke <code>.env</code>.
        </div>
        <!-- Drop zone -->
        <div id="oauthDropZone"
          ondragover="event.preventDefault();this.style.borderColor='var(--accent)'"
          ondragleave="this.style.borderColor='var(--border)'"
          ondrop="handleOauthDrop(event)"
          style="border:2px dashed var(--border);border-radius:10px;padding:28px;text-align:center;cursor:pointer;transition:border-color .2s;margin-bottom:12px"
          onclick="document.getElementById('oauthFileInput').click()">
          <div style="font-size:28px;margin-bottom:8px">📂</div>
          <div style="font-size:13px;color:var(--sub)">Drag &amp; drop <strong>client_secret_*.json</strong> di sini<br/>atau klik untuk pilih file</div>
          <input id="oauthFileInput" type="file" accept="*" style="display:none" onchange="handleOauthFile(event)"/>
        </div>
        <!-- Paste JSON textarea -->
        <div style="margin-bottom:12px">
          <label style="display:block;font-size:12px;color:var(--sub);margin-bottom:6px">Atau paste JSON langsung:</label>
          <textarea id="oauthJsonPaste" rows="5" placeholder='{"installed":{"client_id":"...","client_secret":"...",...}}'
            style="width:100%;padding:10px 12px;background:var(--bg);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:12px;font-family:monospace;resize:vertical;outline:none;box-sizing:border-box"></textarea>
        </div>
        <div style="display:flex;gap:10px">
          <button class="btn-add" onclick="importOauthJson()" style="padding:10px 24px">⬆ Import &amp; Simpan ke .env</button>
          <button class="btn-icon" onclick="loadOauthCreds()" style="padding:10px 18px">↻ Cek Status</button>
        </div>
        <div id="oauthImportResult" style="margin-top:12px"></div>
      </div>
    </div>

    <div class="section">
      <div class="section-head"><span class="section-title">Token Actions</span></div>
      <div style="padding:20px;display:flex;gap:12px;flex-wrap:wrap">
        <button class="btn-primary" onclick="checkOAuth()">↻ Refresh Status</button>
        <button class="btn-icon" style="padding:10px 18px" onclick="revokeOAuth()">🗑 Revoke Token</button>
      </div>
    </div>
  </div>

  <!-- CLOUDINARY PAGE -->
  <div id="page-cloudinary" class="page">
    <div class="page-title">Cloudinary Accounts</div>
    <div class="page-sub">
      Audio files are uploaded here. Multiple accounts = automatic failover.
      Changes are saved to <code>.env</code> and detected by the server instantly — no restart needed.
    </div>

    <!-- Account cards -->
    <div id="cloudinaryList"></div>

    <!-- Add account form -->
    <div class="section">
      <div class="section-head">
        <span class="section-title">➕ Add New Account</span>
        <a href="https://cloudinary.com/console" target="_blank" style="font-size:12px;color:var(--accent);text-decoration:none">Open Cloudinary Console ↗</a>
      </div>
      <div style="padding:20px;display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px">
        <div>
          <label style="display:block;font-size:12px;color:var(--sub);margin-bottom:6px">Cloud Name *</label>
          <input id="cld-cloud" placeholder="my-cloud-name" style="width:100%;padding:9px 12px;background:var(--bg);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:13px;outline:none"/>
        </div>
        <div>
          <label style="display:block;font-size:12px;color:var(--sub);margin-bottom:6px">API Key *</label>
          <input id="cld-key" placeholder="123456789012345" style="width:100%;padding:9px 12px;background:var(--bg);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:13px;outline:none"/>
        </div>
        <div>
          <label style="display:block;font-size:12px;color:var(--sub);margin-bottom:6px">API Secret *</label>
          <input id="cld-secret" type="password" placeholder="••••••••••••••••" style="width:100%;padding:9px 12px;background:var(--bg);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:13px;outline:none"/>
        </div>
      </div>
      <div style="padding:0 20px 20px;display:flex;gap:10px">
        <button class="btn-add" onclick="addCloudinary()" style="padding:10px 24px">Add Account</button>
        <button class="btn-icon" style="padding:10px 18px" onclick="loadCloudinary()">↻ Refresh</button>
      </div>
    </div>

    <!-- How-to note -->
    <div style="background:rgba(59,130,246,.06);border:1px solid rgba(59,130,246,.2);border-radius:12px;padding:16px 20px;font-size:13px;color:var(--sub);line-height:1.7">
      <strong style="color:var(--text)">How to get Cloudinary credentials:</strong><br/>
      1. Sign in at <a href="https://cloudinary.com" target="_blank" style="color:var(--accent)">cloudinary.com</a> (free plan works)<br/>
      2. Go to <strong>Dashboard</strong> → copy <em>Cloud name</em>, <em>API Key</em>, and <em>API Secret</em><br/>
      3. Add them above — they are saved to <code>.env</code> as <code>CLOUDINARY_ACCOUNTS_JSON</code> and loaded immediately.<br/>
      4. Add multiple accounts for automatic failover (first account is used first).
    </div>
  </div>

  <!-- TESTER PAGE -->
  <div id="page-tester" class="page">
    <div class="page-title">API Tester</div>
    <div class="page-sub">Coba endpoint search & download langsung dari sini.</div>

    <!-- Search -->
    <div class="section" style="margin-bottom:20px">
      <div class="section-head"><span class="section-title">🔍 Search</span></div>
      <div style="padding:16px 20px">
        <div style="display:flex;gap:10px;margin-bottom:14px;flex-wrap:wrap">
          <input id="t-keyword" placeholder="Ketik nama lagu / artis…"
            style="flex:1;min-width:200px;padding:9px 13px;background:var(--bg);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:14px;outline:none"
            onkeydown="if(event.key==='Enter')runSearch()"/>
          <select id="t-limit"
            style="padding:9px 12px;background:var(--bg);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:13px;outline:none">
            <option value="5">5 hasil</option>
            <option value="10" selected>10 hasil</option>
            <option value="20">20 hasil</option>
          </select>
          <select id="t-sort"
            style="padding:9px 12px;background:var(--bg);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:13px;outline:none">
            <option value="relevance">Relevance</option>
            <option value="date">Terbaru</option>
          </select>
          <button class="btn-add" onclick="runSearch()" style="padding:9px 20px">Cari</button>
        </div>
        <!-- URL preview -->
        <div id="t-search-url" style="font-size:11px;color:var(--sub);font-family:monospace;margin-bottom:12px;word-break:break-all"></div>
        <!-- Results table -->
        <div id="t-results"></div>
      </div>
    </div>

    <!-- Manual download test -->
    <div class="section" style="margin-bottom:20px">
      <div class="section-head"><span class="section-title">⬇️ Download Audio</span></div>
      <div style="padding:16px 20px">
        <div style="display:flex;gap:10px;margin-bottom:14px;flex-wrap:wrap">
          <input id="t-videoid" placeholder="Video ID (11 karakter, contoh: dQw4w9WgXcQ)"
            maxlength="11"
            style="flex:1;min-width:220px;padding:9px 13px;background:var(--bg);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:14px;font-family:monospace;outline:none"
            onkeydown="if(event.key==='Enter')runDownload()"/>
          <button class="btn-add" onclick="runDownload()" style="padding:9px 20px">Download</button>
        </div>
        <div id="t-dl-url" style="font-size:11px;color:var(--sub);font-family:monospace;margin-bottom:12px;word-break:break-all"></div>
        <div id="t-dl-result"></div>
      </div>
    </div>

    <!-- Raw JSON viewer -->
    <div class="section">
      <div class="section-head">
        <span class="section-title">📋 Response JSON</span>
        <button class="btn-icon" style="padding:5px 12px;font-size:12px" onclick="copyJson()">Copy</button>
      </div>
      <pre id="t-json" style="margin:0;padding:16px 20px;font-size:12px;color:var(--sub);white-space:pre-wrap;word-break:break-all;max-height:360px;overflow-y:auto;background:transparent">
— belum ada request —</pre>
    </div>
  </div>

  <!-- SETTINGS PAGE -->
  <div id="page-settings" class="page">
    <div class="page-title">Settings</div>
    <div class="page-sub">Admin configuration.</div>
    <div class="section" style="margin-bottom:24px">
      <div class="section-head"><span class="section-title">Change Admin PIN</span></div>
      <div style="padding:20px">
        <p style="font-size:13px;color:var(--sub);margin-bottom:16px">
          The PIN is stored as a PBKDF2-SHA256 hash — the raw value is never saved anywhere.
        </p>
        <div class="pin-form">
          <input type="password" id="newPin" placeholder="New PIN" />
          <input type="password" id="confirmPin" placeholder="Confirm PIN" />
          <button class="btn-add" onclick="changePin()">Save PIN</button>
        </div>
      </div>
    </div>
    <div class="section">
      <div class="section-head"><span class="section-title">API Info</span></div>
      <table><thead><tr><th>Setting</th><th>Value</th></tr></thead>
      <tbody>
        <tr><td>Docs URL</td><td><a href="/docs" target="_blank" style="color:var(--accent)">/docs</a></td></tr>
        <tr><td>Health URL</td><td><a href="/health" target="_blank" style="color:var(--accent)">/health</a></td></tr>
        <tr><td>OAuth Setup</td><td><a href="/oauth/device/setup" target="_blank" style="color:var(--accent)">/oauth/device/setup</a></td></tr>
        <tr><td>DB Path</td><td class="mono">%DB_PATH%</td></tr>
      </tbody></table>
    </div>
  </div>

</main>

<div id="toast" class="toast"></div>

<script>
// ── Navigation ───────────────────────────────────────────────────────────────
function showPage(name) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('nav a').forEach(a => a.classList.remove('active'));
  document.getElementById('page-' + name).classList.add('active');
  document.getElementById('nav-' + name).classList.add('active');
  if (name === 'status')    loadStatus();
  if (name === 'whitelist') loadWhitelist();
  if (name === 'analytics') loadAnalytics();
  if (name === 'oauth')     { checkOAuth(); loadOauthCreds(); }
  if (name === 'cloudinary') loadCloudinary();
  if (name === 'tester')   initTester();
}

// ── Toast ─────────────────────────────────────────────────────────────────
let toastTimer;
function showToast(msg, isError=false) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = 'toast show' + (isError ? ' error' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 3000);
}

// ── Fetch helpers ────────────────────────────────────────────────────────────
async function api(method, path, body=null) {
  const opts = { method, headers: {} };
  if (body) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  const r = await fetch(path, opts);
  if (!r.ok) { const d = await r.json().catch(()=>({})); throw new Error(d.detail || r.statusText); }
  return r.json();
}

// ── Status Page ───────────────────────────────────────────────────────────────
async function loadStatus() {
  try {
    const d = await api('GET', '/admin/api/status');
    const healthy = d.health?.status === 'healthy';
    document.getElementById('statusCards').innerHTML = `
      <div class="card">
        <div class="card-label">Server</div>
        <div class="card-value" style="font-size:18px">
          <span class="dot ${healthy ? 'dot-green' : 'dot-red'}"></span>${healthy ? 'Healthy' : 'Error'}
        </div>
        <div class="card-sub">v${d.health?.version || '—'}</div>
      </div>
      <div class="card">
        <div class="card-label">Cache</div>
        <div class="card-value" style="font-size:18px">${d.health?.cache || '—'}</div>
        <div class="card-sub">cache backend</div>
      </div>
      <div class="card">
        <div class="card-label">Cloudinary</div>
        <div class="card-value">${d.health?.cloudinary_accounts ?? '—'}</div>
        <div class="card-sub">accounts</div>
      </div>
      <div class="card">
        <div class="card-label">OAuth</div>
        <div class="card-value" style="font-size:18px">
          <span class="dot ${d.oauth?.ready ? 'dot-green' : 'dot-yellow'}"></span>${d.oauth?.ready ? 'Ready' : 'Not set'}
        </div>
        <div class="card-sub">${d.oauth?.ready ? `expires ${d.oauth.expires_in_seconds}s` : 'Run OAuth setup'}</div>
      </div>
      <div class="card">
        <div class="card-label">Whitelist</div>
        <div class="card-value">${d.whitelist_count}</div>
        <div class="card-sub">${d.whitelist_count === 0 ? 'open (all allowed)' : 'domains'}</div>
      </div>
    `;
    const rows = Object.entries({
      'Service': d.health?.service,
      'Port': d.health?.port,
      'Cache': d.health?.cache,
      'Cloudinary Accounts': d.health?.cloudinary_accounts,
      'OAuth Ready': d.oauth?.ready ? '✅ Yes' : '⚠️ Not configured',
      'OAuth Scope': d.oauth?.scope || '—',
      'OAuth Expires': d.oauth?.expires_in_seconds ? d.oauth.expires_in_seconds + 's' : '—',
      'Whitelist Mode': d.whitelist_count === 0 ? '🌐 Open (all origins)' : `🛡 Restricted (${d.whitelist_count} domains)`,
    }).map(([k,v]) => `<tr><td>${k}</td><td>${v ?? '—'}</td></tr>`).join('');
    document.getElementById('statusDetails').innerHTML = rows;
  } catch(e) {
    showToast('Status load failed: ' + e.message, true);
  }
}

// ── Whitelist Page ────────────────────────────────────────────────────────────
async function loadWhitelist() {
  try {
    const d = await api('GET', '/admin/api/whitelist');
    const badge = document.getElementById('whitelistMode');
    if (d.length === 0) {
      badge.className = 'badge badge-yellow';
      badge.textContent = '🌐 Open — all origins allowed';
    } else {
      badge.className = 'badge badge-green';
      badge.textContent = `🛡 Restricted — ${d.length} domain${d.length!==1?'s':''}`;
    }
    const tbody = document.getElementById('whitelistTable');
    if (d.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" class="empty">No domains added. API is open to all origins.</td></tr>';
      return;
    }
    tbody.innerHTML = d.map(r => `
      <tr>
        <td class="mono">${r.domain}</td>
        <td style="color:var(--sub)">${r.note || '—'}</td>
        <td style="color:var(--sub);font-size:12px">${r.added_at.slice(0,10)}</td>
        <td><span class="badge ${r.enabled ? 'badge-green' : 'badge-red'}">${r.enabled ? 'Active' : 'Disabled'}</span></td>
        <td style="display:flex;gap:6px">
          <button class="btn-icon toggle" onclick="toggleDomain('${r.domain}', ${!r.enabled})">${r.enabled ? 'Disable' : 'Enable'}</button>
          <button class="btn-icon" onclick="removeDomain('${r.domain}')">✕ Remove</button>
        </td>
      </tr>`).join('');
  } catch(e) { showToast('Whitelist load failed: ' + e.message, true); }
}

async function addDomain() {
  const domain = document.getElementById('newDomain').value.trim();
  const note   = document.getElementById('newNote').value.trim();
  if (!domain) { showToast('Enter a domain', true); return; }
  try {
    await api('POST', '/admin/api/whitelist', { domain, note });
    document.getElementById('newDomain').value = '';
    document.getElementById('newNote').value = '';
    showToast('Domain added ✓');
    loadWhitelist();
  } catch(e) { showToast(e.message, true); }
}

async function removeDomain(domain) {
  if (!confirm(`Remove "${domain}" from whitelist?`)) return;
  try {
    await api('DELETE', `/admin/api/whitelist/${encodeURIComponent(domain)}`);
    showToast('Domain removed ✓');
    loadWhitelist();
  } catch(e) { showToast(e.message, true); }
}

async function toggleDomain(domain, enabled) {
  try {
    await api('PATCH', `/admin/api/whitelist/${encodeURIComponent(domain)}`, { enabled });
    showToast(enabled ? 'Domain enabled ✓' : 'Domain disabled ✓');
    loadWhitelist();
  } catch(e) { showToast(e.message, true); }
}

// ── Analytics Page ────────────────────────────────────────────────────────────
async function loadAnalytics() {
  try {
    const d = await api('GET', '/admin/api/analytics');
    document.getElementById('analyticsCards').innerHTML = `
      <div class="card"><div class="card-label">Total Requests</div>
        <div class="card-value">${d.total.toLocaleString()}</div></div>
      <div class="card"><div class="card-label">Today</div>
        <div class="card-value">${d.today.toLocaleString()}</div></div>
      <div class="card"><div class="card-label">Avg Response</div>
        <div class="card-value" style="font-size:22px">${d.avg_response_ms}ms</div></div>
      <div class="card"><div class="card-label">Status Codes</div>
        <div style="margin-top:8px">${(d.status_distribution||[]).slice(0,4).map(s =>
          `<div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:4px">
            <span class="badge ${s.status_code<400?'badge-green':s.status_code<500?'badge-yellow':'badge-red'}">${s.status_code}</span>
            <span style="color:var(--sub)">${s.n}</span></div>`
        ).join('')}</div>
      </div>
    `;
    document.getElementById('topPaths').innerHTML = (d.top_paths||[]).length
      ? d.top_paths.map(p=>`<tr><td class="mono">${p.path}</td><td>${p.n}</td></tr>`).join('')
      : '<tr><td colspan="2" class="empty">No data yet</td></tr>';
    document.getElementById('recentLog').innerHTML = (d.recent||[]).length
      ? d.recent.map(r => {
          const cls = r.status_code >= 500 ? 'log-row-5xx' : r.status_code >= 400 ? 'log-row-4xx' : 'log-row-2xx';
          const t = r.ts.replace('T',' ').slice(0,19);
          const sc = r.status_code;
          const badgeCls = sc<400?'badge-green':sc<500?'badge-yellow':'badge-red';
          return `<tr class="${cls}">
            <td class="mono" style="font-size:11px">${t}</td>
            <td><span class="badge badge-blue">${r.method}</span></td>
            <td class="mono">${r.path}</td>
            <td><span class="badge ${badgeCls}">${sc}</span></td>
            <td style="font-size:12px;color:var(--sub)">${r.origin_domain||'—'}</td>
            <td style="color:var(--sub)">${r.response_ms}</td>
          </tr>`;
        }).join('')
      : '<tr><td colspan="6" class="empty">No requests recorded yet</td></tr>';
  } catch(e) { showToast('Analytics load failed: ' + e.message, true); }
}

// ── OAuth Page ────────────────────────────────────────────────────────────────
async function checkOAuth() {
  const box = document.getElementById('oauthStatus');
  try {
    const d = await api('GET', '/oauth/device/status');
    if (d.ready) {
      box.innerHTML = `
        <div class="oauth-info">
          <div class="oauth-title"><span class="dot dot-green"></span>Connected to Google</div>
          <div class="oauth-sub">Token is valid. Expires in ${d.expires_in_seconds}s.<br>
          Scope: <code>${d.scope || '—'}</code></div>
        </div>
        <span class="badge badge-green">✓ Active</span>`;
    } else {
      box.innerHTML = `
        <div class="oauth-info">
          <div class="oauth-title"><span class="dot dot-yellow"></span>Not Connected</div>
          <div class="oauth-sub">${d.next_step || 'Use the setup UI below to connect.'}</div>
        </div>
        <span class="badge badge-yellow">⚠ Not set</span>`;
    }
  } catch(e) {
    box.innerHTML = `<div class="oauth-info"><div class="oauth-title"><span class="dot dot-red"></span>Status check failed</div>
      <div class="oauth-sub">${e.message}</div></div>`;
  }
}

async function revokeOAuth() {
  if (!confirm('Delete oauth.json and revoke Google access?')) return;
  try {
    await api('POST', '/oauth/device/revoke');
    showToast('Token revoked ✓');
    checkOAuth();
  } catch(e) { showToast(e.message, true); }
}

// ── Settings Page ─────────────────────────────────────────────────────────────
async function changePin() {
  const np = document.getElementById('newPin').value;
  const cp = document.getElementById('confirmPin').value;
  if (!np) { showToast('Enter a new PIN', true); return; }
  if (np !== cp) { showToast('PINs do not match', true); return; }
  if (np.length < 4) { showToast('PIN must be at least 4 characters', true); return; }
  try {
    await api('POST', '/admin/api/change-pin', { pin: np });
    document.getElementById('newPin').value = '';
    document.getElementById('confirmPin').value = '';
    showToast('PIN updated ✓');
  } catch(e) { showToast(e.message, true); }
}

// ── API Tester ────────────────────────────────────────────────────────────────
let _lastJson = null;

function initTester() {
  // Update URL preview on keyword input
  const kw = document.getElementById('t-keyword');
  kw && kw.addEventListener('input', updateSearchUrl);
}

function updateSearchUrl() {
  const kw    = encodeURIComponent(document.getElementById('t-keyword').value || '');
  const limit = document.getElementById('t-limit').value;
  const sort  = document.getElementById('t-sort').value;
  const url   = `/api/v1/search?keyword=${kw}&limit=${limit}&sort_by=${sort}`;
  document.getElementById('t-search-url').textContent = kw ? `GET ${url}` : '';
  return url;
}

async function runSearch() {
  const kw = document.getElementById('t-keyword').value.trim();
  if (!kw) { showToast('Masukkan keyword dulu', true); return; }
  const url = updateSearchUrl();
  const resEl = document.getElementById('t-results');
  resEl.innerHTML = '<div style="color:var(--sub);font-size:13px"><span class="spinner">⟳</span> Mencari…</div>';
  document.getElementById('t-json').textContent = '⟳ loading…';
  try {
    const r = await fetch(url);
    const d = await r.json();
    _lastJson = d;
    document.getElementById('t-json').textContent = JSON.stringify(d, null, 2);

    if (!r.ok) {
      resEl.innerHTML = `<div style="color:#f87171;font-size:13px">Error ${r.status}: ${d.detail?.message || d.detail || r.statusText}</div>`;
      return;
    }
    const videos = d.videos || [];
    if (!videos.length) {
      resEl.innerHTML = '<div style="color:var(--sub);font-size:13px">Tidak ada hasil.</div>';
      return;
    }
    resEl.innerHTML = `
      <div style="font-size:12px;color:var(--sub);margin-bottom:10px">${d.result_count} hasil untuk "<strong style="color:var(--text)">${d.search_keyword}</strong>"</div>
      <table>
        <thead><tr>
          <th>#</th><th>Thumbnail</th><th>Judul</th><th>Video ID</th><th>Durasi</th><th>Action</th>
        </tr></thead>
        <tbody>
          ${videos.map((v, i) => `
            <tr>
              <td style="color:var(--sub)">${i+1}</td>
              <td><img src="${v.thumbnail || ''}" width="80" height="45" style="border-radius:4px;object-fit:cover;background:var(--border)" onerror="this.style.display='none'"/></td>
              <td style="max-width:280px;white-space:normal;line-height:1.4">${v.title || '—'}</td>
              <td class="mono" style="font-size:12px">${v.video_id || '—'}</td>
              <td style="color:var(--sub);white-space:nowrap">${formatDuration(v.duration)}</td>
              <td>
                <button class="btn-icon" style="padding:5px 10px;font-size:12px"
                  onclick="fillDownload('${v.video_id}')">⬇ Download</button>
              </td>
            </tr>`).join('')}
        </tbody>
      </table>`;
  } catch(e) {
    resEl.innerHTML = `<div style="color:#f87171;font-size:13px">Gagal: ${e.message}</div>`;
    document.getElementById('t-json').textContent = e.message;
  }
}

function fillDownload(videoId) {
  document.getElementById('t-videoid').value = videoId;
  updateDownloadUrl();
  document.getElementById('t-videoid').scrollIntoView({behavior:'smooth', block:'center'});
  runDownload();
}

function updateDownloadUrl() {
  const vid = document.getElementById('t-videoid').value.trim();
  const url  = vid ? `/api/v1/download/audio?video_id=${vid}&format=link` : '';
  document.getElementById('t-dl-url').textContent = url ? `GET ${url}` : '';
  return url;
}

async function runDownload() {
  const vid = document.getElementById('t-videoid').value.trim();
  if (!vid || vid.length !== 11) { showToast('Video ID harus 11 karakter', true); return; }
  const url  = updateDownloadUrl();
  const resEl = document.getElementById('t-dl-result');
  resEl.innerHTML = '<div style="color:var(--sub);font-size:13px"><span class="spinner">⟳</span> Downloading… (bisa 30–120 detik untuk video baru)</div>';
  document.getElementById('t-json').textContent = '⟳ loading…';
  try {
    const r = await fetch(url);
    const d = await r.json();
    _lastJson = d;
    document.getElementById('t-json').textContent = JSON.stringify(d, null, 2);

    if (!r.ok) {
      resEl.innerHTML = `<div style="color:#f87171;font-size:13px">Error ${r.status}: ${d.detail?.message || d.detail || r.statusText}</div>`;
      return;
    }
    resEl.innerHTML = `
      <div style="background:rgba(34,197,94,.06);border:1px solid rgba(34,197,94,.25);border-radius:10px;padding:14px 16px">
        <div style="font-weight:600;color:var(--text);margin-bottom:10px">✓ ${d.cached ? 'Cache HIT' : 'Download selesai'}</div>
        <table style="margin:0"><tbody>
          <tr><td style="color:var(--sub);width:120px">Judul</td><td>${d.title || '—'}</td></tr>
          <tr><td style="color:var(--sub)">Durasi</td><td>${formatDuration(d.duration)}</td></tr>
          <tr><td style="color:var(--sub)">Storage</td><td>${d.storage_source}${d.storage_account ? ' · ' + d.storage_account : ''}</td></tr>
          <tr><td style="color:var(--sub)">Cached</td><td><span class="badge ${d.cached ? 'badge-green' : 'badge-red'}">${d.cached ? 'Ya' : 'Tidak'}</span></td></tr>
          <tr><td style="color:var(--sub)">URL</td>
            <td style="word-break:break-all">
              ${d.download_url
                ? `<a href="${d.download_url}" target="_blank" style="color:var(--accent)">${d.download_url.slice(0,70)}${d.download_url.length>70?'…':''}</a>`
                : '—'}
            </td>
          </tr>
        </tbody></table>
      </div>`;
  } catch(e) {
    resEl.innerHTML = `<div style="color:#f87171;font-size:13px">Gagal: ${e.message}</div>`;
    document.getElementById('t-json').textContent = e.message;
  }
}

function formatDuration(secs) {
  if (!secs) return '—';
  const m = Math.floor(secs / 60), s = secs % 60;
  return `${m}:${String(s).padStart(2,'0')}`;
}

function copyJson() {
  if (!_lastJson) return;
  navigator.clipboard.writeText(JSON.stringify(_lastJson, null, 2))
    .then(() => showToast('JSON tersalin ✓'))
    .catch(() => showToast('Gagal copy', true));
}

// ── OAuth Credentials Import ──────────────────────────────────────────────────
async function loadOauthCreds() {
  const el = document.getElementById('oauthCredsStatus');
  if (!el) return;
  try {
    const d = await api('GET', '/admin/api/oauth/credentials');
    const idOk  = d.has_client_id;
    const secOk = d.has_client_secret;
    el.innerHTML = `
      <div style="display:flex;gap:10px;flex-wrap:wrap">
        <span class="badge ${idOk  ? 'badge-green' : 'badge-red'}">${idOk  ? '✓ Client ID set' : '✗ Client ID missing'}</span>
        <span class="badge ${secOk ? 'badge-green' : 'badge-red'}">${secOk ? '✓ Client Secret set' : '✗ Client Secret missing'}</span>
        ${idOk ? `<span style="font-size:11px;color:var(--sub);align-self:center">${d.client_id_prefix}</span>` : ''}
      </div>`;
  } catch(e) { el.innerHTML = `<span class="badge badge-red">Gagal cek status</span>`; }
}

function handleOauthFile(event) {
  const file = event.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = e => {
    document.getElementById('oauthJsonPaste').value = e.target.result;
    document.getElementById('oauthDropZone').style.borderColor = 'var(--green)';
    showToast('File dimuat — klik Import untuk simpan');
  };
  reader.readAsText(file);
}

function handleOauthDrop(event) {
  event.preventDefault();
  document.getElementById('oauthDropZone').style.borderColor = 'var(--border)';
  const file = event.dataTransfer.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = e => {
    document.getElementById('oauthJsonPaste').value = e.target.result;
    document.getElementById('oauthDropZone').style.borderColor = 'var(--green)';
    showToast('File dimuat — klik Import untuk simpan');
  };
  reader.readAsText(file);
}

async function importOauthJson() {
  const raw = document.getElementById('oauthJsonPaste').value.trim();
  const resultEl = document.getElementById('oauthImportResult');
  if (!raw) { showToast('Paste JSON dulu atau pilih file', true); return; }
  resultEl.innerHTML = '<span class="spinner">⟳</span> Menyimpan…';
  try {
    const d = await api('POST', '/admin/api/oauth/import-json', JSON.parse(raw));
    resultEl.innerHTML = `<span class="badge badge-green">✓ ${d.message} (${d.client_id_prefix})</span>`;
    showToast('OAuth credentials tersimpan ke .env ✓');
    document.getElementById('oauthJsonPaste').value = '';
    document.getElementById('oauthDropZone').style.borderColor = 'var(--border)';
    loadOauthCreds();
  } catch(e) {
    resultEl.innerHTML = `<span class="badge badge-red">✗ ${e.message}</span>`;
    showToast(e.message, true);
  }
}

// ── Cloudinary Page ───────────────────────────────────────────────────────────
async function loadCloudinary() {
  const list = document.getElementById('cloudinaryList');
  list.innerHTML = '<div style="color:var(--sub);font-size:13px;padding:8px 0"><span class="spinner">⟳</span> Loading…</div>';
  try {
    const d = await api('GET', '/admin/api/cloudinary');
    if (!d.length) {
      list.innerHTML = `
        <div class="section" style="margin-bottom:20px">
          <div style="padding:32px;text-align:center;color:var(--sub);font-size:13px">
            ☁️ No Cloudinary accounts configured yet.<br/>
            Add one below to enable audio uploads.
          </div>
        </div>`;
      return;
    }
    list.innerHTML = d.map((acc, i) => `
      <div class="section" style="margin-bottom:16px" id="cld-card-${acc.index}">
        <div class="section-head">
          <div style="display:flex;align-items:center;gap:10px">
            <span style="font-size:18px">☁️</span>
            <div>
              <div style="font-weight:600;color:var(--text)">${acc.cloud_name}</div>
              <div style="font-size:12px;color:var(--sub)">Account #${acc.index + 1}${acc.index === 0 ? ' · <span style="color:var(--green)">Primary</span>' : ' · Failover'}</div>
            </div>
          </div>
          <div style="display:flex;gap:8px">
            <button class="btn-icon" style="padding:7px 14px;border-color:rgba(59,130,246,.4);color:var(--accent)" onclick="testCloudinary(${acc.index})">⚡ Test</button>
            <button class="btn-icon" onclick="editCloudinary(${acc.index}, '${acc.cloud_name}', '${acc.api_key}')">✏ Edit</button>
            <button class="btn-icon" onclick="removeCloudinary(${acc.index})">✕ Remove</button>
          </div>
        </div>
        <div id="cld-edit-${acc.index}" style="display:none;padding:16px 20px;border-top:1px solid var(--border);display:none">
          <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:12px">
            <div>
              <label style="display:block;font-size:12px;color:var(--sub);margin-bottom:5px">Cloud Name</label>
              <input id="edit-cloud-${acc.index}" value="${acc.cloud_name}" style="width:100%;padding:8px 11px;background:var(--bg);border:1px solid var(--border);border-radius:7px;color:var(--text);font-size:13px;outline:none"/>
            </div>
            <div>
              <label style="display:block;font-size:12px;color:var(--sub);margin-bottom:5px">API Key</label>
              <input id="edit-key-${acc.index}" value="${acc.api_key}" style="width:100%;padding:8px 11px;background:var(--bg);border:1px solid var(--border);border-radius:7px;color:var(--text);font-size:13px;outline:none"/>
            </div>
            <div>
              <label style="display:block;font-size:12px;color:var(--sub);margin-bottom:5px">API Secret (leave blank to keep)</label>
              <input id="edit-secret-${acc.index}" type="password" placeholder="Leave blank to keep existing" style="width:100%;padding:8px 11px;background:var(--bg);border:1px solid var(--border);border-radius:7px;color:var(--text);font-size:13px;outline:none"/>
            </div>
          </div>
          <div style="display:flex;gap:8px">
            <button class="btn-add" style="padding:8px 18px" onclick="saveCloudinary(${acc.index})">💾 Save</button>
            <button class="btn-icon" onclick="cancelEdit(${acc.index})">Cancel</button>
          </div>
        </div>
        <table style="margin:0"><tbody>
          <tr>
            <td style="width:140px;color:var(--sub)">API Key</td>
            <td class="mono">${acc.api_key}</td>
          </tr>
          <tr>
            <td style="color:var(--sub)">API Secret</td>
            <td class="mono">${acc.api_secret_masked || '(not set)'}</td>
          </tr>
          <tr>
            <td style="color:var(--sub)">Credentials</td>
            <td><span class="badge ${acc.has_secret ? 'badge-green' : 'badge-red'}">${acc.has_secret ? '✓ Set' : '⚠ Missing secret'}</span></td>
          </tr>
          <tr id="cld-test-result-${acc.index}"><td colspan="2"></td></tr>
        </tbody></table>
      </div>`).join('');
  } catch(e) { showToast('Cloudinary load failed: ' + e.message, true); }
}

async function addCloudinary() {
  const cloud  = document.getElementById('cld-cloud').value.trim();
  const key    = document.getElementById('cld-key').value.trim();
  const secret = document.getElementById('cld-secret').value.trim();
  if (!cloud || !key || !secret) { showToast('All three fields are required', true); return; }
  try {
    await api('POST', '/admin/api/cloudinary', { cloud_name: cloud, api_key: key, api_secret: secret });
    document.getElementById('cld-cloud').value = '';
    document.getElementById('cld-key').value = '';
    document.getElementById('cld-secret').value = '';
    showToast('Account added & saved to .env ✓');
    loadCloudinary();
  } catch(e) { showToast(e.message, true); }
}

function editCloudinary(idx, cloud, key) {
  const editDiv = document.getElementById('cld-edit-' + idx);
  editDiv.style.display = editDiv.style.display === 'none' ? 'block' : 'none';
}
function cancelEdit(idx) {
  document.getElementById('cld-edit-' + idx).style.display = 'none';
}

async function saveCloudinary(idx) {
  const cloud  = document.getElementById('edit-cloud-' + idx).value.trim();
  const key    = document.getElementById('edit-key-' + idx).value.trim();
  const secret = document.getElementById('edit-secret-' + idx).value.trim();
  try {
    await api('PUT', `/admin/api/cloudinary/${idx}`, { cloud_name: cloud, api_key: key, api_secret: secret || null });
    showToast('Account updated & saved to .env ✓');
    loadCloudinary();
  } catch(e) { showToast(e.message, true); }
}

async function removeCloudinary(idx) {
  if (!confirm('Remove this Cloudinary account?')) return;
  try {
    await api('DELETE', `/admin/api/cloudinary/${idx}`);
    showToast('Account removed from .env ✓');
    loadCloudinary();
  } catch(e) { showToast(e.message, true); }
}

async function testCloudinary(idx) {
  const row = document.getElementById('cld-test-result-' + idx);
  row.innerHTML = '<td colspan="2" style="color:var(--sub)"><span class="spinner">⟳</span> Testing connection…</td>';
  try {
    const d = await api('POST', `/admin/api/cloudinary/${idx}/test`);
    row.innerHTML = `<td colspan="2"><span class="badge ${d.ok ? 'badge-green' : 'badge-red'}">${d.message}</span></td>`;
  } catch(e) {
    row.innerHTML = `<td colspan="2"><span class="badge badge-red">Error: ${e.message}</span></td>`;
  }
}

// ── Boot ──────────────────────────────────────────────────────────────────────
loadStatus();
</script>
</body>
</html>"""


@router.get("/admin", response_class=HTMLResponse, include_in_schema=False)
async def dashboard(request: Request):
    if not _is_logged_in(request):
        return RedirectResponse("/login", status_code=302)
    html = _DASHBOARD_HTML.replace("%DB_PATH%", str(db.DB_PATH))
    return HTMLResponse(html)


# ── Admin JSON API ─────────────────────────────────────────────────────────────

def _auth_guard(request: Request):
    if not _is_logged_in(request):
        raise HTTPException(status_code=401, detail="Not authenticated")


@router.get("/admin/api/status", include_in_schema=False)
async def api_status(request: Request):
    _auth_guard(request)
    import httpx
    health = {}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get("http://localhost:8000/health", timeout=5)
            health = r.json()
    except Exception:
        health = {"status": "error"}

    oauth = {}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get("http://localhost:8000/oauth/device/status", timeout=5)
            oauth = r.json()
    except Exception:
        pass

    whitelist = await db.list_whitelist()
    return {
        "health": health,
        "oauth": oauth,
        "whitelist_count": len([d for d in whitelist if d["enabled"]]),
    }


@router.get("/admin/api/whitelist", include_in_schema=False)
async def api_list_whitelist(request: Request):
    _auth_guard(request)
    return await db.list_whitelist()


@router.post("/admin/api/whitelist", include_in_schema=False)
async def api_add_whitelist(request: Request):
    _auth_guard(request)
    body = await request.json()
    domain = body.get("domain", "").strip()
    note = body.get("note", "").strip()
    if not domain:
        raise HTTPException(status_code=422, detail="'domain' is required")
    try:
        result = await db.add_domain(domain, note)
        return result
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.delete("/admin/api/whitelist/{domain:path}", include_in_schema=False)
async def api_remove_whitelist(request: Request, domain: str):
    _auth_guard(request)
    removed = await db.remove_domain(domain)
    if not removed:
        raise HTTPException(status_code=404, detail="Domain not found")
    return {"status": "removed", "domain": domain}


@router.patch("/admin/api/whitelist/{domain:path}", include_in_schema=False)
async def api_toggle_whitelist(request: Request, domain: str):
    _auth_guard(request)
    body = await request.json()
    enabled = bool(body.get("enabled", True))
    ok = await db.toggle_domain(domain, enabled)
    if not ok:
        raise HTTPException(status_code=404, detail="Domain not found")
    return {"status": "updated", "domain": domain, "enabled": enabled}


@router.post("/admin/api/change-pin", include_in_schema=False)
async def api_change_pin(request: Request):
    _auth_guard(request)
    body = await request.json()
    new_pin = str(body.get("pin", "")).strip()
    if len(new_pin) < 4:
        raise HTTPException(status_code=422, detail="PIN must be at least 4 characters")
    await db.update_pin(new_pin)
    return {"status": "ok"}


@router.get("/admin/api/analytics", include_in_schema=False)
async def api_analytics(request: Request):
    _auth_guard(request)
    return await db.get_analytics()


# ── Cloudinary endpoints ──────────────────────────────────────────────────────

@router.get("/admin/api/cloudinary", include_in_schema=False)
async def api_cloudinary_list(request: Request):
    _auth_guard(request)
    from youtube_search.admin.env_manager import list_accounts
    return list_accounts()


@router.post("/admin/api/cloudinary", include_in_schema=False)
async def api_cloudinary_add(request: Request):
    _auth_guard(request)
    from youtube_search.admin.env_manager import add_account
    body = await request.json()
    try:
        result = add_account(
            body.get("cloud_name", ""),
            body.get("api_key", ""),
            body.get("api_secret", ""),
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.put("/admin/api/cloudinary/{index}", include_in_schema=False)
async def api_cloudinary_update(request: Request, index: int):
    _auth_guard(request)
    from youtube_search.admin.env_manager import update_account
    body = await request.json()
    try:
        result = update_account(
            index,
            body.get("cloud_name", ""),
            body.get("api_key", ""),
            body.get("api_secret") or None,
        )
        return result
    except (ValueError, IndexError) as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.delete("/admin/api/cloudinary/{index}", include_in_schema=False)
async def api_cloudinary_remove(request: Request, index: int):
    _auth_guard(request)
    from youtube_search.admin.env_manager import remove_account
    try:
        name = remove_account(index)
        return {"status": "removed", "cloud_name": name}
    except IndexError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/admin/api/cloudinary/{index}/test", include_in_schema=False)
async def api_cloudinary_test(request: Request, index: int):
    _auth_guard(request)
    from youtube_search.admin.env_manager import test_account
    try:
        return test_account(index)
    except IndexError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── OAuth credentials import ──────────────────────────────────────────────────

@router.post("/admin/api/oauth/import-json", include_in_schema=False)
async def api_oauth_import_json(request: Request):
    """
    Accept a Google OAuth client_secret_*.json (raw JSON body or form upload).
    Extracts client_id + client_secret and saves them to .env.
    """
    _auth_guard(request)
    import json as _json
    from youtube_search.admin.env_manager import _set_env_key, _reload_settings

    content_type = request.headers.get("content-type", "")

    if "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        raw = form.get("json_text", "")
    else:
        raw = (await request.body()).decode("utf-8", errors="replace")

    try:
        data = _json.loads(raw)
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid JSON")

    # Google Console JSON has a top-level key: "installed" or "web"
    inner = data.get("installed") or data.get("web")
    if inner:
        client_id     = inner.get("client_id", "").strip()
        client_secret = inner.get("client_secret", "").strip()
    else:
        # Maybe user pasted just the inner object
        client_id     = data.get("client_id", "").strip()
        client_secret = data.get("client_secret", "").strip()

    if not client_id or not client_secret:
        raise HTTPException(
            status_code=422,
            detail="Could not find client_id / client_secret in the JSON"
        )

    _set_env_key("OAUTH_CLIENT_ID", client_id)
    _set_env_key("OAUTH_CLIENT_SECRET", client_secret)
    import os
    os.environ["OAUTH_CLIENT_ID"] = client_id
    os.environ["OAUTH_CLIENT_SECRET"] = client_secret
    _reload_settings()

    return {
        "status": "ok",
        "client_id_prefix": client_id[:12] + "…",
        "message": "Saved OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET to .env",
    }


@router.get("/admin/api/oauth/credentials", include_in_schema=False)
async def api_oauth_credentials_status(request: Request):
    """Return whether OAuth credentials are configured (never expose the values)."""
    _auth_guard(request)
    import os
    from youtube_search.admin.env_manager import _get_env_key

    client_id = (os.environ.get("OAUTH_CLIENT_ID") or _get_env_key("OAUTH_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("OAUTH_CLIENT_SECRET") or _get_env_key("OAUTH_CLIENT_SECRET") or "").strip()

    return {
        "has_client_id":     bool(client_id),
        "has_client_secret": bool(client_secret),
        "client_id_prefix":  (client_id[:16] + "…") if client_id else "",
    }
