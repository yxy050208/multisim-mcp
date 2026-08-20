"""Transport-neutral application service for capability-driven EDA backends."""

from __future__ import annotations

from collections.abc import Iterable

from .eda_backend import (
    BackendCapabilities,
    BackendDiagnostic,
    BackendExecution,
    EdaBackend,
    SchematicRequest,
    SimulationRequest,
)
from .eda_core import CircuitDesign


class EdaApplicationService:
    """Register EDA backends and validate all cross-boundary dispatch results."""

    def __init__(self, backends: Iterable[EdaBackend] = ()) -> None:
        self._backends: dict[str, EdaBackend] = {}
        for backend in backends:
            self.register_backend(backend)

    def register_backend(self, backend: EdaBackend) -> None:
        if not isinstance(backend, EdaBackend):
            raise ValueError("backend must implement EdaBackend")
        backend_id = backend.backend_id
        if backend_id in self._backends:
            raise ValueError(f"backend {backend_id!r} is already registered")
        capabilities = backend.discover_capabilities()
        if capabilities.backend_id != backend_id:
            raise ValueError("backend capability id does not match backend_id")
        self._backends[backend_id] = backend

    def backend_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._backends))

    def discover_backends(self) -> tuple[BackendCapabilities, ...]:
        return tuple(
            self._backends[backend_id].discover_capabilities()
            for backend_id in self.backend_ids()
        )

    def validate_design(
        self, backend_id: str, design: CircuitDesign
    ) -> tuple[BackendDiagnostic, ...]:
        backend = self._backend(backend_id)
        self._require_operation(backend, "validate")
        diagnostics = backend.validate_design(design)
        if any(not isinstance(item, BackendDiagnostic) for item in diagnostics):
            raise RuntimeError("backend returned invalid diagnostics")
        return tuple(diagnostics)

    def create_schematic(
        self, backend_id: str, request: SchematicRequest
    ) -> BackendExecution:
        backend = self._backend(backend_id)
        self._require_operation(backend, "schematic")
        result = backend.create_schematic(request)
        self._validate_execution(backend_id, "schematic", request.design, result)
        return result

    def simulate(
        self, backend_id: str, request: SimulationRequest
    ) -> BackendExecution:
        backend = self._backend(backend_id)
        self._require_operation(backend, "simulate")
        result = backend.simulate(request)
        self._validate_execution(backend_id, "simulate", request.design, result)
        return result

    def _backend(self, backend_id: str) -> EdaBackend:
        try:
            return self._backends[backend_id]
        except KeyError as exc:
            available = ", ".join(self.backend_ids()) or "none"
            raise KeyError(
                f"unknown EDA backend {backend_id!r}; available backends: {available}"
            ) from exc

    @staticmethod
    def _require_operation(backend: EdaBackend, operation: str) -> None:
        if operation not in backend.discover_capabilities().operations:
            raise ValueError(
                f"backend {backend.backend_id!r} does not support {operation!r}"
            )

    @staticmethod
    def _validate_execution(
        backend_id: str,
        operation: str,
        design: CircuitDesign,
        result: BackendExecution,
    ) -> None:
        if not isinstance(result, BackendExecution):
            raise RuntimeError("backend returned an invalid execution result")
        if result.backend_id != backend_id or result.operation != operation:
            raise RuntimeError("backend execution identity does not match the request")
        if result.artifacts.design_id != design.design_id:
            raise RuntimeError("backend artifacts belong to a different design")


__all__ = ["EdaApplicationService"]
