"""Single-process local Workbench application server.

The regular ``workbench-api`` command intentionally exposes only JSON routes.
This module adds a small static-file layer around the same loopback server so a
local user can start the browser workbench and its API with one command.  It
does not add remote hosting, file browsing, or execution endpoints.
"""

from __future__ import annotations

import mimetypes
import socket
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .workbench_api import (
    DEFAULT_WORKBENCH_API_HOST,
    DEFAULT_WORKBENCH_API_PORT,
    WorkbenchHTTPServer,
    _WorkbenchRequestHandler,
    _loopback_host,
    _port,
)


_MAX_STATIC_BYTES = 16 * 1024 * 1024


def _safe_ui_root(path: str | Path) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError("workbench UI root must not be a symlink")
    root = candidate.resolve()
    if not root.is_dir():
        raise ValueError(f"workbench UI root is not a directory: {root}")
    index = root / "index.html"
    if not index.is_file() or index.is_symlink():
        raise ValueError(f"workbench UI root is missing index.html: {root}")
    return root


def _safe_static_file(root: Path, route: str) -> Path | None:
    """Resolve a route below ``root`` without symlink or traversal escapes."""
    try:
        decoded = unquote(route)
    except UnicodeError:
        return None
    if not decoded.startswith("/") or "\x00" in decoded:
        return None
    relative = decoded.lstrip("/") or "index.html"
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    cursor = root
    try:
        for part in Path(relative).parts:
            cursor = cursor / part
            if cursor.is_symlink():
                return None
    except OSError:
        return None
    if candidate.is_dir():
        candidate = candidate / "index.html"
    if candidate.is_file() and not candidate.is_symlink():
        return candidate
    # React/Vite history routes have no physical file.  Asset misses should
    # remain a 404 so typos do not silently render the application shell.
    if "." not in Path(relative).name:
        fallback = root / "index.html"
        return fallback if fallback.is_file() and not fallback.is_symlink() else None
    return None


class _WorkbenchAppRequestHandler(_WorkbenchRequestHandler):
    """Serve the API through the parent handler and UI assets locally."""

    def _send_static(self, path: Path) -> None:
        try:
            body = path.read_bytes()
        except OSError:
            self.send_error(404, "workbench asset is unavailable")
            return
        if len(body) > _MAX_STATIC_BYTES:
            self.send_error(413, "workbench asset exceeds the size limit")
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {
            "application/javascript",
            "application/json",
            "image/svg+xml",
        }:
            content_type = f"{content_type}; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        for key, value in self._cors_headers().items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        route = urlsplit(self.path).path
        if route.startswith("/api/"):
            super().do_GET()
            return
        path = _safe_static_file(self.server.ui_root, route)
        if path is None:
            self.send_error(404, "workbench asset was not found")
            return
        self._send_static(path)


class WorkbenchAppHTTPServer(WorkbenchHTTPServer):
    """Typed server state for the combined UI/API process."""

    def __init__(
        self,
        server_address: tuple[str, int] | tuple[str, int, int, int],
        project_root: str,
        ui_root: str,
        *,
        verify: bool,
        max_entries: int,
        max_depth: int,
    ) -> None:
        # WorkbenchHTTPServer currently fixes its handler class to the API-only
        # handler, so initialise the stdlib base directly for this variant.
        from http.server import ThreadingHTTPServer

        ThreadingHTTPServer.__init__(self, server_address, _WorkbenchAppRequestHandler)
        self.project_root = project_root
        self.ui_root = _safe_ui_root(ui_root)
        self.verify = verify
        self.max_entries = max_entries
        self.max_depth = max_depth


class IPv6WorkbenchAppHTTPServer(WorkbenchAppHTTPServer):
    address_family = socket.AF_INET6


def create_workbench_app_server(
    project_root: str,
    ui_root: str | Path,
    *,
    host: str = DEFAULT_WORKBENCH_API_HOST,
    port: int = DEFAULT_WORKBENCH_API_PORT,
    verify: bool = True,
    max_entries: int = 256,
    max_depth: int = 5,
) -> WorkbenchAppHTTPServer:
    """Create, but do not start, the combined local application server."""
    normalized_host = _loopback_host(host)
    _port(port)
    # Reuse the API constructor's strict project validation and limits.
    from .project_inspection import inspect_project

    root = Path(project_root).expanduser().resolve()
    inspect_project(root, verify=False, max_entries=max_entries, max_depth=max_depth)
    server_class = (
        IPv6WorkbenchAppHTTPServer if normalized_host == "::1" else WorkbenchAppHTTPServer
    )
    address = (normalized_host, port, 0, 0) if normalized_host == "::1" else (normalized_host, port)
    return server_class(
        address,
        str(root),
        str(_safe_ui_root(ui_root)),
        verify=verify,
        max_entries=max_entries,
        max_depth=max_depth,
    )


def serve_workbench_app(
    project_root: str,
    ui_root: str | Path,
    *,
    host: str = DEFAULT_WORKBENCH_API_HOST,
    port: int = DEFAULT_WORKBENCH_API_PORT,
    verify: bool = True,
    max_entries: int = 256,
    max_depth: int = 5,
    ready=None,
) -> None:
    """Serve the combined local app until interrupted."""
    server = create_workbench_app_server(
        project_root,
        ui_root,
        host=host,
        port=port,
        verify=verify,
        max_entries=max_entries,
        max_depth=max_depth,
    )
    if ready is not None:
        ready(server)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        return
    finally:
        server.server_close()


__all__ = [
    "WorkbenchAppHTTPServer",
    "create_workbench_app_server",
    "serve_workbench_app",
]
