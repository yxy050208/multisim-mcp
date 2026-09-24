from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from multisim_mcp.search_plan_approval import (
    SearchPlanApprovalStore,
    build_search_plan_binding,
    read_search_plan_token,
    write_search_plan_token,
)


def _draft() -> dict[str, object]:
    return {
        "available": True,
        "draft_kind": "sensitivity-guided-search-v1",
        "source_optimization_kind": "global-optimization",
        "review_required": True,
        "read_only": True,
        "executable": False,
        "max_experiments": 4,
        "preflight": {
            "status": "ready_for_review",
            "approval_required": True,
            "execution_enabled": False,
        },
        "parameters": [
            {"name": "R1", "values": ["1k", "1.2k"], "budget_share": 3}
        ],
    }


class SearchPlanApprovalTest(unittest.TestCase):
    def test_exact_binding_and_one_time_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft = _draft()
            binding = build_search_plan_binding(
                entry_handle="entry-global-1",
                optimization_id="global-1",
                source_optimization_kind="global-optimization",
                source_design={"design_id": "fixture"},
                source_spec={"title": "fixture"},
                spec_draft=draft,
                exploration_budget=3,
                max_experiments=4,
            )
            store = SearchPlanApprovalStore(root / "records")
            issued = store.issue(binding, ttl_seconds=60)
            token = issued["approval_token"]
            record = next((root / "records").glob("*.json")).read_text(encoding="utf-8")
            self.assertNotIn(token, record)
            self.assertEqual(store.inspect(token, binding)["status"], "approved")
            with store.claim(token, binding) as claim:
                claim.consume("search-submit-0123456789abcdef0123456789abcdef")
            with self.assertRaisesRegex(ValueError, "already been consumed"):
                store.inspect(token, binding)

    def test_digest_and_budget_mismatch_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            draft = _draft()
            binding = build_search_plan_binding(
                entry_handle="entry-global-1",
                optimization_id="global-1",
                source_optimization_kind="global-optimization",
                source_design={"design_id": "fixture"},
                source_spec={"title": "fixture"},
                spec_draft=draft,
                exploration_budget=3,
                max_experiments=4,
            )
            store = SearchPlanApprovalStore(Path(tmp) / "records")
            issued = store.issue(binding)
            changed = dict(binding)
            changed["exploration_budget"] = 2
            with self.assertRaisesRegex(ValueError, "exploration_budget"):
                store.inspect(issued["approval_token"], changed)

    def test_token_file_is_private_and_non_replacing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft = _draft()
            binding = build_search_plan_binding(
                entry_handle="entry-global-1",
                optimization_id="global-1",
                source_optimization_kind="global-optimization",
                source_design={"design_id": "fixture"},
                source_spec={"title": "fixture"},
                spec_draft=draft,
                exploration_budget=3,
                max_experiments=4,
            )
            token = SearchPlanApprovalStore(root / "records").issue(binding)["approval_token"]
            output = root / "token.txt"
            write_search_plan_token(output, token)
            self.assertEqual(read_search_plan_token(output), token)
            with self.assertRaises(FileExistsError):
                write_search_plan_token(output, token)

    def test_executable_or_unbounded_draft_cannot_be_approved(self) -> None:
        draft = _draft()
        draft["executable"] = True
        with self.assertRaisesRegex(ValueError, "read-only"):
            build_search_plan_binding(
                entry_handle="entry-global-1",
                optimization_id="global-1",
                source_optimization_kind="global-optimization",
                source_design={"design_id": "fixture"},
                source_spec={"title": "fixture"},
                spec_draft=draft,
                exploration_budget=3,
                max_experiments=4,
            )


if __name__ == "__main__":
    unittest.main()
