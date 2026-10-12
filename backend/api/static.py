"""
Static file serving for the built frontend (frontend/dist), so one process serves UI + API + WebSocket.

Pure path-resolution logic lives here (no sockets) so the security-sensitive part is unit-testable:
  * only files inside the static root are ever served (`..`, encoded `..`, backslashes, NUL bytes and
    symlinks pointing outside are all refused -- the resolved real path must stay inside the root),
  * /api/... and /ws/... never fall through to the UI,
  * unknown extensionless paths (client-side routes like /experiments/abc123) get index.html so a page
    refresh or deep link works; unknown paths WITH an extension (a missing .js) stay 404 rather than
    being answered with HTML.
"""

from __future__ import annotations

import mimetypes
import os
from typing import Optional, Tuple
from urllib.parse import unquote

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("application/json", ".map")


def resolve_static(static_dir: str, url_path: str) -> Optional[str]:
    """Absolute path of the file to serve for `url_path`, or None for 404."""
    path = unquote(url_path)
    if "\x00" in path or "\\" in path:
        return None
    if path == "/api" or path.startswith("/api/") or path == "/ws" or path.startswith("/ws/"):
        return None
    root = os.path.realpath(static_dir)
    index = os.path.join(root, "index.html")
    rel = path.lstrip("/")
    if not rel:
        return index if os.path.isfile(index) else None
    candidate = os.path.realpath(os.path.join(root, rel))  # resolves symlinks and ..
    if os.path.commonpath([root, candidate]) != root:
        return None
    if os.path.isfile(candidate):
        return candidate
    if "." not in os.path.basename(path.rstrip("/")) and os.path.isfile(index):
        return index  # client-side route
    return None


def describe(static_dir: str, file_path: str) -> Tuple[str, str]:
    """(content-type, cache-control) for a resolved file."""
    ctype = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
    if ctype.startswith("text/") or ctype in ("application/javascript", "application/json", "image/svg+xml"):
        ctype += "; charset=utf-8"
    rel = os.path.relpath(file_path, os.path.realpath(static_dir)).replace(os.sep, "/")
    # Vite fingerprints everything under assets/ (content hash in the name), so it can be cached forever;
    # index.html must always be revalidated or a rebuild would never show up.
    cache = "public, max-age=31536000, immutable" if rel.startswith("assets/") else "no-cache"
    return ctype, cache
