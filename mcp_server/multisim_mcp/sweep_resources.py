"""Safe MCP resource handles for completed experiment sweeps."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Final


_SCHEME: Final = "multisim://sweeps"
_ID_PATTERN: Final = re.compile(r"^sweep-[0-9a-f]{24}$")
_FILES: Final = {"summary": "summary.json", "data": "data.csv"}
_DEFAULT_MAX_BYTES: Final = 16 * 1024 * 1024
_registry: dict[str, Path] = {}
_lock = threading.RLock()


def _stable_id(root: Path) -> str:
    normalized = os.path.normcase(str(root.resolve()))
    return "sweep-" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def _safe_file(root: Path, name: str) -> Path:
    filename = _FILES.get(name)
    if filename is None:
        raise ValueError(f"Unknown sweep artifact: {name}")
    path = (root / filename).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("Sweep artifact escapes its output directory") from exc
    if not path.is_file() or path.stat().st_size <= 0:
        raise FileNotFoundError(f"Sweep artifact is missing or empty: {filename}")
    return path


def register_sweep(output_dir: str) -> dict[str, Any]:
    root = Path(output_dir).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Sweep output directory does not exist: {root}")
    for name in _FILES:
        _safe_file(root, name)
    sweep_id = _stable_id(root)
    with _lock:
        _registry[sweep_id] = root
    return {
        "success": True,
        "sweep_id": sweep_id,
        "output_dir": str(root),
        "resources": {name: f"{_SCHEME}/{sweep_id}/{name}" for name in _FILES},
    }


def _root(sweep_id: str) -> Path:
    if not _ID_PATTERN.fullmatch(sweep_id):
        raise ValueError("Invalid sweep resource handle")
    with _lock:
        root = _registry.get(sweep_id)
    if root is None:
        raise KeyError("Unknown sweep resource handle; call register_sweep_artifacts first")
    return root


def read_sweep_text(sweep_id: str, name: str) -> str:
    path = _safe_file(_root(sweep_id), name)
    raw_limit = os.environ.get("MULTISIM_MCP_RESOURCE_MAX_BYTES", "").strip()
    try:
        limit = int(raw_limit) if raw_limit else _DEFAULT_MAX_BYTES
    except ValueError as exc:
        raise ValueError("MULTISIM_MCP_RESOURCE_MAX_BYTES must be an integer") from exc
    if limit < 1:
        raise ValueError("MULTISIM_MCP_RESOURCE_MAX_BYTES must be positive")
    if path.stat().st_size > limit:
        raise ValueError("Sweep artifact exceeds the resource size limit")
    return path.read_text(encoding="utf-8", errors="replace")


def read_sweep_summary(sweep_id: str) -> dict[str, Any]:
    value = json.loads(read_sweep_text(sweep_id, "summary"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("Invalid sweep summary schema")
    return value


def clear_sweep_registry() -> None:
    with _lock:
        _registry.clear()
