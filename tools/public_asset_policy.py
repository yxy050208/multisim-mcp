"""Shared path guard for public Git candidates and Python distribution members.

This is a conservative packaging check, not a determination of asset ownership.
Previously public source is unaffected; future private assets belong elsewhere.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath


PRIVATE_PRODUCT_DIRECTORIES = frozenset(
    {"commercial", "commercial_assets", "private", "proprietary", "paid", "agensi-products"}
)
ARCHIVE_SUFFIXES = (".zip", ".7z", ".rar", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")


def forbidden_public_asset_reason(path: str) -> str | None:
    """Classify a repository path or archive member without reading its contents."""
    normalized = path.replace("\\", "/").casefold()
    item = PurePosixPath(normalized)
    directories = item.parts if normalized.endswith("/") else item.parts[:-1]
    if any(part in PRIVATE_PRODUCT_DIRECTORIES for part in directories):
        return "private-product"
    if normalized.endswith(ARCHIVE_SUFFIXES):
        return "product-archive"
    if "analysis" in item.parts[:-1]:
        return "local-research"
    if re.search(r"\.ms\d+(?:\.xml)?$", normalized):
        return "native-design"
    if normalized.endswith((".chm", ".dll", ".tlb", ".whl")):
        return "local-binary"
    if "templates" in item.parts[:-1] and normalized.endswith(".xml"):
        return "extracted-template"
    return None
