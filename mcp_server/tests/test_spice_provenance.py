from __future__ import annotations

import hashlib
import unittest

from multisim_mcp.component_adapters import expand_component_adapters
from multisim_mcp.model_provenance import (
    build_model_provenance,
    validate_model_provenance,
)
from multisim_mcp.spice_provenance import (
    audit_spice_compatibility,
    compare_spice_compatibility_audits,
)


class SpiceProvenanceTest(unittest.TestCase):
    def test_model_provenance_normalizes_design_and_component_shapes(self) -> None:
        digest = "a" * 64
        result = build_model_provenance(
            [
                {
                    "name": "model:B",
                    "source": "models/b.lib",
                    "sha256": digest,
                    "license": "MIT",
                },
                {
                    "name": "model:A",
                    "uri": "models/a.lib",
                    "sha256": digest,
                    "license": "BSD-3-Clause",
                },
            ]
        )
        self.assertEqual([item["name"] for item in result["models"]], ["model:A", "model:B"])
        self.assertTrue(result["provenance_complete"])
        self.assertEqual(validate_model_provenance(result), result)

    def test_model_provenance_keeps_missing_declarations_explicit(self) -> None:
        result = build_model_provenance(
            [{"name": "external-model", "source": "vendor-library"}]
        )
        self.assertFalse(result["provenance_complete"])
        self.assertEqual(result["models"][0]["sha256_status"], "missing")
        self.assertEqual(result["models"][0]["license_status"], "unknown")

    def test_unreviewed_license_is_unknown_for_embedded_and_external_references(self) -> None:
        for license_value in ("pending", "unreviewed", "NOASSERTION", "待核对", "unknown"):
            for name in ("model:DMOD", "external-model"):
                with self.subTest(license=license_value, name=name):
                    definition = ".model DMOD D"
                    result = audit_spice_compatibility(
                        f"D1 in 0 DMOD\n{definition}\n.end\n",
                        backend_id="ngspice", declared_dialect="SPICE3", solver_output="ngspice-45",
                        model_references=[{
                            "name": name, "source": "local", "license": license_value,
                            "sha256": hashlib.sha256(definition.encode()).hexdigest(),
                        }],
                    )
                    self.assertFalse(result["summary"]["provenance_complete"])
                    self.assertTrue(all(item["license_status"] == "unknown" for item in result["models"]))

    def test_verifies_inline_model_hash_license_dialect_and_solver(self) -> None:
        definition = ".model DMOD D(IS=1e-14)"
        netlist = f"V1 in 0 5\nD1 in 0 DMOD\n{definition}\n.end\n"
        result = audit_spice_compatibility(
            netlist,
            backend_id="ngspice",
            declared_dialect="ngspice-45",
            solver_output="** ngspice-45 : Circuit level simulation program",
            model_references=[
                {
                    "name": "model:DMOD",
                    "source": "https://example.invalid/dmod.lib",
                    "sha256": hashlib.sha256(definition.encode()).hexdigest(),
                    "license": "BSD-3-Clause",
                }
            ],
        )

        self.assertEqual(result["dialect"]["source"], "explicit-input")
        self.assertEqual(result["backend"]["solver_version"], "45")
        self.assertEqual(result["models"][0]["hash_status"], "verified")
        self.assertEqual(result["models"][0]["license_status"], "declared")
        self.assertTrue(result["summary"]["provenance_complete"])
        self.assertEqual(validate_model_provenance(result["model_provenance"]), result["model_provenance"])

    def test_hash_mismatch_and_unresolved_model_fail_closed_as_evidence(self) -> None:
        mismatched = audit_spice_compatibility(
            "D1 in 0 DMOD\n.model DMOD D\n.end\n",
            model_references=[
                {
                    "name": "model:DMOD",
                    "source": "vendor",
                    "sha256": "0" * 64,
                    "license": "MIT",
                }
            ],
        )
        self.assertEqual(mismatched["summary"]["risk_level"], "high")
        self.assertIn(
            "model-sha256-mismatch",
            {item["code"] for item in mismatched["diagnostics"]},
        )

        unresolved = audit_spice_compatibility("D1 in 0 1N4148\n.end\n")
        self.assertEqual(unresolved["models"][0]["definition_status"], "not-embedded")
        self.assertIn(
            "model-definition-not-embedded",
            {item["code"] for item in unresolved["diagnostics"]},
        )

    def test_cross_backend_scope_requires_identical_netlist_and_models(self) -> None:
        netlist = "V1 in 0 1\nR1 in 0 1k\n.end\n"
        reference = audit_spice_compatibility(
            netlist,
            backend_id="multisim",
            solver_output="NI Multisim version 14.3",
            declared_dialect="SPICE3",
        )
        candidate = audit_spice_compatibility(
            netlist,
            backend_id="ngspice",
            solver_output="ngspice-45",
            declared_dialect="SPICE3",
        )
        comparison = compare_spice_compatibility_audits(reference, candidate)
        self.assertEqual(comparison["status"], "verified")
        self.assertEqual(comparison["comparison_scope"], "same-input-cross-solver")

        changed = audit_spice_compatibility(
            netlist.replace("1k", "2k"), backend_id="ngspice"
        )
        comparison = compare_spice_compatibility_audits(reference, changed)
        self.assertEqual(comparison["status"], "incomparable")
        self.assertFalse(comparison["same_netlist"])

    def test_prepared_execution_netlist_is_part_of_cross_backend_identity(self) -> None:
        source = "V1 in 0 1\nR1 in 0 1k\n.end\n"
        reference = audit_spice_compatibility(
            source, backend_id="multisim", executed_netlist=source
        )
        candidate = audit_spice_compatibility(
            source,
            backend_id="ngspice",
            executed_netlist=source.replace("1k", "1000"),
        )
        comparison = compare_spice_compatibility_audits(reference, candidate)
        self.assertEqual(comparison["status"], "incomparable")
        self.assertFalse(comparison["same_netlist"])
        self.assertIn(
            "executed-netlist-sha256-differs",
            {item["code"] for item in comparison["diagnostics"]},
        )

    def test_prepared_execution_model_fingerprint_is_recorded(self) -> None:
        source = "V1 in 0 1\nD1 in 0 DMOD\n.model DMOD D(IS=1e-14)\n.end\n"
        executed = source.replace("IS=1e-14", "IS=2e-14")
        result = audit_spice_compatibility(
            source,
            backend_id="ngspice",
            executed_netlist=executed,
        )

        self.assertIsNotNone(result["executed_netlist"]["model_fingerprint_sha256"])
        self.assertNotEqual(
            result["model_fingerprint_sha256"],
            result["executed_netlist"]["model_fingerprint_sha256"],
        )
        reference = audit_spice_compatibility(source, executed_netlist=source)
        candidate = audit_spice_compatibility(source, executed_netlist=executed)
        comparison = compare_spice_compatibility_audits(reference, candidate)
        self.assertEqual(comparison["status"], "incomparable")
        self.assertIn(
            "model-fingerprint-differs",
            {item["code"] for item in comparison["diagnostics"]},
        )

    def test_component_adapter_expansion_is_visible_in_execution_evidence(self) -> None:
        source = (
            "VDD high 0 5\n"
            "XDFF d clk set reset q qb high 0 @DFF\n"
            ".end\n"
        )
        executed = expand_component_adapters(source)
        result = audit_spice_compatibility(
            source,
            backend_id="ngspice",
            executed_netlist=executed,
        )

        self.assertNotEqual(
            result["netlist"]["sha256"], result["executed_netlist"]["sha256"]
        )
        self.assertIn("xspice-code-model", result["executed_netlist"]["features"])
        self.assertNotEqual(
            result["dialect"]["features"], result["executed_netlist"]["features"]
        )

    def test_generated_solver_wrapper_is_auditable(self) -> None:
        source = "V1 in 0 1\nR1 in 0 1k\n.end\n"
        generated = (
            "* generated ngspice deck\n"
            + source
            + ".save all\n.control\nrun\n.endc\n.end\n"
        )
        result = audit_spice_compatibility(
            source,
            backend_id="ngspice",
            executed_netlist=generated,
        )
        self.assertEqual(result["executed_netlist"]["sha256"], hashlib.sha256(generated.encode()).hexdigest())


if __name__ == "__main__":
    unittest.main()
