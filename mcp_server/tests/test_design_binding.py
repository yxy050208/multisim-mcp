"""Tests for read-only binding of requirements to a design snapshot."""

from __future__ import annotations

import unittest
import tempfile
import json
from pathlib import Path
from unittest.mock import Mock, patch

from multisim_mcp import server
from multisim_mcp.design_binding import (
    assess_snapshot_boundaries,
    bind_requirement_review_to_design,
    build_existing_design_snapshot,
    circuit_design_from_multisim_report,
    enrich_snapshot_with_com_parameters,
    enrich_snapshot_with_native_metadata,
    load_existing_design_snapshot,
    validate_snapshot_for_binding,
)
from multisim_mcp.eda_core import CircuitDesign
from multisim_mcp.requirement_contract import review_design_requirements
from multisim_mcp.native_metadata import extract_native_component_metadata


def _design() -> CircuitDesign:
    return CircuitDesign.from_dict(
        {
            "schema_version": 1,
            "design_id": "binding-demo",
            "title": "Binding demo",
            "revision": 3,
            "components": [
                {
                    "refdes": "R1",
                    "kind": "R",
                    "nodes": ["in", "out"],
                    "value": "1k",
                    "model": None,
                    "parameters": {},
                },
                {
                    "refdes": "C1",
                    "kind": "C",
                    "nodes": ["out", "0"],
                    "value": "10n",
                    "model": None,
                    "parameters": {},
                },
            ],
            "nets": ["in", "out", "0"],
            "parameters": {},
            "annotations": {},
        }
    )


class DesignBindingTest(unittest.TestCase):
    def test_native_xml_identity_maps_internal_refdes_without_model_body(self) -> None:
        xml = """<?xml version="1.0"?>
<Root>
  <Item CiID="1"><CiComponent LocalName="&amp;ASCU4"><Attributes>
    <Item><CiaSpiceTmpltExprt String="&amp;ASCx%p pins 5T_Virtual" /></Item>
    <Item><CiaCollString><strings>
      <Item Value="&amp;ASCANALOG_VIRTUAL"/><Item Value="&amp;ASCOPAMP_5T_VIRTUAL"/>
      <Item Value=""/><Item Value="&amp;ASC5T_Virtual"/>
    </strings></CiaCollString></Item>
  </Attributes><Ports><Item CiID="2"/><Item CiID="3"/></Ports></CiComponent></Item>
  <Item CiID="2"><CiPort LocalName="&amp;ASCIN+" Component="1"/></Item>
  <Item CiID="3"><CiPort LocalName="&amp;ASCOUT" Component="1"/></Item>
  <CIRToInfoMapItem CIRKey="&amp;ASCU4"><RefDesInfo IRPrefix="&amp;ASCU" IRNumber="1"/></CIRToInfoMapItem>
</Root>"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.xml"
            path.write_text(xml, encoding="utf-8")
            evidence = extract_native_component_metadata(
                str(path), expected_refdes={"U1"}
            )
        item = evidence["components"][0]
        self.assertEqual(item["refdes"], "U1")
        self.assertEqual(item["model_name"], "5T_Virtual")
        self.assertEqual(item["port_names"], ["IN+", "OUT"])
        self.assertTrue(item["model_verified"])
        self.assertFalse(item["raw_model_material_included"])

    def test_native_metadata_resolves_model_and_pin_specific_boundaries(self) -> None:
        snapshot = build_existing_design_snapshot(
            """_ucTitle
----
0 Demo U1 IN+
out Demo U1 OUT
""",
            circuit_info={"name": "Native"},
            components=["U1"],
            inputs=[],
            outputs=[],
        )
        enriched = enrich_snapshot_with_native_metadata(
            snapshot,
            {
                "kind": "multisim-mcp-native-component-metadata",
                "state": "verified",
                "components": [
                    {
                        "refdes": "U1",
                        "model_name": "5T_Virtual",
                        "model_verified": True,
                        "port_names": ["IN+", "OUT"],
                        "raw_model_material_included": False,
                    }
                ],
            },
        )
        component = enriched["design"]["components"][0]
        self.assertEqual(component["model"], "5T_Virtual")
        self.assertTrue(component["annotations"]["native_metadata"]["pin_inventory_verified"])
        codes = {item["code"] for item in enriched["boundary_review"]["findings"]}
        self.assertNotIn("model_not_explicit", codes)
        self.assertNotIn("hidden_pin_mapping_unverified", codes)
        self.assertIn("connectivity_report_missing_parameters", codes)

    def test_native_optimization_readiness_allows_connectivity_only_boundary(self) -> None:
        snapshot = build_existing_design_snapshot(
            """_ucTitle
----
in Demo R1 1
out Demo R1 2
in Demo U1 IN+
out Demo U1 OUT
""",
            circuit_info={"name": "Native"},
            components=["R1", "U1"],
            inputs=[],
            outputs=[],
        )
        snapshot = enrich_snapshot_with_com_parameters(
            snapshot, [{"component": "R1", "value": 1000.0}]
        )
        snapshot = enrich_snapshot_with_native_metadata(
            snapshot,
            {
                "state": "verified",
                "components": [
                    {"refdes": "R1", "model_name": "VIRTUAL_RESISTANCE", "model_verified": True, "port_names": ["1", "2"]},
                    {"refdes": "U1", "model_name": "5T_Virtual", "model_verified": True, "port_names": ["IN+", "OUT"]},
                ],
            },
        )
        review = review_design_requirements(
            [{"id": "out", "metric": "mean", "signal": "V(out)", "operator": "at_least", "target": 1.0, "unit": "V"}]
        )
        bound = bind_requirement_review_to_design(
            CircuitDesign.from_dict(snapshot["design"]), review, snapshot_evidence=snapshot
        )
        self.assertEqual(
            bound["native_optimization_readiness"]["state"],
            "ready-for-com-parameter-sweep",
        )
        self.assertTrue(bound["native_optimization_readiness"]["requires_approval"])

    def test_parses_multisim_fixed_width_connectivity_report_as_bounded_topology(self) -> None:
        report = """_ucTitle (encoded, 12:00:00)
----
_ucH1 _ucH2 _ucH3 _ucH4
----
0 LowPassFilter R1 1
in LowPassFilter R1 2
in LowPassFilter _uc257 1
"""
        design = circuit_design_from_multisim_report(report, title="Opened")
        self.assertEqual(design.title, "Opened")
        self.assertEqual([item.refdes for item in design.components], ["R1", "X_uc257"])
        self.assertEqual(design.components[0].nodes, ("0", "in"))
        self.assertEqual(design.annotations["multisim_report"]["format"], "connectivity")
        self.assertFalse(assess_snapshot_boundaries(design)["optimization_safe"])

    def test_snapshot_reconciles_generated_single_net_report_anchors(self) -> None:
        snapshot = build_existing_design_snapshot(
            """_ucTitle
----
0 Demo R1 1
out Demo R1 2
0 Demo _uc257 1
""",
            circuit_info={"name": "Opened"},
            components=["R1"],
            inputs=[],
            outputs=[],
        )
        self.assertEqual(snapshot["cross_validation"]["state"], "verified")
        self.assertEqual(
            [item["refdes"] for item in snapshot["design"]["components"]], ["R1"]
        )
        self.assertEqual(
            snapshot["design"]["annotations"]["multisim_report"]["ignored_connectivity_artifact_count"],
            1,
        )

    def test_merges_verified_rlc_values_without_relaxing_boundaries(self) -> None:
        snapshot = build_existing_design_snapshot(
            "V1 in 0 5\nR1 in out 1k\nC1 out 0 10n\n.end\n",
            circuit_info={"name": "RLC"},
            components=["V1", "R1", "C1"],
            inputs=[],
            outputs=[],
        )
        enriched = enrich_snapshot_with_com_parameters(
            snapshot,
            [
                {"component": "R1", "value": 1000.0},
                {"component": "C1", "value": 1e-7},
            ],
        )
        values = {item["refdes"]: item["value"] for item in enriched["design"]["components"]}
        self.assertEqual(values["R1"], "1000")
        self.assertEqual(values["C1"], "1e-07")
        self.assertEqual(enriched["parameter_coverage"]["verified"], 2)
        self.assertTrue(enriched["boundary_review"]["optimization_safe"])

    def test_flags_model_and_hidden_pin_boundaries(self) -> None:
        design = CircuitDesign.from_dict(
            {
                "schema_version": 1,
                "design_id": "boundary-demo",
                "title": "Boundary demo",
                "components": [
                    {"refdes": "D1", "kind": "D", "nodes": ["a", "0"], "value": None},
                    {"refdes": "X1", "kind": "X", "nodes": ["a", "b", "c"], "value": None, "model": "AMP"},
                ],
                "nets": ["a", "b", "c", "0"],
            }
        )
        result = assess_snapshot_boundaries(design)
        self.assertEqual(result["state"], "manual-review-required")
        self.assertFalse(result["optimization_safe"])
        self.assertGreaterEqual(result["finding_count"], 2)

    def test_builds_snapshot_from_exported_netlist_and_com_evidence(self) -> None:
        snapshot = build_existing_design_snapshot(
            "V1 in 0 5\nR1 in out 1k\nC1 out 0 10n\n.end\n",
            circuit_info={"name": "Opened", "file": "C:/demo.ms14", "state": 0},
            components=["V1", "R1", "C1"],
            inputs=["V1"],
            outputs=["V(out)"],
        )
        self.assertEqual(snapshot["kind"], "multisim-mcp-existing-design-snapshot")
        self.assertEqual(snapshot["design"]["title"], "Opened")
        self.assertEqual(snapshot["com_enumeration"]["outputs"], ["V(out)"])
        self.assertEqual(snapshot["cross_validation"]["state"], "verified")
        self.assertEqual(snapshot["next_step"], "bind_requirement_review_to_design")
        self.assertFalse(snapshot["source_mutated"])

    def test_snapshot_marks_com_netlist_mismatch_before_binding(self) -> None:
        snapshot = build_existing_design_snapshot(
            "V1 in 0 5\nR1 in out 1k\n.end\n",
            circuit_info={"name": "Opened", "file": "C:/demo.ms14"},
            components=["V1", "R1", "C_EXTRA"],
            inputs=[],
            outputs=[],
        )
        self.assertEqual(snapshot["cross_validation"]["state"], "mismatch")
        self.assertEqual(snapshot["next_step"], "review_snapshot_mismatch")
        self.assertEqual(
            snapshot["cross_validation"]["components_extra_in_enumeration"], ["c_extra"]
        )

    def test_binds_voltage_and_current_and_lists_optimizable_values(self) -> None:
        review = review_design_requirements(
            [
                {
                    "id": "output",
                    "metric": "mean",
                    "signal": "V(out)",
                    "operator": "between",
                    "lower": 0.0,
                    "upper": 5.0,
                    "unit": "V",
                },
                {
                    "id": "current",
                    "metric": "mean",
                    "signal": "I(R1)",
                    "operator": "at_most",
                    "target": 0.1,
                    "unit": "A",
                },
            ]
        )
        result = bind_requirement_review_to_design(_design(), review)
        self.assertEqual(result["state"], "ready-for-baseline")
        self.assertEqual(result["coverage"], {"total": 2, "bound": 2, "missing": 0})
        self.assertEqual(
            {item["refdes"] for item in result["optimizable_parameters"]}, {"R1", "C1"}
        )
        self.assertFalse(result["source_mutated"])

    def test_guarded_binding_requires_verified_snapshot_evidence(self) -> None:
        snapshot = build_existing_design_snapshot(
            "V1 in 0 5\nR1 in out 1k\nC1 out 0 10n\n.end\n",
            circuit_info={"name": "Opened", "file": "C:/demo.ms14"},
            components=["V1", "R1", "C1"],
            inputs=[],
            outputs=[],
        )
        design = CircuitDesign.from_dict(snapshot["design"])
        review = review_design_requirements(
            [
                {
                    "id": "output",
                    "metric": "mean",
                    "signal": "V(out)",
                    "operator": "at_least",
                    "target": 1.0,
                    "unit": "V",
                }
            ]
        )
        result = bind_requirement_review_to_design(
            design, review, snapshot_evidence=snapshot
        )
        self.assertEqual(result["state"], "ready-for-baseline")
        tampered = dict(snapshot)
        tampered["boundary_review"] = {"optimization_safe": False}
        with self.assertRaisesRegex(ValueError, "unresolved model"):
            validate_snapshot_for_binding(design, tampered)

    def test_reports_missing_signal_and_supports_alias(self) -> None:
        review = review_design_requirements(
            [
                {
                    "id": "output",
                    "metric": "mean",
                    "signal": "OUT_PORT",
                    "operator": "at_least",
                    "target": 1.0,
                    "unit": "V",
                }
            ]
        )
        missing = bind_requirement_review_to_design(_design(), review)
        self.assertEqual(missing["state"], "needs-signal-binding")
        self.assertEqual(missing["next_step"], "define_signal_aliases")
        bound = bind_requirement_review_to_design(
            _design(), review, signal_aliases={"OUT_PORT": "V(out)"}
        )
        self.assertEqual(bound["state"], "ready-for-baseline")
        self.assertEqual(bound["signals"][0]["resolved_signal"], "V(out)")

    def test_server_snapshot_exports_without_touching_source(self) -> None:
        fake_client = Mock()
        fake_client.report_netlist.side_effect = lambda path, probes, fmt: (
            Path(path).write_text(
                "V1 in 0 5\nR1 in out 1k\nC1 out 0 10n\n.end\n",
                encoding="utf-8",
            )
            or path
        )
        fake_client.circuit_info.return_value = {
            "name": "Opened",
            "file": "C:/source.ms14",
            "state": 0,
            "last_error": "",
        }
        fake_client.enum_components.return_value = ["V1", "R1", "C1"]
        fake_client.enum_inputs.return_value = ["V1"]
        fake_client.enum_outputs.return_value = ["V(out)"]
        fake_client.get_rlc_value.side_effect = lambda refdes: {
            "component": refdes,
            "value": {"R1": 1000.0, "C1": 1e-7}.get(refdes, 0.0),
        }
        with tempfile.TemporaryDirectory() as tmp, patch.object(server, "client", fake_client):
            result = server.snapshot_open_circuit(tmp)
            self.assertEqual(result["design"]["title"], "Opened")
            self.assertEqual(
                [item["refdes"] for item in result["design"]["components"]],
                ["V1", "R1", "C1"],
            )
            self.assertTrue(Path(result["snapshot_path"]).is_file())
            self.assertEqual(result["parameter_coverage"]["verified"], 2)
            self.assertTrue(Path(result["netlist_path"]).is_file())
        self.assertFalse(result["source_mutated"])

    def test_server_snapshot_refuses_nonempty_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "keep.txt").write_text("do not overwrite", encoding="utf-8")
            with self.assertRaisesRegex(FileExistsError, "directory must be empty"):
                server.snapshot_open_circuit(tmp)

    def test_persisted_snapshot_can_be_reloaded_and_detects_tampering(self) -> None:
        snapshot = build_existing_design_snapshot(
            "V1 in 0 5\nR1 in out 1k\nC1 out 0 10n\n.end\n",
            circuit_info={"name": "Reloadable", "file": "C:/demo.ms14"},
            components=["V1", "R1", "C1"],
            inputs=[],
            outputs=[],
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "design-snapshot.json"
            path.write_text(json.dumps(snapshot), encoding="utf-8")
            loaded, design = load_existing_design_snapshot(str(path))
            self.assertEqual(loaded["snapshot_digest"], snapshot["snapshot_digest"])
            self.assertEqual(design.design_id, snapshot["design"]["design_id"])
            tampered = dict(snapshot)
            tampered["circuit_name"] = "Tampered"
            path.write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "integrity digest"):
                load_existing_design_snapshot(str(path))


if __name__ == "__main__":
    unittest.main()
