"""Tests for the approved search-plan to durable-job hand-off."""

from __future__ import annotations

import json
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from multisim_mcp.eda_core import CircuitDesign
from multisim_mcp.job_engine import ExperimentJobManager
from multisim_mcp.search_plan_approval import (
    SearchPlanApprovalStore,
    build_search_plan_binding,
    write_search_plan_token,
)
from multisim_mcp.search_plan_submission import (
    derive_approved_search_spec,
    submit_approved_search_plan,
)
from multisim_mcp.cli import main


def _design() -> CircuitDesign:
    return CircuitDesign.from_dict(
        {
            "schema_version": 1,
            "design_id": "approval-divider",
            "title": "Approval divider",
            "revision": 1,
            "components": [
                {
                    "refdes": "V1",
                    "kind": "V",
                    "nodes": ["in", "0"],
                    "value": "10",
                    "model": None,
                    "parameters": {},
                    "annotations": {},
                },
                {
                    "refdes": "R1",
                    "kind": "R",
                    "nodes": ["in", "out"],
                    "value": "1k",
                    "model": None,
                    "parameters": {},
                    "annotations": {},
                },
                {
                    "refdes": "R2",
                    "kind": "R",
                    "nodes": ["out", "0"],
                    "value": "1k",
                    "model": None,
                    "parameters": {},
                    "annotations": {},
                },
            ],
            "parameters": {},
            "annotations": {},
            "source_netlist": "V1 in 0 10\nR1 in out 1k\nR2 out 0 1k\n.end\n",
        }
    )


def _source_spec() -> dict[str, object]:
    return {
        "schema_version": 1,
        "title": "Approval-bound divider search",
        "variables": [{"refdes": "R2", "values": ["1k", "2k"]}],
        "commands": "op",
        "requirements": [
            {
                "id": "vout",
                "metric": "mean",
                "signal": "V(out)",
                "operator": "between",
                "lower": 1.0,
                "upper": 9.0,
                "unit": "V",
            }
        ],
        "objective": {"requirement_id": "vout", "goal": "maximize"},
        "max_experiments": 3,
    }


def _draft() -> dict[str, object]:
    return {
        "available": True,
        "draft_kind": "sensitivity-guided-search-v1",
        "source_optimization_kind": "design-optimization",
        "review_required": True,
        "read_only": True,
        "executable": False,
        "max_experiments": 3,
        "preflight": {
            "status": "ready_for_review",
            "approval_required": True,
            "execution_enabled": False,
        },
        "parameters": [
            {"name": "R2", "values": ["1k", "2k"], "budget_share": 2}
        ],
    }


class SearchPlanSubmissionTest(unittest.TestCase):
    def _issue(self, root: Path) -> tuple[dict[str, object], str]:
        design = _design().to_dict()
        source_spec = _source_spec()
        draft = _draft()
        binding = build_search_plan_binding(
            entry_handle="entry-design-1",
            optimization_id="design-1",
            source_optimization_kind="design-optimization",
            source_design=design,
            source_spec=source_spec,
            spec_draft=draft,
            exploration_budget=2,
            max_experiments=3,
        )
        issued = SearchPlanApprovalStore(root / "approvals").issue(binding, ttl_seconds=60)
        return issued, str(issued["approval_token"])

    def test_submission_queues_derived_spec_and_consumes_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            issued, token = self._issue(root)
            manager = ExperimentJobManager(root / "jobs", start=False)
            try:
                result = submit_approved_search_plan(
                    design=_design().to_dict(),
                    source_spec=_source_spec(),
                    spec_draft=_draft(),
                    approval_token=token,
                    entry_handle="entry-design-1",
                    optimization_id="design-1",
                    source_optimization_kind="design-optimization",
                    exploration_budget=2,
                    max_experiments=3,
                    output_dir=str(root / "output"),
                    approval_store=str(root / "approvals"),
                    job_manager=manager,
                    timeout_per_experiment=1.0,
                    max_points=100,
                    job_timeout=5.0,
                    heartbeat_timeout=10.0,
                )
                self.assertEqual(result["state"], "queued")
                self.assertTrue(result["approval_consumed"])
                self.assertFalse(result["execution_started"])
                self.assertEqual(result["approval_id"], issued["approval_id"])
                record_path = root / "jobs" / "records" / f"{result['job_id']}.json"
                record = json.loads(record_path.read_text(encoding="utf-8"))
                self.assertEqual(
                    record["spec"]["optimization_spec"]["variables"][0]["values"],
                    ["1k", "2k"],
                )
                self.assertEqual(record["spec"]["optimization_spec"]["max_experiments"], 3)
                self.assertNotIn(token, json.dumps(record))
                with self.assertRaisesRegex(ValueError, "already been consumed"):
                    SearchPlanApprovalStore(root / "approvals").inspect(token)
            finally:
                manager.shutdown()

    def test_source_mismatch_is_rejected_before_claim_and_topology_draft_is_not_scalar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _issued, token = self._issue(root)
            manager = ExperimentJobManager(root / "jobs", start=False)
            try:
                with self.assertRaisesRegex(ValueError, "does not match current source_spec_sha256"):
                    submit_approved_search_plan(
                        design=_design().to_dict(),
                        source_spec={**_source_spec(), "title": "tampered"},
                        spec_draft=_draft(),
                        approval_token=token,
                        entry_handle="entry-design-1",
                        optimization_id="design-1",
                        source_optimization_kind="design-optimization",
                        exploration_budget=2,
                        max_experiments=3,
                        output_dir=str(root / "output"),
                        approval_store=str(root / "approvals"),
                        job_manager=manager,
                    )
            finally:
                manager.shutdown()

    def test_global_topology_draft_requires_explicit_operations(self) -> None:
        topology_spec = {
            "schema_version": 1,
            "title": "topology",
            "dimensions": [
                {
                    "id": "load",
                    "kind": "topology_choice",
                    "include_baseline": True,
                    "choices": [],
                }
            ],
            "commands": "op",
            "requirements": [],
            "objectives": [],
            "max_experiments": 2,
        }
        draft = {"max_experiments": 2, "parameters": [{"name": "load", "values": ["x"]}]}
        with self.assertRaisesRegex(ValueError, "explicit operations"):
            derive_approved_search_spec(
                topology_spec, draft, _design(), "global-optimization"
            )

    def test_global_component_value_draft_is_normalized_to_options(self) -> None:
        source = _source_spec()
        global_spec = {
            "schema_version": 1,
            "title": "Global component-value search",
            "dimensions": [
                {
                    "id": "r2-value",
                    "kind": "component_value",
                    "refdes": "R2",
                    "values": ["1k", "2k"],
                }
            ],
            "commands": source["commands"],
            "requirements": source["requirements"],
            "objectives": [{"requirement_id": "vout", "goal": "maximize"}],
            "max_experiments": 3,
        }
        draft = {
            "max_experiments": 3,
            "parameters": [{"name": "R2-VALUE", "values": ["1k", "2k"]}],
        }
        derived = derive_approved_search_spec(
            global_spec, draft, _design(), "global-optimization"
        )
        self.assertEqual(derived["dimensions"][0]["options"], ["1k", "2k"])

    def test_cli_submit_emits_queue_handle_without_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design = _design().to_dict()
            source_spec = _source_spec()
            draft = _draft()
            design_path = root / "design.json"
            spec_path = root / "source-spec.json"
            draft_path = root / "draft.json"
            design_path.write_text(json.dumps(design), encoding="utf-8")
            spec_path.write_text(json.dumps(source_spec), encoding="utf-8")
            draft_path.write_text(json.dumps(draft), encoding="utf-8")
            binding = build_search_plan_binding(
                entry_handle="entry-design-1",
                optimization_id="design-1",
                source_optimization_kind="design-optimization",
                source_design=design,
                source_spec=source_spec,
                spec_draft=draft,
                exploration_budget=2,
                max_experiments=3,
            )
            approval_store = root / "approvals"
            issued = SearchPlanApprovalStore(approval_store).issue(binding, ttl_seconds=60)
            token_path = root / "approval-token.txt"
            write_search_plan_token(token_path, str(issued["approval_token"]))
            output = io.StringIO()
            with redirect_stdout(output):
                return_code = main(
                    [
                        "search-plan-submit",
                        "--spec-draft",
                        str(draft_path),
                        "--source-design",
                        str(design_path),
                        "--source-spec",
                        str(spec_path),
                        "--entry-handle",
                        "entry-design-1",
                        "--optimization-id",
                        "design-1",
                        "--optimization-kind",
                        "design-optimization",
                        "--exploration-budget",
                        "2",
                        "--max-experiments",
                        "3",
                        "--output",
                        str(root / "output"),
                        "--approval-token-file",
                        str(token_path),
                        "--approval-store",
                        str(approval_store),
                        "--job-dir",
                        str(root / "jobs"),
                        "--timeout",
                        "1",
                        "--job-timeout",
                        "5",
                        "--heartbeat-timeout",
                        "10",
                        "--json",
                    ]
                )
            self.assertEqual(return_code, 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["state"], "queued")
            self.assertTrue(result["queue_only"])
            self.assertFalse(result["approval_token_exposed"])
            self.assertNotIn(str(issued["approval_token"]), output.getvalue())


if __name__ == "__main__":
    unittest.main()
