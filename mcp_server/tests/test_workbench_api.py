from __future__ import annotations

import base64
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from multisim_mcp.workbench_api import create_workbench_server
from multisim_mcp.workbench_app import create_workbench_app_server
from multisim_mcp.model_provider import ModelMessage, ModelResponse
from multisim_mcp.workbench_artifacts import entry_handle
from multisim_mcp.provider_config import write_provider_config
from multisim_mcp.job_engine import ExperimentJobManager
from multisim_mcp.workspace_manifest import write_directory_manifest


class WorkbenchApiTest(unittest.TestCase):
    def _project(self, root: Path) -> None:
        design = root / "design.json"
        design.write_text("{}\n", encoding="utf-8")
        write_directory_manifest(
            root,
            directory_kind="project",
            entity_id="api-project",
            state="active",
            artifacts={"design.json": "design"},
        )

    def _experiment(self, root: Path) -> Path:
        experiment = root / "experiments" / "fixture-run"
        experiment.mkdir(parents=True)
        files: dict[str, bytes] = {
            "circuit.ms14": b"fixture-ms14",
            "circuit.ms14.xml": b"<circuit />\n",
            "schematic.png": base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
            ),
            "data.csv": b"time,V(out)\n0,0\n1,1\n",
            "result.raw": b"not-a-spice-raw-file\n",
            "run.log": b"fixture complete\n",
            "run.txt": b"tran 1u 1m\n",
            "circuit.cir": b"V1 in 0 1\nR1 in 0 1k\n.end\n",
            "plot.svg": b'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20"><path d="M0 18L40 2" stroke="#65d9c4"/></svg>',
            "report.md": b"# Fixture report\n\nMeasured output is 1 V.\n",
            "verification.json": json.dumps(
                {
                    "schema_version": 1,
                    "overall_status": "pass",
                    "counts": {"pass": 1, "fail": 0, "unverified": 0},
                    "requirements": [
                        {
                            "id": "output",
                            "metric": "final_value",
                            "signal": "V(out)",
                            "status": "pass",
                            "value": 1.0,
                            "unit": "V",
                            "operator": "approximately",
                            "target": 1.0,
                        }
                    ],
                }
            ).encode("utf-8"),
        }
        roles = {
            "circuit.ms14": "schematic",
            "circuit.ms14.xml": "schematic-source",
            "schematic.png": "schematic-image",
            "data.csv": "simulation-data",
            "result.raw": "simulation-data",
            "run.log": "log",
            "run.txt": "analysis-commands",
            "circuit.cir": "netlist",
            "plot.svg": "plot",
            "report.md": "report",
            "verification.json": "verification",
        }
        for name, content in files.items():
            (experiment / name).write_bytes(content)
        write_directory_manifest(
            experiment,
            directory_kind="experiment",
            entity_id="fixture-run",
            state="succeeded",
            artifacts=roles,
            metadata={
                "backend_id": "multisim",
                "verified": True,
                "approval_provenance": {
                    "schema_version": 1,
                    "kind": "multisim-mcp-approved-simulation-provenance",
                    "simulation_plan_approval_id": "simulation-approval-fixture",
                    "simulation_plan_approval_digest": "a" * 64,
                    "netlist_approval_id": "netlist-approval-fixture",
                    "netlist_approval_digest": "b" * 64,
                    "compiled_id": "compiled-fixture",
                    "compiled_digest": "c" * 64,
                    "design_id": "design-fixture",
                    "design_digest": "d" * 64,
                    "spice_sha256": "e" * 64,
                    "spec_digest": "f" * 64,
                },
            },
        )
        return experiment

    def _patch_evaluation(
        self,
        root: Path,
        *,
        transaction: dict[str, object] | None = None,
    ) -> Path:
        evaluation = root / "correction" / "patch-evaluation-fixture"
        evaluation.mkdir(parents=True)
        state = {
            "schema_version": 1,
            "kind": "multisim-mcp-design-patch-evaluation",
            "evaluation_id": "patch-evaluation-" + "3" * 32,
            "state": "succeeded",
            "status": "candidate-passed",
            "success": True,
            "source_revision": 2,
            "candidate_revision": 3,
            "patch_id": "patch-" + "4" * 16,
            "source_design_modified": False,
            "candidate_persisted_as_source": False,
            "approval_required_before_apply": True,
            "adoption_eligible": True,
            "diagnosis_delta": {
                "before_status": "fail",
                "after_status": "pass",
                "resolved_finding_count": 1,
                "introduced_finding_count": 0,
                "severity_delta": {"error": -1, "warning": 0, "info": 0},
            },
            "comparison": {"status": "candidate-passed"},
        }
        if transaction is not None:
            state["transaction"] = transaction
        patch = {
            "schema_version": 1,
            "patch_id": state["patch_id"],
            "design_id": "fixture-design",
            "base_revision": 2,
            "description": "Increase divider resistance",
            "operations": [{
                "operation": "set_component_value",
                "target": "R1.value",
                "before": "1k",
                "after": "2k",
                "reason": "Meet the output requirement",
            }],
            "metadata": {},
        }
        inverse = {
            **patch,
            "patch_id": "patch-revert-" + "5" * 16,
            "base_revision": 3,
            "description": "Revert: Increase divider resistance",
            "operations": [{
                "operation": "set_component_value",
                "target": "R1.value",
                "before": "2k",
                "after": "1k",
                "reason": "Revert patch",
            }],
        }
        (evaluation / "evaluation.json").write_text(json.dumps(state), encoding="utf-8")
        (evaluation / "patch.json").write_text(json.dumps(patch), encoding="utf-8")
        (evaluation / "inverse-patch.json").write_text(json.dumps(inverse), encoding="utf-8")
        write_directory_manifest(
            evaluation,
            directory_kind="patch-evaluation",
            entity_id=state["evaluation_id"],
            state="succeeded",
            artifacts={
                "evaluation.json": "patch-evaluation-state",
                "patch.json": "design-patch",
                "inverse-patch.json": "inverse-patch",
            },
        )
        return evaluation

    def _optimization(self, root: Path) -> Path:
        optimization = root / "optimization" / "fixture-search"
        optimization.mkdir(parents=True)
        optimization_id = "optimization-" + "1" * 32
        state = {
            "schema_version": 1,
            "kind": "multisim-mcp-design-optimization",
            "optimization_id": optimization_id,
            "state": "succeeded",
            "status": "optimized",
            "stop_reason": "candidate_space_exhausted",
            "started_at": "2026-08-25T01:00:00Z",
            "updated_at": "2026-08-25T01:02:00Z",
            "finished_at": "2026-08-25T01:02:00Z",
            "candidate_space_size": 2,
            "max_experiments": 3,
            "experiments_attempted": 3,
            "experiment_attempt_count": 3,
            "resume_count": 0,
            "feasible_solution_count": 2,
            "best_evaluation_id": "candidate-001",
            "ranked_feasible_evaluation_ids": ["candidate-001", "baseline"],
            "evaluations": [
                {
                    "evaluation_id": "baseline",
                    "index": 0,
                    "kind": "baseline",
                    "values": {"R1": "1k"},
                    "status": "feasible",
                    "attempt": 1,
                    "objective": {"status": "measured", "value": 1.4, "score": 0.4},
                    "experiment": {"overall_status": "pass"},
                    "procurement": {"status": "not_configured"},
                },
                {
                    "evaluation_id": "candidate-001",
                    "index": 1,
                    "kind": "candidate",
                    "values": {"R1": "2k"},
                    "status": "feasible",
                    "attempt": 1,
                    "objective": {"status": "measured", "value": 1.0, "score": 0.0},
                    "experiment": {"overall_status": "pass"},
                    "procurement": {
                        "status": "pass",
                        "total_unit_cost": 0.12,
                        "currency": "CNY",
                    },
                },
                {
                    "evaluation_id": "candidate-002",
                    "index": 2,
                    "kind": "candidate",
                    "values": {"R1": "4k"},
                    "status": "constraint_fail",
                    "attempt": 1,
                    "objective": {"status": "measured", "value": 2.1, "score": 1.1},
                    "experiment": {"overall_status": "fail"},
                    "procurement": {"status": "not_configured"},
                },
            ],
        }
        spec = {
            "schema_version": 1,
            "objective": {"requirement_id": "gain", "goal": "target", "target": 1.0},
        }
        (optimization / "optimization.json").write_text(
            json.dumps(state), encoding="utf-8"
        )
        (optimization / "optimization-spec.json").write_text(
            json.dumps(spec), encoding="utf-8"
        )
        (optimization / "candidates.csv").write_text(
            "evaluation_id,status,objective_value\ncandidate-001,feasible,1.0\n",
            encoding="utf-8",
        )
        write_directory_manifest(
            optimization,
            directory_kind="optimization",
            entity_id=optimization_id,
            state="succeeded",
            artifacts={
                "optimization.json": "optimization-state",
                "optimization-spec.json": "optimization-spec",
                "candidates.csv": "optimization-data",
            },
        )
        return optimization

    def _global_optimization(self, root: Path) -> Path:
        optimization = root / "optimization" / "fixture-global"
        optimization.mkdir(parents=True)
        optimization_id = "global-optimization-" + "2" * 32
        objectives = [
            {"requirement_id": "error", "goal": "minimize", "epsilon": 0.0, "weight": 1.0},
            {"requirement_id": "bandwidth", "goal": "maximize", "epsilon": 0.0, "weight": 1.0},
        ]
        evaluations = [
            {
                "evaluation_id": "baseline",
                "index": 0,
                "kind": "baseline",
                "assignments": {},
                "status": "feasible",
                "attempt": 1,
                "pareto_rank": 1,
                "experiment": {"overall_status": "pass"},
                "objectives": [
                    {**objectives[0], "status": "measured", "value": 0.8, "score": 0.8},
                    {**objectives[1], "status": "measured", "value": 1200.0, "score": -1200.0},
                ],
            },
            {
                "evaluation_id": "candidate-0001",
                "index": 1,
                "kind": "candidate",
                "assignments": {"R1": "2k", "topology": "active-filter"},
                "status": "feasible",
                "attempt": 1,
                "pareto_rank": 0,
                "experiment": {"overall_status": "pass"},
                "objectives": [
                    {**objectives[0], "status": "measured", "value": 0.2, "score": 0.2},
                    {**objectives[1], "status": "measured", "value": 1800.0, "score": -1800.0},
                ],
            },
            {
                "evaluation_id": "candidate-0002",
                "index": 2,
                "kind": "candidate",
                "assignments": {"R1": "4k", "topology": "passive-filter"},
                "status": "feasible",
                "attempt": 1,
                "pareto_rank": 0,
                "experiment": {"overall_status": "pass"},
                "objectives": [
                    {**objectives[0], "status": "measured", "value": 0.1, "score": 0.1},
                    {**objectives[1], "status": "measured", "value": 1500.0, "score": -1500.0},
                ],
            },
        ]
        state = {
            "schema_version": 1,
            "kind": "multisim-mcp-global-optimization",
            "global_optimization_id": optimization_id,
            "state": "succeeded",
            "status": "completed",
            "stop_reason": "candidate_space_exhausted",
            "started_at": "2026-08-25T02:00:00Z",
            "updated_at": "2026-08-25T02:04:00Z",
            "finished_at": "2026-08-25T02:04:00Z",
            "search_strategy": "exhaustive",
            "candidate_space_size": 2,
            "max_experiments": 3,
            "experiments_attempted": 3,
            "experiment_attempt_count": 3,
            "resume_count": 0,
            "feasible_solution_count": 3,
            "pareto_evaluation_ids": ["candidate-0001", "candidate-0002"],
            "recommended_evaluation_id": "candidate-0001",
            "evaluations": evaluations,
        }
        pareto = {
            "schema_version": 1,
            "global_optimization_id": optimization_id,
            "objectives": objectives,
            "pareto_evaluation_ids": ["candidate-0001", "candidate-0002"],
            "recommended_evaluation_id": "candidate-0001",
            "selection_policy": "weighted_compromise",
        }
        (optimization / "global-optimization.json").write_text(
            json.dumps(state), encoding="utf-8"
        )
        (optimization / "pareto-front.json").write_text(
            json.dumps(pareto), encoding="utf-8"
        )
        (optimization / "global-candidates.csv").write_text(
            "evaluation_id,status,pareto_rank\ncandidate-0001,feasible,0\n",
            encoding="utf-8",
        )
        write_directory_manifest(
            optimization,
            directory_kind="global-optimization",
            entity_id=optimization_id,
            state="succeeded",
            artifacts={
                "global-optimization.json": "global-optimization-state",
                "pareto-front.json": "pareto-front",
                "global-candidates.csv": "optimization-data",
            },
        )
        return optimization

    def test_loopback_api_serves_health_and_snapshot_with_allowed_cors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            self._experiment(root)
            server = create_workbench_server(str(root), host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                health_request = Request(
                    f"{base}/api/health",
                    headers={"Origin": "http://127.0.0.1:4173"},
                )
                with urlopen(health_request, timeout=3) as response:
                    health = json.loads(response.read())
                    self.assertEqual(response.headers["Access-Control-Allow-Origin"], "http://127.0.0.1:4173")
                custom_port_request = Request(
                    f"{base}/api/health",
                    headers={"Origin": "http://localhost:5173"},
                )
                with urlopen(custom_port_request, timeout=3) as response:
                    self.assertEqual(response.headers["Access-Control-Allow-Origin"], "http://localhost:5173")
                with urlopen(f"{base}/api/project-snapshot", timeout=3) as response:
                    snapshot = json.loads(response.read())
                with urlopen(
                    f"{base}/api/project-snapshot?root=C:/Windows", timeout=3
                ) as response:
                    fixed_root_snapshot = json.loads(response.read())
                self.assertEqual(health["status"], "ok")
                self.assertTrue(health["read_only"])
                self.assertEqual(health["assistant"], "read-only-chat")
                self.assertEqual(snapshot["source"], "local-workbench-api")
                self.assertEqual(fixed_root_snapshot["workspace_root"], str(root.resolve()))
                self.assertEqual(snapshot["root_manifest"]["entity_id"], "api-project")
                experiment = next(
                    item for item in snapshot["entries"] if item["directory_kind"] == "experiment"
                )
                self.assertRegex(experiment["entry_handle"], r"^entry-[0-9a-f]{24}$")
                self.assertTrue(experiment["details_available"])
                with urlopen(
                    f"{base}/api/entries/{experiment['entry_handle']}", timeout=3
                ) as response:
                    details = json.loads(response.read())
                self.assertTrue(details["read_only"])
                self.assertIn("Fixture report", details["experiment"]["report_excerpt"])
                self.assertEqual(
                    details["experiment"]["verification"]["result"]["overall_status"],
                    "pass",
                )
                self.assertEqual(
                    details["entry"]["approval_provenance_status"], "verified"
                )
                self.assertEqual(
                    details["entry"]["approval_provenance"]["simulation_plan_approval_id"],
                    "simulation-approval-fixture",
                )
                with urlopen(
                    f"{base}/api/entries/{experiment['entry_handle']}/media/plot",
                    timeout=3,
                ) as response:
                    self.assertEqual(response.headers.get_content_type(), "image/svg+xml")
                    self.assertIn(b"<svg", response.read())
                    self.assertRegex(response.headers["X-Artifact-SHA256"], r"^[0-9a-f]{64}$")
                with urlopen(
                    f"{base}/api/entries/{experiment['entry_handle']}/media/schematic",
                    timeout=3,
                ) as response:
                    self.assertEqual(response.headers.get_content_type(), "image/png")
                    self.assertTrue(response.read().startswith(b"\x89PNG"))
            finally:
                server.shutdown()
                thread.join(timeout=3)
                server.server_close()

    def test_combined_workbench_app_serves_ui_and_api_on_one_loopback_port(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            ui = root / "ui"
            (ui / "assets").mkdir(parents=True)
            (ui / "index.html").write_text(
                "<!doctype html><html><body><div id='root'></div></body></html>",
                encoding="utf-8",
            )
            (ui / "assets" / "app.js").write_text("console.log('ok');", encoding="utf-8")
            server = create_workbench_app_server(str(root), str(ui), port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with urlopen(f"{base}/", timeout=3) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(b"<div id='root'>", response.read())
                with urlopen(f"{base}/assets/app.js", timeout=3) as response:
                    self.assertIn(
                        response.headers.get_content_type(),
                        {"text/javascript", "application/javascript"},
                    )
                with urlopen(f"{base}/design", timeout=3) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(b"<div id='root'>", response.read())
                with urlopen(f"{base}/api/health", timeout=3) as response:
                    self.assertEqual(json.loads(response.read())["status"], "ok")
            finally:
                server.shutdown()
                thread.join(timeout=3)
                server.server_close()

    def test_assistant_chat_is_read_only_and_does_not_expose_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            server = create_workbench_server(str(root), port=0)
            fake_response = ModelResponse(
                provider_id="local-test",
                requested_model="test-model",
                model="test-model",
                message=ModelMessage("assistant", "建议先确认带宽和负载范围，再比较方案。"),
                finish_reason="stop",
            )

            class FakeRegistry:
                def complete(self, messages, **kwargs):
                    self.messages = messages
                    self.kwargs = kwargs
                    return fake_response

            fake_registry = FakeRegistry()
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                request = Request(
                    f"{base}/api/assistant-chat",
                    data=json.dumps(
                        {
                            "message": "哪条方案更适合低延迟？",
                            "history": [{"role": "assistant", "content": "请补充约束。"}],
                            "context": {"selected_option_id": "balanced"},
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch("multisim_mcp.workbench_api.read_provider_config", return_value={"schema_version": 1, "active_provider": "local-test", "providers": {}}), patch(
                    "multisim_mcp.workbench_api.ModelProviderRegistry.from_config",
                    return_value=fake_registry,
                ):
                    with urlopen(request, timeout=3) as response:
                        payload = json.loads(response.read())
                self.assertTrue(payload["success"])
                self.assertTrue(payload["read_only"])
                self.assertEqual(payload["assistant"]["provider_id"], "local-test")
                self.assertFalse(payload["execution_boundary"]["files_written"])
                self.assertFalse(payload["execution_boundary"]["simulation_started"])
                self.assertNotIn("api_key", json.dumps(payload).lower())
                self.assertEqual(fake_registry.kwargs["max_tokens"], 1200)
            finally:
                server.shutdown()
                thread.join(timeout=3)
                server.server_close()

    def test_planning_endpoint_returns_read_only_options_without_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            server = create_workbench_server(str(root), host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                request = Request(
                    f"{base}/api/design-plan",
                    data=json.dumps(
                        {
                            "requirements": "机器人底盘电机闭环控制，要求低延迟和抗负载变化",
                            "objectives": {"robustness": 0.6, "performance": 0.4},
                            "max_options": 3,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=3) as response:
                    payload = json.loads(response.read())
                self.assertTrue(payload["success"])
                self.assertTrue(payload["read_only"])
                self.assertEqual(payload["next_step"], "select_option_before_schematic")
                self.assertEqual(payload["artifacts_generated"], [])
                self.assertFalse(payload["execution_boundary"]["schematic_generated"])
                self.assertGreaterEqual(len(payload["options"]), 2)
                self.assertIn(
                    payload["recommended_option_id"],
                    {item["option_id"] for item in payload["options"]},
                )
            finally:
                server.shutdown()
                thread.join(timeout=3)
                server.server_close()

    def test_planning_selection_endpoint_binds_the_selected_option(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            server = create_workbench_server(str(root), host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                plan_request = Request(
                    f"{base}/api/design-plan",
                    data=json.dumps({"requirements": "传感器无源滤波"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(plan_request, timeout=3) as response:
                    plan = json.loads(response.read())
                option_id = plan["recommended_option_id"]
                select_request = Request(
                    f"{base}/api/design-plan/select",
                    data=json.dumps({"plan": plan, "option_id": option_id}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(select_request, timeout=3) as response:
                    selected = json.loads(response.read())
                self.assertTrue(selected["success"])
                self.assertTrue(selected["read_only"])
                self.assertEqual(selected["state"], "selected")
                self.assertEqual(selected["selected_option_id"], option_id)
                self.assertEqual(selected["source_plan_digest"], plan["plan_digest"])
                self.assertEqual(selected["next_step"], "prepare_netlist_after_confirmation")
                self.assertEqual(selected["artifacts_generated"], [])
                specification_request = Request(
                    f"{base}/api/design-specification",
                    data=json.dumps(
                        {"plan": selected, "parameter_values": {}}
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(specification_request, timeout=3) as response:
                    specification = json.loads(response.read())
                self.assertTrue(specification["success"])
                self.assertTrue(specification["read_only"])
                self.assertEqual(
                    specification["kind"],
                    "multisim-mcp-design-specification",
                )
                self.assertEqual(specification["plan_id"], plan["plan_id"])
                self.assertEqual(specification["state"], "needs-input")
                self.assertFalse(specification["execution_boundary"]["netlist_generated"])
                completed_values = {
                    "supply_voltage_v": 5,
                    "input_min_v": 0,
                    "input_max_v": 5,
                    "output_min_v": 0.5,
                    "output_max_v": 2.5,
                    "source_impedance_ohm": 100,
                    "load_impedance_ohm": 100_000,
                    "cutoff_frequency_hz": 1_000,
                }
                completed_request = Request(
                    f"{base}/api/design-specification",
                    data=json.dumps(
                        {"plan": selected, "parameter_values": completed_values}
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(completed_request, timeout=3) as response:
                    completed = json.loads(response.read())
                self.assertTrue(completed["ready_for_netlist_draft"])
                draft_request = Request(
                    f"{base}/api/netlist-draft",
                    data=json.dumps(
                        {
                            "plan": selected,
                            "specification": completed,
                            "approval": {
                                "approved": True,
                                "specification_id": completed["specification_id"],
                                "specification_digest": completed["specification_digest"],
                            },
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(draft_request, timeout=3) as response:
                    draft = json.loads(response.read())
                self.assertTrue(draft["success"])
                self.assertTrue(draft["read_only"])
                self.assertEqual(draft["kind"], "multisim-mcp-logical-netlist-draft")
                self.assertFalse(draft["ready_for_schematic"])
                self.assertFalse(draft["execution_boundary"]["spice_netlist_generated"])
                resolution_request = Request(
                    f"{base}/api/component-resolution",
                    data=json.dumps({"draft": draft, "selections": {}}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(resolution_request, timeout=3) as response:
                    resolution = json.loads(response.read())
                self.assertTrue(resolution["success"])
                self.assertTrue(resolution["read_only"])
                self.assertEqual(resolution["kind"], "multisim-mcp-component-resolution")
                self.assertEqual(resolution["draft_id"], draft["draft_id"])
                self.assertFalse(resolution["ready_for_executable_netlist"])
                self.assertFalse(resolution["execution_boundary"]["simulation_started"])
                selections = {}
                compiler_families = {
                    "cr-01": "series-resistor",
                    "cr-02": "capacitor",
                    "cr-03": "resistor-divider",
                }
                for item in resolution["requirements"]:
                    candidate = next(
                        candidate
                        for candidate in item["candidates"]
                        if candidate["family"] == compiler_families[item["requirement_id"]]
                    )
                    selections[item["requirement_id"]] = {"family": candidate["family"]}
                    for rating in candidate.get("rating_requirements", []):
                        if rating.get("minimum") is not None:
                            selections[item["requirement_id"]][rating["metric"]] = rating["minimum"]
                selected_resolution_request = Request(
                    f"{base}/api/component-resolution",
                    data=json.dumps({"draft": draft, "selections": selections}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(selected_resolution_request, timeout=3) as response:
                    selected_resolution = json.loads(response.read())
                self.assertEqual(selected_resolution["summary"]["unresolved_selection_count"], 0)
                approval_request = Request(
                    f"{base}/api/component-resolution/approve",
                    data=json.dumps(
                        {
                            "draft": draft,
                            "resolution": selected_resolution,
                            "approval": {
                                "approved": True,
                                "resolution_id": selected_resolution["resolution_id"],
                                "resolution_digest": selected_resolution["resolution_digest"],
                                "confirm_topology": True,
                                "confirm_ratings": True,
                                "confirm_model_provenance": True,
                            },
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(approval_request, timeout=3) as response:
                    approved = json.loads(response.read())
                self.assertTrue(approved["success"])
                self.assertTrue(approved["read_only"])
                self.assertTrue(approved["approval_only"])
                self.assertTrue(approved["ready_for_executable_netlist"])
                self.assertFalse(approved["execution_boundary"]["spice_netlist_generated"])
                compile_request = Request(
                    f"{base}/api/executable-netlist/compile",
                    data=json.dumps(
                        {"draft": draft, "component_approval": approved}
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(compile_request, timeout=3) as response:
                    compiled = json.loads(response.read())
                self.assertTrue(compiled["success"])
                self.assertTrue(compiled["read_only"])
                self.assertTrue(compiled["preview_only"])
                self.assertEqual(
                    compiled["kind"], "multisim-mcp-executable-netlist-preview"
                )
                self.assertTrue(compiled["ready_for_netlist_approval"])
                self.assertFalse(compiled["ready_for_simulation"])
                self.assertFalse(compiled["execution_boundary"]["files_written"])
                netlist_approval_request = Request(
                    f"{base}/api/executable-netlist/approve",
                    data=json.dumps(
                        {
                            "executable_netlist": compiled,
                            "approval": {
                                "approved": True,
                                "compiled_id": compiled["compiled_id"],
                                "compiled_digest": compiled["compiled_digest"],
                                "confirm_components": True,
                                "confirm_topology": True,
                                "confirm_calculated_values": True,
                                "confirm_spice": True,
                                "review_note": "Workbench review",
                            },
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(netlist_approval_request, timeout=3) as response:
                    netlist_approval = json.loads(response.read())
                self.assertTrue(netlist_approval["success"])
                self.assertTrue(netlist_approval["read_only"])
                self.assertTrue(netlist_approval["approval_only"])
                self.assertTrue(netlist_approval["ready_for_schematic"])
                self.assertFalse(netlist_approval["ready_for_simulation"])
                self.assertEqual(
                    netlist_approval["next_step"],
                    "create_schematic_after_netlist_approval",
                )
                self.assertFalse(netlist_approval["execution_boundary"]["files_written"])
                simulation_spec = {
                    "schema_version": 1,
                    "title": "Workbench simulation plan",
                    "netlist": compiled["spice_netlist"],
                    "commands": "op",
                    "requirements": [
                        {
                            "id": "output-range",
                            "metric": "min",
                            "signal": "V(out)",
                            "operator": "between",
                            "lower": 0,
                            "upper": 5,
                        }
                    ],
                    "theoretical_values": {},
                }
                simulation_approval_request = Request(
                    f"{base}/api/executable-netlist/simulation-approve",
                    data=json.dumps(
                        {
                            "executable_netlist": compiled,
                            "netlist_approval": netlist_approval,
                            "experiment_spec": simulation_spec,
                            "approval": {
                                "approved": True,
                                "netlist_approval_id": netlist_approval["approval_id"],
                                "netlist_approval_digest": netlist_approval["approval_digest"],
                                "confirm_netlist": True,
                                "confirm_commands": True,
                                "confirm_measurements": True,
                                "confirm_limits": True,
                                "review_note": "Workbench simulation review",
                            },
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(simulation_approval_request, timeout=3) as response:
                    simulation_approval = json.loads(response.read())
                self.assertTrue(simulation_approval["success"])
                self.assertTrue(simulation_approval["read_only"])
                self.assertTrue(simulation_approval["approval_only"])
                self.assertTrue(simulation_approval["ready_for_simulation"])
                self.assertFalse(simulation_approval["execution_boundary"]["simulation_started"])
                self.assertEqual(simulation_approval["experiment_spec"]["commands"], "op")
            finally:
                server.shutdown()
                thread.join(timeout=3)
                server.server_close()

    def test_rejects_non_loopback_bind_and_unknown_route(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            with self.assertRaisesRegex(ValueError, "loopback-only"):
                create_workbench_server(str(root), host="0.0.0.0", port=0)
            server = create_workbench_server(str(root), port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with self.assertRaises(HTTPError) as context:
                    urlopen(f"http://127.0.0.1:{server.server_port}/api/unknown", timeout=3)
                self.assertEqual(context.exception.code, 404)
                with self.assertRaises(HTTPError) as context:
                    urlopen(
                        f"http://127.0.0.1:{server.server_port}/api/entries/{entry_handle('.')}",
                        timeout=3,
                    )
                self.assertEqual(context.exception.code, 422)
                with self.assertRaises(HTTPError) as context:
                    urlopen(
                        f"http://127.0.0.1:{server.server_port}/api/entries/entry-{'0' * 24}",
                        timeout=3,
                    )
                self.assertEqual(context.exception.code, 404)
            finally:
                server.shutdown()
                thread.join(timeout=3)
                server.server_close()

    def test_provider_config_is_secret_free_and_probe_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            config_path = root / "providers.json"
            env = {
                "MULTISIM_MODEL_PROVIDER_CONFIG": str(config_path),
                "DEEPSEEK_API_KEY": "",
                "OPENAI_API_KEY": "",
                "OPENAI_MODEL": "",
                "OLLAMA_MODEL": "",
                "OPENAI_COMPATIBLE_BASE_URL": "",
                "OPENAI_COMPATIBLE_MODEL": "",
                "OPENAI_COMPATIBLE_API_KEY": "",
            }
            server = create_workbench_server(str(root), port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with patch.dict("os.environ", env, clear=False):
                    with urlopen(f"{base}/api/provider-config", timeout=3) as response:
                        config_payload = json.loads(response.read())
                self.assertTrue(config_payload["success"])
                self.assertEqual(config_payload["source"], "environment-discovery")
                self.assertFalse(config_payload["persisted"])
                self.assertFalse(config_payload["credential_values_exposed"])
                self.assertEqual(config_payload["config"]["providers"], {})

                write_provider_config(
                    {
                        "schema_version": 1,
                        "active_provider": "local-ollama",
                        "providers": {
                            "local-ollama": {
                                "id": "local-ollama",
                                "provider": "ollama",
                                "base_url": "http://127.0.0.1:11434/v1",
                                "model": "qwen3:8b",
                                "models_path": "/models",
                                "credential": None,
                            }
                        },
                    },
                    config_path,
                )
                with patch.dict("os.environ", env, clear=False):
                    with urlopen(f"{base}/api/provider-config", timeout=3) as response:
                        stored_payload = json.loads(response.read())
                self.assertEqual(stored_payload["source"], "stored")
                self.assertTrue(stored_payload["persisted"])
                self.assertEqual(stored_payload["config"]["active_provider"], "local-ollama")
                self.assertFalse(stored_payload["credential_values_exposed"])

                provider = {
                    "id": "deepseek",
                    "provider": "deepseek",
                    "base_url": "https://api.deepseek.com",
                    "model": "deepseek-v4-flash",
                    "models_path": "/models",
                    "credential": {"source": "environment", "name": "DEEPSEEK_API_KEY"},
                }
                request = Request(
                    f"{base}/api/provider-probe",
                    data=json.dumps({"provider": provider}).encode("utf-8"),
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with patch.dict("os.environ", {"DEEPSEEK_API_KEY": ""}, clear=False):
                    with urlopen(request, timeout=3) as response:
                        probe_payload = json.loads(response.read())
                self.assertFalse(probe_payload["success"])
                self.assertEqual(probe_payload["probe"]["status"], "missing_credential")
                self.assertNotIn("plaintext", json.dumps(probe_payload).lower())

                invalid_request = Request(
                    f"{base}/api/provider-probe",
                    data=json.dumps({"provider": {**provider, "api_key": "plaintext"}}).encode("utf-8"),
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with self.assertRaises(HTTPError) as context:
                    urlopen(invalid_request, timeout=3)
                self.assertEqual(context.exception.code, 422)
            finally:
                server.shutdown()
                thread.join(timeout=3)
                server.server_close()

    def test_serves_ranked_and_pareto_optimization_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            self._optimization(root)
            self._global_optimization(root)
            server = create_workbench_server(str(root), port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with urlopen(f"{base}/api/project-snapshot", timeout=3) as response:
                    snapshot = json.loads(response.read())
                optimization = next(
                    item for item in snapshot["entries"]
                    if item.get("directory_kind") == "optimization"
                )
                global_optimization = next(
                    item for item in snapshot["entries"]
                    if item.get("directory_kind") == "global-optimization"
                )
                self.assertTrue(optimization["details_available"])
                self.assertEqual(optimization["detail_kind"], "optimization")
                with urlopen(
                    f"{base}/api/entries/{optimization['entry_handle']}", timeout=3
                ) as response:
                    ranked = json.loads(response.read())
                ranked_view = ranked["optimization"]
                self.assertEqual(ranked["detail_kind"], "optimization")
                self.assertEqual(ranked_view["run"]["status"], "optimized")
                self.assertEqual(ranked_view["candidates"][0]["evaluation_id"], "candidate-001")
                self.assertEqual(ranked_view["candidates"][0]["rank"], 1)
                self.assertTrue(ranked_view["candidates"][0]["recommended"])
                self.assertEqual(ranked_view["objective_definitions"][0]["requirement_id"], "gain")
                self.assertTrue(ranked_view["sensitivity"]["available"])
                self.assertEqual(ranked_view["sensitivity"]["parameters"][0]["parameter"], "R1")
                self.assertTrue(ranked_view["search_plan"]["available"])
                self.assertEqual(ranked_view["search_plan"]["priorities"][0]["parameter"], "R1")
                self.assertTrue(ranked_view["search_plan"]["read_only"])
                self.assertLessEqual(
                    sum(item["budget_share"] for item in ranked_view["search_plan"]["priorities"]),
                    ranked_view["search_plan"]["exploration_budget"],
                )
                self.assertTrue(ranked_view["search_plan"]["spec_draft"]["available"])
                self.assertFalse(ranked_view["search_plan"]["spec_draft"]["executable"])
                self.assertEqual(
                    ranked_view["search_plan"]["spec_draft"]["parameters"][0]["name"],
                    "R1",
                )
                self.assertEqual(
                    ranked_view["search_plan"]["spec_draft"]["preflight"]["status"],
                    "ready_for_review",
                )
                self.assertTrue(
                    ranked_view["search_plan"]["spec_draft"]["preflight"]["approval_required"]
                )
                self.assertFalse(
                    ranked_view["search_plan"]["spec_draft"]["preflight"]["execution_enabled"]
                )
                self.assertEqual(
                    ranked_view["search_plan"]["spec_draft"]["preflight"]["approval"]["status"],
                    "not_issued",
                )
                self.assertEqual(
                    ranked_view["search_plan"]["spec_draft"]["approval_binding"]["issuance"],
                    "cli-only",
                )
                self.assertNotIn(str(root), json.dumps(ranked))

                with urlopen(
                    f"{base}/api/entries/{global_optimization['entry_handle']}", timeout=3
                ) as response:
                    pareto = json.loads(response.read())
                pareto_view = pareto["optimization"]
                self.assertEqual(pareto_view["optimization_kind"], "global-optimization")
                self.assertEqual(pareto_view["pareto"]["solution_count"], 2)
                self.assertEqual(pareto_view["pareto"]["objective_count"], 2)
                self.assertTrue(pareto_view["pareto"]["available"])
                self.assertTrue(pareto_view["candidates"][0]["recommended"])
                self.assertTrue(pareto_view["candidates"][0]["pareto"])
                self.assertEqual(len(pareto_view["pareto"]["points"]), 3)
                self.assertTrue(pareto_view["sensitivity"]["available"])
                self.assertGreaterEqual(len(pareto_view["sensitivity"]["parameters"]), 2)
                self.assertTrue(pareto_view["search_plan"]["available"])
                self.assertEqual(pareto_view["search_plan"]["method"], "sensitivity-guided-neighborhood")
                self.assertLessEqual(
                    sum(item["budget_share"] for item in pareto_view["search_plan"]["priorities"]),
                    pareto_view["search_plan"]["exploration_budget"],
                )
                self.assertTrue(pareto_view["search_plan"]["spec_draft"]["available"])
                self.assertFalse(pareto_view["search_plan"]["spec_draft"]["executable"])
                self.assertEqual(
                    pareto_view["search_plan"]["spec_draft"]["source_optimization_kind"],
                    "global-optimization",
                )
                self.assertEqual(
                    pareto_view["search_plan"]["spec_draft"]["preflight"]["status"],
                    "ready_for_review",
                )
                self.assertNotIn(str(root), json.dumps(pareto))
            finally:
                server.shutdown()
                thread.join(timeout=3)
                server.server_close()

    def test_serves_read_only_patch_review_and_hides_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            evaluation = self._patch_evaluation(root)
            server = create_workbench_server(str(root), port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with urlopen(f"{base}/api/project-snapshot", timeout=3) as response:
                    snapshot = json.loads(response.read())
                entry = next(item for item in snapshot["entries"] if item["directory_kind"] == "patch-evaluation")
                self.assertTrue(entry["details_available"])
                self.assertEqual(entry["detail_kind"], "patch-review")
                with urlopen(f"{base}/api/entries/{entry['entry_handle']}", timeout=3) as response:
                    details = json.loads(response.read())
                review = details["patch_review"]
                self.assertTrue(details["read_only"])
                self.assertTrue(review["adoption_eligible"])
                self.assertEqual(review["operation_count"], 1)
                self.assertEqual(review["operations"][0]["target"], "R1.value")
                self.assertEqual(review["approval"]["issuance"], "cli-only")
                self.assertFalse(review["approval"]["token_exposed"])
                self.assertEqual(review["transaction"]["status"], "approval_pending")
                self.assertFalse(review["transaction"]["approval_consumed"])
                self.assertTrue(review["transaction"]["read_only"])
                self.assertEqual(review["inverse_patch_available"], True)
                self.assertNotIn(str(root), json.dumps(details))
                self.assertNotIn("evaluation.json", json.dumps(review["operations"]))
                self.assertNotIn("output_directory", json.dumps(review))
                with urlopen(f"{base}/api/entries/{entry['entry_handle']}/patch/candidate", timeout=3) as response:
                    self.assertEqual(response.headers.get_content_type(), "application/json")
                    self.assertIn("attachment", response.headers["Content-Disposition"])
                    self.assertIn(b"R1.value", response.read())
                with urlopen(f"{base}/api/entries/{entry['entry_handle']}/patch/inverse", timeout=3) as response:
                    self.assertIn(b"Revert patch", response.read())
            finally:
                server.shutdown()
                thread.join(timeout=3)
                server.server_close()

    def test_patch_review_transaction_summary_is_sanitized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            self._patch_evaluation(
                root,
                transaction={
                    "transaction_id": "patch-txn-" + "a" * 32,
                    "operation": "apply",
                    "approval_consumed": True,
                    "patch_journal_recovery_required": True,
                    "receipt_sha256": "b" * 64,
                    "output_design_digest": "c" * 64,
                    "receipt_path": "C:/should-not-leak/receipt.json",
                    "approval_token": "secret-material",
                },
            )
            server = create_workbench_server(str(root), port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with urlopen(f"{base}/api/project-snapshot", timeout=3) as response:
                    snapshot = json.loads(response.read())
                entry = next(item for item in snapshot["entries"] if item["directory_kind"] == "patch-evaluation")
                with urlopen(f"{base}/api/entries/{entry['entry_handle']}", timeout=3) as response:
                    review = json.loads(response.read())["patch_review"]
                transaction = review["transaction"]
                self.assertEqual(transaction["status"], "committed")
                self.assertEqual(transaction["transaction_id"], "patch-txn-" + "a" * 32)
                self.assertTrue(transaction["approval_consumed"])
                self.assertTrue(transaction["recovery_required"])
                self.assertNotIn("receipt_path", json.dumps(transaction))
                self.assertNotIn("approval_token", json.dumps(transaction))
                self.assertNotIn(str(root), json.dumps(transaction))
            finally:
                server.shutdown()
                thread.join(timeout=3)
                server.server_close()

    def test_cors_is_not_granted_to_another_origin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            server = create_workbench_server(str(root), port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/health",
                    headers={"Origin": "https://attacker.invalid"},
                )
                with urlopen(request, timeout=3) as response:
                    self.assertIsNone(response.headers.get("Access-Control-Allow-Origin"))
            finally:
                server.shutdown()
                thread.join(timeout=3)
                server.server_close()

    def test_lists_sanitized_durable_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            job_dir = root / "jobs"
            manager = ExperimentJobManager(state_dir=job_dir, start=False)
            submitted = manager.submit(
                {
                    "job_kind": "experiment",
                    "title": "fixture secret title",
                    "output_dir": str(root / "run-output"),
                    "netlist": "V1 in 0 1\nR1 in 0 1k\n.end\n",
                    "commands": "tran 1u 1m",
                }
            )
            job_id = submitted["job_id"]
            server = None
            thread = None
            with patch.dict("os.environ", {"MULTISIM_MCP_JOB_DIR": str(job_dir)}):
                server = create_workbench_server(str(root), port=0)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    base = f"http://127.0.0.1:{server.server_port}"
                    with urlopen(f"{base}/api/jobs?limit=10", timeout=3) as response:
                        listing = json.loads(response.read())
                    self.assertTrue(listing["success"])
                    self.assertTrue(listing["read_only"])
                    self.assertEqual(listing["count"], 1)
                    self.assertEqual(listing["jobs"][0]["state"], "queued")
                    listed_json = json.dumps(listing, ensure_ascii=False)
                    self.assertNotIn("netlist", listed_json)
                    self.assertNotIn("output_dir", listed_json)
                    self.assertNotIn("fixture secret title", listed_json)
                    with urlopen(f"{base}/api/jobs/{job_id}", timeout=3) as response:
                        detail = json.loads(response.read())
                    self.assertTrue(detail["success"])
                    self.assertEqual(detail["job"]["job_id"], job_id)
                    self.assertFalse(detail["job"]["has_result"])
                    self.assertNotIn("spec", json.dumps(detail, ensure_ascii=False))
                finally:
                    server.shutdown()
                    thread.join(timeout=3)
                    server.server_close()

    def test_empty_job_listing_does_not_create_state_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            job_dir = root / "jobs-not-created"
            with patch.dict("os.environ", {"MULTISIM_MCP_JOB_DIR": str(job_dir)}):
                server = create_workbench_server(str(root), port=0)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    base = f"http://127.0.0.1:{server.server_port}"
                    with urlopen(f"{base}/api/jobs", timeout=3) as response:
                        listing = json.loads(response.read())
                    self.assertEqual(listing["jobs"], [])
                    self.assertFalse(job_dir.exists())
                finally:
                    server.shutdown()
                    thread.join(timeout=3)
                    server.server_close()

    def test_completed_job_links_only_to_verified_in_project_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            experiment = self._experiment(root)
            job_dir = root / "jobs"
            manager = ExperimentJobManager(state_dir=job_dir, start=False)
            submitted = manager.submit(
                {
                    "job_kind": "experiment",
                    "title": "verified fixture",
                    "output_dir": str(experiment),
                    "netlist": "V1 in 0 1\nR1 in 0 1k\n.end\n",
                    "commands": "tran 1u 1m",
                }
            )
            job_id = submitted["job_id"]
            record = manager._records[job_id]
            record.update(
                state="succeeded",
                stage="completed",
                progress=100,
                result={"output_dir": str(experiment), "secret_payload": "not-for-browser"},
            )
            manager._persist(record)
            with patch.dict("os.environ", {"MULTISIM_MCP_JOB_DIR": str(job_dir)}):
                server = create_workbench_server(str(root), port=0)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    base = f"http://127.0.0.1:{server.server_port}"
                    with urlopen(f"{base}/api/jobs/{job_id}", timeout=3) as response:
                        detail = json.loads(response.read())
                    target = detail["job"]["result_entry"]
                    self.assertEqual(
                        target["entry_handle"],
                        entry_handle(experiment.relative_to(root).as_posix()),
                    )
                    self.assertEqual(target["entry_kind"], "experiment")
                    self.assertEqual(target["entity_id"], "fixture-run")
                    self.assertEqual(target["integrity_status"], "verified")
                    rendered = json.dumps(detail, ensure_ascii=False)
                    self.assertNotIn(str(root), rendered)
                    self.assertNotIn("secret_payload", rendered)
                finally:
                    server.shutdown()
                    thread.join(timeout=3)
                    server.server_close()

    def test_completed_job_does_not_link_result_outside_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            project = base / "project"
            project.mkdir()
            self._project(project)
            outside_root = base / "outside"
            outside_root.mkdir()
            outside_experiment = self._experiment(outside_root)
            job_dir = base / "jobs"
            manager = ExperimentJobManager(state_dir=job_dir, start=False)
            submitted = manager.submit(
                {
                    "job_kind": "experiment",
                    "output_dir": str(outside_experiment),
                    "netlist": "V1 in 0 1\nR1 in 0 1k\n.end\n",
                    "commands": "tran 1u 1m",
                }
            )
            job_id = submitted["job_id"]
            record = manager._records[job_id]
            record.update(
                state="succeeded",
                stage="completed",
                progress=100,
                result={"output_dir": str(outside_experiment)},
            )
            manager._persist(record)
            with patch.dict("os.environ", {"MULTISIM_MCP_JOB_DIR": str(job_dir)}):
                server = create_workbench_server(str(project), port=0)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    base_url = f"http://127.0.0.1:{server.server_port}"
                    with urlopen(f"{base_url}/api/jobs/{job_id}", timeout=3) as response:
                        detail = json.loads(response.read())
                    self.assertIsNone(detail["job"]["result_entry"])
                    self.assertNotIn(str(base), json.dumps(detail, ensure_ascii=False))
                finally:
                    server.shutdown()
                    thread.join(timeout=3)
                    server.server_close()


if __name__ == "__main__":
    unittest.main()
