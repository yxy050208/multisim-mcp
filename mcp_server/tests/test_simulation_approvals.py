"""Tests for the simulation-plan approval boundary."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from multisim_mcp import server
from multisim_mcp.executable_approvals import approve_executable_netlist
from multisim_mcp.simulation_approvals import (
    approve_simulation_plan,
    build_experiment_approval_provenance,
    validate_experiment_approval_provenance,
    validate_simulation_plan_approval,
)
from tests.test_executable_approvals import _compiled


def _netlist_approval(preview: dict[str, object]) -> dict[str, object]:
    return approve_executable_netlist(
        preview,
        {
            "approved": True,
            "compiled_id": preview["compiled_id"],
            "compiled_digest": preview["compiled_digest"],
            "confirm_components": True,
            "confirm_topology": True,
            "confirm_calculated_values": True,
            "confirm_spice": True,
        },
    )


def _spec(preview: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "title": "approved simulation plan",
        "netlist": preview["spice_netlist"],
        "commands": " op ",
        "requirements": [
            {
                "id": "input-min",
                "metric": "min",
                "signal": "V(in)",
                "operator": "between",
                "lower": 0,
                "upper": 5,
            }
        ],
        "theoretical_values": {"input-min": 0},
    }


class SimulationPlanApprovalTest(unittest.TestCase):
    def _approved_artifact(self) -> dict[str, object]:
        preview = _compiled()
        netlist_approval = _netlist_approval(preview)
        return approve_simulation_plan(
            preview,
            netlist_approval,
            _spec(preview),
            {
                "approved": True,
                "netlist_approval_id": netlist_approval["approval_id"],
                "netlist_approval_digest": netlist_approval["approval_digest"],
                "confirm_netlist": True,
                "confirm_commands": True,
                "confirm_measurements": True,
                "confirm_limits": True,
            },
        )

    def test_projects_only_stable_approval_identity_into_provenance(self) -> None:
        artifact = self._approved_artifact()
        provenance = build_experiment_approval_provenance(artifact)
        self.assertEqual(provenance["kind"], "multisim-mcp-approved-simulation-provenance")
        self.assertEqual(
            set(provenance),
            {
                "schema_version",
                "kind",
                "simulation_plan_approval_id",
                "simulation_plan_approval_digest",
                "netlist_approval_id",
                "netlist_approval_digest",
                "compiled_id",
                "compiled_digest",
                "design_id",
                "design_digest",
                "spice_sha256",
                "spec_digest",
            },
        )
        self.assertEqual(validate_experiment_approval_provenance(provenance), provenance)
        self.assertNotIn("experiment_spec", provenance)
        self.assertNotIn("review_note", provenance)

    def test_rejects_tampered_provenance(self) -> None:
        provenance = build_experiment_approval_provenance(self._approved_artifact())
        tampered = dict(provenance)
        tampered["spec_digest"] = "not-a-digest"
        with self.assertRaisesRegex(ValueError, "spec_digest"):
            validate_experiment_approval_provenance(tampered)

    def test_binds_safe_experiment_spec_to_approved_netlist(self) -> None:
        preview = _compiled()
        netlist_approval = _netlist_approval(preview)
        spec = _spec(preview)
        artifact = approve_simulation_plan(
            preview,
            netlist_approval,
            spec,
            {
                "approved": True,
                "netlist_approval_id": netlist_approval["approval_id"],
                "netlist_approval_digest": netlist_approval["approval_digest"],
                "confirm_netlist": True,
                "confirm_commands": True,
                "confirm_measurements": True,
                "confirm_limits": True,
                "review_note": "已确认网表、op、测量信号和验收边界",
            },
        )
        self.assertTrue(artifact["ready_for_simulation"])
        self.assertFalse(artifact["execution_boundary"]["simulation_started"])
        self.assertEqual(artifact["experiment_spec"]["commands"], "op")
        self.assertEqual(
            validate_simulation_plan_approval(
                json.loads(json.dumps(preview)),
                json.loads(json.dumps(netlist_approval)),
                json.loads(json.dumps(spec)),
                json.loads(json.dumps(artifact)),
            ),
            artifact,
        )

    def test_requires_all_explicit_review_gates(self) -> None:
        preview = _compiled()
        netlist_approval = _netlist_approval(preview)
        with self.assertRaisesRegex(ValueError, "confirm_limits"):
            approve_simulation_plan(
                preview,
                netlist_approval,
                _spec(preview),
                {
                    "approved": True,
                    "netlist_approval_id": netlist_approval["approval_id"],
                    "netlist_approval_digest": netlist_approval["approval_digest"],
                    "confirm_netlist": True,
                    "confirm_commands": True,
                    "confirm_measurements": True,
                    "confirm_limits": False,
                },
            )

    def test_rejects_commands_or_netlist_changed_after_review(self) -> None:
        preview = _compiled()
        netlist_approval = _netlist_approval(preview)
        spec = _spec(preview)
        artifact = approve_simulation_plan(
            preview,
            netlist_approval,
            spec,
            {
                "approved": True,
                "netlist_approval_id": netlist_approval["approval_id"],
                "netlist_approval_digest": netlist_approval["approval_digest"],
                "confirm_netlist": True,
                "confirm_commands": True,
                "confirm_measurements": True,
                "confirm_limits": True,
            },
        )
        changed_spec = dict(spec)
        changed_spec["commands"] = "tran 1u 1m"
        with self.assertRaisesRegex(ValueError, "does not match"):
            validate_simulation_plan_approval(
                preview, netlist_approval, changed_spec, artifact
            )

        tampered = json.loads(json.dumps(artifact))
        tampered["experiment_spec"]["requirements"][0]["upper"] = 4
        with self.assertRaisesRegex(ValueError, "does not match"):
            validate_simulation_plan_approval(
                preview, netlist_approval, spec, tampered
            )

    def test_verified_experiment_rejects_partial_handoff_before_runner(self) -> None:
        preview = _compiled()
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            server, "_run_circuit_experiment_impl"
        ) as runner:
            with self.assertRaisesRegex(ValueError, "must be provided together"):
                server.run_verified_circuit_experiment(
                    _spec(preview),
                    str(Path(tmp) / "partial"),
                    executable_netlist=preview,
                )
        runner.assert_not_called()

    def test_verified_experiment_revalidates_approval_before_running(self) -> None:
        preview = _compiled()
        netlist_approval = _netlist_approval(preview)
        spec = _spec(preview)
        simulation_approval = approve_simulation_plan(
            preview,
            netlist_approval,
            spec,
            {
                "approved": True,
                "netlist_approval_id": netlist_approval["approval_id"],
                "netlist_approval_digest": netlist_approval["approval_digest"],
                "confirm_netlist": True,
                "confirm_commands": True,
                "confirm_measurements": True,
                "confirm_limits": True,
            },
        )
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            server,
            "_run_circuit_experiment_impl",
            return_value={"success": True, "verification": {"overall_status": "pass"}},
        ) as runner:
            result = server.run_verified_circuit_experiment(
                spec,
                str(Path(tmp) / "approved"),
                executable_netlist=preview,
                netlist_approval=netlist_approval,
                simulation_plan_approval=simulation_approval,
            )
        self.assertEqual(
            result["simulation_plan_approval"]["approval_id"],
            simulation_approval["approval_id"],
        )
        self.assertTrue(result["simulation_plan_approval"]["simulation_started"])
        self.assertEqual(runner.call_args.args[0], preview["spice_netlist"])
        self.assertEqual(
            runner.call_args.kwargs["approval_provenance"],
            build_experiment_approval_provenance(simulation_approval),
        )

    def test_durable_submission_persists_the_approval_handoff(self) -> None:
        preview = _compiled()
        netlist_approval = _netlist_approval(preview)
        spec = _spec(preview)
        simulation_approval = approve_simulation_plan(
            preview,
            netlist_approval,
            spec,
            {
                "approved": True,
                "netlist_approval_id": netlist_approval["approval_id"],
                "netlist_approval_digest": netlist_approval["approval_digest"],
                "confirm_netlist": True,
                "confirm_commands": True,
                "confirm_measurements": True,
                "confirm_limits": True,
            },
        )

        class FakeJobManager:
            submitted: dict[str, object] | None = None

            def submit(self, value: dict[str, object]) -> dict[str, object]:
                self.submitted = value
                return {
                    "success": True,
                    "job_id": "job-approved",
                    "state": "queued",
                    "status_uri": "multisim://jobs/job-approved",
                    "output_dir": str(value["output_dir"]),
                }

        manager = FakeJobManager()
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            server, "_job_manager", return_value=manager
        ):
            result = server.submit_circuit_experiment(
                netlist=preview["spice_netlist"],
                commands=" op ",
                output_dir=str(Path(tmp) / "queued"),
                title=str(spec["title"]),
                requirements=spec["requirements"],
                theoretical_values=spec["theoretical_values"],
                executable_netlist=preview,
                netlist_approval=netlist_approval,
                simulation_plan_approval=simulation_approval,
            )
        self.assertTrue(result["success"])
        assert manager.submitted is not None
        self.assertEqual(
            manager.submitted["simulation_plan_approval"], simulation_approval
        )
        self.assertEqual(manager.submitted["executable_netlist"], preview)


if __name__ == "__main__":
    unittest.main()
