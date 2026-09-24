"""COM-free tests for deterministic read-only design diagnosis."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from multisim_mcp.design_diagnosis import (
    DesignDiagnosisService,
    load_experiment_diagnosis_evidence,
)
from multisim_mcp.eda_core import CircuitComponent, CircuitDesign
from multisim_mcp.spice_adapter import circuit_design_to_spice
from multisim_mcp.workspace_manifest import write_directory_manifest


def _divider() -> CircuitDesign:
    return CircuitDesign(
        design_id="diagnosis-divider",
        title="Diagnosis divider",
        revision=2,
        components=(
            CircuitComponent("V1", "V", ("in", "0"), value="10"),
            CircuitComponent("R1", "R", ("in", "out"), value="1k"),
            CircuitComponent("R2", "R", ("out", "0"), value="1k"),
        ),
        source_netlist="V1 in 0 10\nR1 in out 1k\nR2 out 0 1k\n.end\n",
    )


def _evidence(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": 1,
        "experiment_id": "exp-test",
        "manifest_sha256": "a" * 64,
        "design_binding": "verified-netlist-match",
        "analysis": {"plotname": "Operating Point", "point_count": 1},
        "verification": None,
        "operating_point": {},
        "simulation_log": "",
    }
    value.update(overrides)
    return value


class DesignDiagnosisTest(unittest.TestCase):
    def test_structural_findings_are_read_only_and_actionable(self) -> None:
        design = CircuitDesign(
            design_id="floating-design",
            title="Floating design",
            components=(CircuitComponent("R1", "R", ("a", "b"), value="1k"),),
        )
        before = design.to_dict()
        result = DesignDiagnosisService().run(design)
        codes = {item["code"] for item in result["findings"]}
        self.assertEqual(result["overall_status"], "warning")
        self.assertIn("reference-net-absent", codes)
        self.assertIn("single-connection-net", codes)
        self.assertIn("excitation-source-absent", codes)
        self.assertTrue(all(not item["auto_fixable"] for item in result["findings"]))
        self.assertFalse(result["source_design_modified"])
        self.assertEqual(design.to_dict(), before)

    def test_requirement_failure_and_unverified_evidence_are_distinct(self) -> None:
        verification = {
            "schema_version": 1,
            "overall_status": "fail",
            "counts": {"pass": 0, "fail": 1, "unverified": 1},
            "requirements": [
                {
                    "id": "gain",
                    "metric": "gain",
                    "signal": "V(out)",
                    "status": "fail",
                    "measurement": {"status": "measured", "value": 0.2, "unit": ""},
                    "criterion": {"operator": "approximately", "target": 0.5},
                },
                {
                    "id": "thd",
                    "metric": "thd",
                    "signal": "V(out)",
                    "status": "unverified",
                    "reason": "insufficient periods",
                },
            ],
        }
        result = DesignDiagnosisService().run(
            _divider(), experiment_evidence=_evidence(verification=verification)
        )
        by_code = {item["code"]: item for item in result["findings"]}
        self.assertEqual(by_code["requirement-failed"]["severity"], "error")
        self.assertEqual(by_code["requirement-unverified"]["severity"], "warning")
        self.assertEqual(result["overall_status"], "error")

    def test_requirement_evidence_is_bounded_and_drops_nested_details(self) -> None:
        evidence = {
            "schema_version": 1,
            "verification": {
                "requirements": [
                    {
                        "id": "x" * 1000,
                        "status": "fail",
                        "reason": "r" * 2000,
                        "measurement": {
                            "status": "measured",
                            "value": 1.0,
                            "details": {"untrusted": "not forwarded"},
                        },
                        "criterion": {
                            "operator": "at_most",
                            "upper": 0.5,
                            "nested": {"untrusted": "not forwarded"},
                        },
                    }
                ]
            },
        }
        result = DesignDiagnosisService().run(_divider(), experiment_evidence=evidence)
        requirement = next(
            item for item in result["findings"] if item["code"] == "requirement-failed"
        )
        compact = requirement["evidence"]
        self.assertEqual(len(compact["requirement_id"]), 256)
        self.assertEqual(len(compact["reason"]), 1000)
        self.assertNotIn("details", compact["measurement"])
        self.assertNotIn("nested", compact["criterion"])

    def test_convergence_failure_is_classified_without_guessing_a_fix(self) -> None:
        result = DesignDiagnosisService().run(
            _divider(),
            simulation_failure={
                "code": "EXPERIMENT_FAILED",
                "stage": "simulation",
                "message": "solver stopped: singular matrix at node out",
            },
        )
        finding = next(
            item for item in result["findings"] if item["code"] == "singular-matrix"
        )
        self.assertEqual(finding["category"], "convergence")
        self.assertEqual(finding["severity"], "error")
        self.assertFalse(finding["auto_fixable"])
        self.assertNotIn(
            "simulation-failed", {item["code"] for item in result["findings"]}
        )

    def test_bjt_and_opamp_saturation_require_operating_point_evidence(self) -> None:
        design = CircuitDesign(
            design_id="active-regions",
            title="Active regions",
            components=(
                CircuitComponent("VCC", "V", ("vcc", "0"), value="5"),
                CircuitComponent("VEE", "V", ("vee", "0"), value="-5"),
                CircuitComponent("Q1", "QNPN", ("c", "b", "0"), model="NPN"),
                CircuitComponent(
                    "XU1",
                    "OPAMP5",
                    ("plus", "minus", "vcc", "vee", "opout"),
                    model="OPAMP5",
                ),
            ),
        )
        result = DesignDiagnosisService().run(
            design,
            experiment_evidence=_evidence(
                operating_point={
                    "V(c)": 0.1,
                    "V(b)": 0.7,
                    "V(vcc)": 5.0,
                    "V(vee)": -5.0,
                    "V(plus)": 1.0,
                    "V(minus)": 0.0,
                    "V(opout)": 4.95,
                }
            ),
        )
        codes = {item["code"] for item in result["findings"]}
        self.assertIn("bjt-saturation-likely", codes)
        self.assertIn("opamp-output-near-rail", codes)
        self.assertEqual(result["evidence"]["operating_point_device_count"], 2)

    def test_reversed_bjt_polarity_is_not_misclassified_as_saturation(self) -> None:
        design = CircuitDesign(
            design_id="reverse-bjt",
            title="Reverse BJT",
            components=(
                CircuitComponent("V1", "V", ("vcc", "0"), value="10"),
                CircuitComponent("Q1", "QNPN", ("collector", "base", "0"), model="NPN"),
            ),
        )
        evidence = {
            "schema_version": 1,
            "operating_point": {"V(collector)": -1.0, "V(base)": 0.7},
        }
        result = DesignDiagnosisService().run(design, experiment_evidence=evidence)
        codes = {item["code"] for item in result["findings"]}
        self.assertIn("bjt-reverse-bias-likely", codes)
        self.assertNotIn("bjt-saturation-likely", codes)

    def test_completed_experiment_loader_verifies_manifest_and_design_binding(self) -> None:
        design = _divider()
        verification = {
            "schema_version": 1,
            "overall_status": "pass",
            "counts": {"pass": 1, "fail": 0, "unverified": 0},
            "requirements": [{"id": "vout", "status": "pass"}],
        }
        raw = """Title: divider
Date: Tue Jan 1 00:00:00 2026
Plotname: Operating Point
Flags: real
No. Variables: 3
No. Points: 1
Variables:
0 v(in) voltage V(in)
1 v(out) voltage V(out)
2 i(v1) current I(V1)
Values:
0 10
5
-0.005
"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "experiment"
            root.mkdir()
            (root / "circuit.cir").write_text(
                circuit_design_to_spice(design), encoding="utf-8"
            )
            (root / "verification.json").write_text(
                json.dumps(verification), encoding="utf-8"
            )
            (root / "result.raw").write_text(raw, encoding="utf-8")
            (root / "run.log").write_text("simulation complete\n", encoding="utf-8")
            write_directory_manifest(
                root,
                directory_kind="experiment",
                entity_id="exp-diagnosis-fixture",
                state="succeeded",
                artifacts={
                    "circuit.cir": "netlist",
                    "verification.json": "verification",
                    "result.raw": "simulation-data",
                    "run.log": "log",
                },
            )
            evidence = load_experiment_diagnosis_evidence(design, str(root))
            self.assertEqual(evidence["design_binding"], "verified-netlist-match")
            self.assertEqual(evidence["operating_point"]["V(out)"], 5.0)

            other = CircuitDesign(
                design_id="different",
                title="Different",
                components=(CircuitComponent("R9", "R", ("x", "0"), value="2k"),),
            )
            with self.assertRaisesRegex(ValueError, "does not match"):
                load_experiment_diagnosis_evidence(other, str(root))


if __name__ == "__main__":
    unittest.main()
