from __future__ import annotations

import json
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from multisim_mcp.handoff_execution import (
    HANDOFF_KIND,
    execute_handoff,
    load_handoff,
    submit_handoff,
    validate_handoff,
)
from multisim_mcp.cli import main


def _payload() -> dict:
    netlist = "V1 in 0 1\nR1 in 0 1k\n.end\n"
    compiled = {
        "compiled_id": "compiled-1",
        "compiled_digest": "c" * 64,
        "spice_netlist": netlist,
    }
    netlist_approval = {
        "approval_id": "netlist-approval-1",
        "approval_digest": "b" * 64,
        "compiled_id": "compiled-1",
        "compiled_digest": "c" * 64,
        "state": "approved",
    }
    simulation_approval = {
        "approval_id": "simulation-approval-1",
        "approval_digest": "a" * 64,
        "netlist_approval_id": "netlist-approval-1",
        "netlist_approval_digest": "b" * 64,
        "compiled_id": "compiled-1",
        "compiled_digest": "c" * 64,
        "spec_digest": "d" * 64,
        "state": "approved",
    }
    spec = {
        "schema_version": 1,
        "title": "fixture",
        "netlist": netlist,
        "commands": "op",
        "requirements": [
            {
                "id": "input-min",
                "metric": "min",
                "signal": "V(in)",
                "operator": "at_least",
                "target": 0,
            }
        ],
        "theoretical_values": {},
    }
    return {
        "schema_version": 1,
        "kind": HANDOFF_KIND,
        "execution_started": False,
        "output_dir": "experiments/approved-plan",
        "steps": [
            {
                "step_id": "schematic",
                "order": 1,
                "tool": "create_schematic_from_netlist",
                "arguments": {
                    "netlist": netlist,
                    "output_ms14": "experiments/approved-plan/approved-plan.ms14",
                    "image_path": "experiments/approved-plan/approved-plan.png",
                    "probe_nets": [],
                    "include_experimental_probes": False,
                    "open_after_build": False,
                    "overwrite": False,
                    "executable_netlist": compiled,
                    "netlist_approval": netlist_approval,
                },
            },
            {
                "step_id": "simulation",
                "order": 2,
                "tool": "run_verified_circuit_experiment",
                "arguments": {
                    "spec": spec,
                    "output_dir": "experiments/approved-plan",
                    "timeout": 120,
                    "max_points": 2000,
                    "overwrite": False,
                    "executable_netlist": compiled,
                    "netlist_approval": netlist_approval,
                    "simulation_plan_approval": simulation_approval,
                },
            },
        ],
        "result_contract": {
            "expected_entry_kind": "experiment",
            "expected_path": "experiments/approved-plan",
            "identity_check": "path_manifest_and_approval_provenance",
        },
    }


class HandoffExecutionTest(unittest.TestCase):
    def test_validation_resolves_paths_and_preserves_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            handoff = validate_handoff(_payload(), tmp)
            self.assertEqual(
                handoff.output_dir,
                (Path(tmp) / "experiments" / "approved-plan").resolve(),
            )
            self.assertEqual(handoff.schematic["output_ms14"].endswith("approved-plan.ms14"), True)
            self.assertEqual(handoff.approval_identity["spec_digest"], "d" * 64)

    def test_validation_rejects_path_traversal_and_netlist_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            traversal = _payload()
            traversal["output_dir"] = "../outside"
            with self.assertRaises(ValueError):
                validate_handoff(traversal, tmp)

            drift = _payload()
            drift["steps"][1]["arguments"]["spec"]["netlist"] = "V1 in 0 2\n.end\n"
            with self.assertRaises(ValueError):
                validate_handoff(drift, tmp)

    def test_load_handoff_rejects_duplicate_json_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "handoff.json"
            path.write_text('{"schema_version": 1, "schema_version": 2}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_handoff(path)

    def test_execution_is_schematic_first_and_stops_before_simulation_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            handoff = validate_handoff(_payload(), tmp)
            calls: list[str] = []

            def schematic(**kwargs):
                calls.append("schematic")
                self.assertTrue(kwargs["output_ms14"].endswith("approved-plan.ms14"))
                return {"success": False, "error": {"type": "fixture"}}

            with patch("multisim_mcp.server.create_schematic_from_netlist", side_effect=schematic), patch(
                "multisim_mcp.server.run_verified_circuit_experiment"
            ) as simulation:
                result = execute_handoff(handoff)
            self.assertFalse(result["success"])
            self.assertEqual(result["stage"], "schematic")
            self.assertFalse(result["simulation_started"])
            self.assertEqual(calls, ["schematic"])
            simulation.assert_not_called()

    def test_execution_runs_simulation_after_successful_schematic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            handoff = validate_handoff(_payload(), tmp)
            calls: list[str] = []

            def schematic(**kwargs):
                calls.append("schematic")
                return {"success": True, "ms14": kwargs["output_ms14"]}

            def simulation(**kwargs):
                calls.append("simulation")
                self.assertEqual(kwargs["output_dir"], str(handoff.output_dir))
                return {"success": True, "experiment_id": "exp-fixture"}

            with patch("multisim_mcp.server.create_schematic_from_netlist", side_effect=schematic), patch(
                "multisim_mcp.server.run_verified_circuit_experiment", side_effect=simulation
            ):
                result = execute_handoff(handoff)
            self.assertTrue(result["success"])
            self.assertTrue(result["simulation_started"])
            self.assertEqual(calls, ["schematic", "simulation"])

    def test_simulation_failure_does_not_claim_started_when_runner_reports_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            handoff = validate_handoff(_payload(), tmp)

            with patch(
                "multisim_mcp.server.create_schematic_from_netlist",
                return_value={"success": True},
            ), patch(
                "multisim_mcp.server.run_verified_circuit_experiment",
                return_value={
                    "success": False,
                    "simulation_plan_approval": {"simulation_started": False},
                },
            ):
                result = execute_handoff(handoff)
            self.assertFalse(result["success"])
            self.assertEqual(result["stage"], "simulation")
            self.assertFalse(result["simulation_started"])

    def test_submit_mode_generates_schematic_then_enqueues_durable_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            handoff = validate_handoff(_payload(), tmp)
            calls: list[str] = []

            def schematic(**kwargs):
                calls.append("schematic")
                return {"success": True, "ms14": kwargs["output_ms14"]}

            def submit(**kwargs):
                calls.append("submit")
                self.assertEqual(kwargs["output_dir"], str(handoff.output_dir))
                self.assertEqual(kwargs["requirements"][0]["id"], "input-min")
                return {"success": True, "job_id": "job-fixture", "status_uri": "multisim://jobs/job-fixture"}

            with patch("multisim_mcp.server.create_schematic_from_netlist", side_effect=schematic), patch(
                "multisim_mcp.server.submit_circuit_experiment", side_effect=submit
            ):
                result = submit_handoff(handoff, job_timeout=600, heartbeat_timeout=180)
            self.assertTrue(result["success"])
            self.assertEqual(result["stage"], "queue")
            self.assertFalse(result["simulation_started"])
            self.assertEqual(calls, ["schematic", "submit"])

    def test_submit_rejects_invalid_queue_limits_before_writing_schematic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            handoff = validate_handoff(_payload(), tmp)
            with patch("multisim_mcp.server.create_schematic_from_netlist") as schematic:
                with self.assertRaises(ValueError):
                    submit_handoff(handoff, job_timeout=120, heartbeat_timeout=180)
            schematic.assert_not_called()

    def test_validation_rejects_invalid_experiment_spec_before_any_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _payload()
            payload["steps"][1]["arguments"]["spec"]["requirements"] = None
            with self.assertRaises(ValueError):
                validate_handoff(payload, tmp)

    def test_validation_rejects_unsafe_commands_before_any_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _payload()
            payload["steps"][1]["arguments"]["spec"]["commands"] = "write bad.txt"
            with self.assertRaises(ValueError):
                validate_handoff(payload, tmp)

    def test_validation_rejects_runtime_limits_outside_server_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            too_many_points = _payload()
            too_many_points["steps"][1]["arguments"]["max_points"] = 100_001
            with self.assertRaises(ValueError):
                validate_handoff(too_many_points, tmp)

            too_long = _payload()
            too_long["steps"][1]["arguments"]["timeout"] = 3600.1
            with self.assertRaises(ValueError):
                validate_handoff(too_long, tmp)

            huge = _payload()
            huge["steps"][1]["arguments"]["timeout"] = 10**1000
            with self.assertRaises(ValueError):
                validate_handoff(huge, tmp)

    def test_cli_defaults_to_validation_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            handoff_path = root / "handoff.json"
            handoff_path.write_text(
                json.dumps(_payload(), ensure_ascii=False), encoding="utf-8"
            )
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "execute-handoff",
                        "--handoff",
                        str(handoff_path),
                        "--root",
                        str(root),
                        "--json",
                    ]
                )
            result = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(result["mode"], "validate")
            self.assertFalse(result["simulation_started"])

    def test_cli_submit_requires_explicit_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            handoff_path = root / "handoff.json"
            handoff_path.write_text(
                json.dumps(_payload(), ensure_ascii=False), encoding="utf-8"
            )
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "execute-handoff",
                        "--handoff",
                        str(handoff_path),
                        "--root",
                        str(root),
                        "--submit",
                        "--json",
                    ]
                )
            result = json.loads(output.getvalue())
            self.assertEqual(exit_code, 2)
            self.assertFalse(result["success"])
            self.assertIn("--submit requires --confirm", result["error"]["message"])


if __name__ == "__main__":
    unittest.main()
