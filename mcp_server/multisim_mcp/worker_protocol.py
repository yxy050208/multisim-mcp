"""Dependency-free constants shared by the worker frontend and subprocess."""

from typing import Final


PROTOCOL_VERSION: Final = 1
MAX_REQUEST_BYTES: Final = 16 * 1024 * 1024


__all__ = ["MAX_REQUEST_BYTES", "PROTOCOL_VERSION"]
