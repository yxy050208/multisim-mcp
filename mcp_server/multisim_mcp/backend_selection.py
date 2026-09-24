"""Validated runtime selection for complete experiment execution backends."""

from __future__ import annotations

import os
from collections.abc import Mapping


EXPERIMENT_BACKEND_ENV = "MULTISIM_MCP_EXPERIMENT_BACKEND"
EXPERIMENT_BACKENDS = ("multisim", "ngspice")


def normalize_experiment_backend(value: str | None) -> str:
    backend = (value or "multisim").strip().casefold() or "multisim"
    if backend not in EXPERIMENT_BACKENDS:
        raise ValueError(
            f"unknown experiment backend {backend!r}; choose one of: "
            + ", ".join(EXPERIMENT_BACKENDS)
        )
    return backend


def selected_experiment_backend(
    environment: Mapping[str, str] | None = None,
) -> str:
    source = os.environ if environment is None else environment
    return normalize_experiment_backend(source.get(EXPERIMENT_BACKEND_ENV))


__all__ = [
    "EXPERIMENT_BACKEND_ENV",
    "EXPERIMENT_BACKENDS",
    "normalize_experiment_backend",
    "selected_experiment_backend",
]
