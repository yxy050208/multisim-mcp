"""Application service shared by CLI, MCP adapters, and future Workbench flows.

The durable patch primitives remain in their focused modules.  This facade is
the transport-neutral boundary that selects apply/revert, verified execution,
and recovery while preserving the existing approval and journal contracts.
It intentionally contains no model or filesystem policy of its own.
"""

from __future__ import annotations

from typing import Any

from .design_patch_transactions import (
    DEFAULT_APPROVAL_TTL_SECONDS,
    apply_patch_transaction,
    approve_patch_apply,
    approve_patch_revert,
    recover_patch_transaction,
    revert_patch_transaction,
)
from .design_patch_workflow import (
    approve_verified_patch_application,
    execute_verified_patch_application,
    recover_verified_patch_workflow,
)


class DesignPatchApplicationService:
    """Route all durable DesignPatch transitions through one application API."""

    def approve_basic(
        self,
        design_path: str,
        *,
        patch_path: str | None,
        revert_transaction: str | None,
        output_path: str | None,
        in_place: bool,
        receipt_path: str,
        regenerate_source_netlist: bool,
        approval_store: str | None = None,
        ttl_seconds: int = DEFAULT_APPROVAL_TTL_SECONDS,
    ) -> dict[str, Any]:
        if (patch_path is None) == (revert_transaction is None):
            raise ValueError("provide exactly one of patch_path or revert_transaction")
        if patch_path is not None:
            return approve_patch_apply(
                design_path,
                patch_path,
                output_path=output_path,
                in_place=in_place,
                receipt_path=receipt_path,
                regenerate_source_netlist=regenerate_source_netlist,
                approval_store=approval_store,
                ttl_seconds=ttl_seconds,
            )
        assert revert_transaction is not None
        return approve_patch_revert(
            design_path,
            revert_transaction,
            output_path=output_path,
            in_place=in_place,
            receipt_path=receipt_path,
            regenerate_source_netlist=regenerate_source_netlist,
            approval_store=approval_store,
            ttl_seconds=ttl_seconds,
        )

    def execute_basic(
        self,
        design_path: str,
        *,
        patch_path: str | None,
        transaction_path: str | None,
        output_path: str | None,
        in_place: bool,
        receipt_path: str,
        regenerate_source_netlist: bool,
        approval_token: str,
        approval_store: str | None = None,
    ) -> dict[str, Any]:
        if (patch_path is None) == (transaction_path is None):
            raise ValueError("provide exactly one of patch_path or transaction_path")
        if patch_path is not None:
            return apply_patch_transaction(
                design_path,
                patch_path,
                output_path=output_path,
                in_place=in_place,
                receipt_path=receipt_path,
                regenerate_source_netlist=regenerate_source_netlist,
                approval_token=approval_token,
                approval_store=approval_store,
            )
        assert transaction_path is not None
        return revert_patch_transaction(
            design_path,
            transaction_path,
            output_path=output_path,
            in_place=in_place,
            receipt_path=receipt_path,
            regenerate_source_netlist=regenerate_source_netlist,
            approval_token=approval_token,
            approval_store=approval_store,
        )

    def recover_basic(
        self,
        *,
        journal_path: str | None,
        target_path: str | None,
        action: str,
        approval_store: str | None = None,
    ) -> dict[str, Any]:
        if (journal_path is None) == (target_path is None):
            raise ValueError("provide exactly one of journal_path or target_path")
        if action not in {"auto", "commit", "rollback"}:
            raise ValueError("patch recovery action must be auto, commit, or rollback")
        if journal_path is not None:
            return recover_patch_transaction(
                journal_path=journal_path,
                action=action,
                approval_store=approval_store,
            )
        assert target_path is not None
        return recover_patch_transaction(
            target_path=target_path,
            action=action,
            approval_store=approval_store,
        )

    def approve_verified(
        self,
        design_path: str,
        patch_path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return approve_verified_patch_application(design_path, patch_path, **kwargs)

    def execute_verified(
        self,
        experiment_service: Any,
        design_path: str,
        patch_path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return execute_verified_patch_application(
            experiment_service, design_path, patch_path, **kwargs
        )

    def recover_verified(self, workflow_manifest: str) -> dict[str, Any]:
        return recover_verified_patch_workflow(workflow_manifest)


__all__ = ["DesignPatchApplicationService"]
