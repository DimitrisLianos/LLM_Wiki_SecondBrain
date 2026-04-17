#!/usr/bin/env python3
"""secondbrain web ui — fastapi backend.

wraps all existing terminal scripts (ingest, query, search, lint, server
control, dedup) behind a json api. the frontend is served as static files
from web/frontend/dist/ in production, or proxied via vite in development.

usage:
    python web/api/app.py                    # start on port 3000.
    python web/api/app.py --port 8888        # custom port.
    python web/api/app.py --dev              # reload on code changes.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# ensure project root + scripts/ are importable.
_PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _PROJECT_DIR / "scripts"
for _p in (str(_PROJECT_DIR), str(_SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from fastapi import FastAPI, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse, Response
    from starlette.middleware.base import BaseHTTPMiddleware
except ImportError:
    print("\n  error: fastapi is not installed.")
    print("  install it:  pip install 'fastapi[standard]'\n")
    sys.exit(1)

from web.api.routers import (  # noqa: E402
    admin,
    dedup,
    ingest,
    lint,
    query,
    search,
    server,
    wiki,
)

# --- app. ---

app = FastAPI(
    title="SecondBrain",
    description="Local LLM Wiki — web interface for ingest, query, search, and wiki management.",
    version="0.1.0",
)

# cors: allow local dev servers.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",  # vite dev server.
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- security headers. ---
# this app is intended to run on localhost only, but defence-in-depth here
# prevents accidental exposure on a LAN or tunnelled port from turning a
# harmless bug into a clickjacking / xss / mime-sniff / mixed-content
# vector. keep the CSP strict enough to block inline event handlers but
# permissive enough to allow vite-built bundle + marked / dompurify
# evaluation via a module script.
#
# google fonts allowance: index.html pulls the Inter + Playfair Display
# stylesheet from fonts.googleapis.com, which in turn requests woff2 files
# from fonts.gstatic.com. both origins are whitelisted below.
_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "img-src 'self' data:; "
    "font-src 'self' data: https://fonts.gstatic.com; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "object-src 'none'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """attach baseline security headers to every response."""

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", _CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=()",
        )
        return response


app.add_middleware(SecurityHeadersMiddleware)

# --- routers. ---

app.include_router(server.router, prefix="/api/server", tags=["server"])
app.include_router(search.router, prefix="/api/search", tags=["search"])
app.include_router(wiki.router, prefix="/api/wiki", tags=["wiki"])
app.include_router(query.router, prefix="/api/query", tags=["query"])
app.include_router(ingest.router, prefix="/api/ingest", tags=["ingest"])
app.include_router(lint.router, prefix="/api/lint", tags=["lint"])
app.include_router(dedup.router, prefix="/api/dedup", tags=["dedup"])
app.include_router(admin.router, prefix="/api/admin", tags=["admin"])


# --- static frontend (production). ---

_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if _FRONTEND_DIST.exists():
    # serve built frontend assets.
    app.mount(
        "/assets",
        StaticFiles(directory=_FRONTEND_DIST / "assets"),
        name="frontend-assets",
    )

    _FRONTEND_ROOT = _FRONTEND_DIST.resolve()
    _INDEX_HTML = (_FRONTEND_ROOT / "index.html").resolve()

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str) -> FileResponse:
        """spa fallback: serve index.html for all non-api routes.

        ``full_path`` comes straight from the URL and is treated as
        untrusted. we resolve it relative to the frontend dist directory
        and refuse anything that escapes that tree (``..``, absolute paths,
        symlinks that point outside, …). on any mismatch we fall back to
        index.html so the SPA router still gets a chance to render.
        """
        # empty path → serve the SPA entry point.
        if not full_path:
            return FileResponse(_INDEX_HTML)

        try:
            candidate = (_FRONTEND_ROOT / full_path).resolve()
        except (OSError, ValueError):
            return FileResponse(_INDEX_HTML)

        # containment check: candidate MUST live under the dist tree.
        try:
            candidate.relative_to(_FRONTEND_ROOT)
        except ValueError:
            return FileResponse(_INDEX_HTML)

        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_INDEX_HTML)
else:
    @app.get("/")
    async def root() -> dict:
        return {
            "status": "ok",
            "message": "SecondBrain API is running. Frontend not built yet.",
            "docs": "/docs",
        }


# --- cli entrypoint. ---

def main() -> None:
    parser = argparse.ArgumentParser(description="SecondBrain Web UI")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 3000)), help="server port (default: 3000)")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: 127.0.0.1)")
    parser.add_argument("--dev", action="store_true", help="enable auto-reload for development")
    args = parser.parse_args()

    try:
        import uvicorn
    except ImportError:
        print("\n  error: uvicorn is not installed.")
        print("  install it:  pip install 'fastapi[standard]'\n")
        sys.exit(1)

    print(f"\n  SecondBrain Web UI starting on http://{args.host}:{args.port}")
    print(f"  API docs: http://{args.host}:{args.port}/docs\n")

    os.chdir(str(_PROJECT_DIR))

    uvicorn.run(
        "web.api.app:app",
        host=args.host,
        port=args.port,
        reload=args.dev,
    )


if __name__ == "__main__":
    main()
