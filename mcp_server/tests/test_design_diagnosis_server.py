"""Thin MCP adapter tests for deterministic diagnose_design."""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from multisim_mcp import server
from multisim_mcp.eda_core import CircuitDesign


DESIGN = {
    "schema_version": 1,
    "design_id": "diagnosis-adapter",
    "title": "Diagnosis adapter",
    "revision": 0,
    "components": [
        {
            "refdes": "R1",
            "kind": "R",
            "nodes": ["out", "0"],
            "value": "1k",
            "model": None,
            "parameters": {},
        }
    ],
    "parameters": {},
    "annotations": {},
}


class DesignDiagnosisServerTest(unittest.TestCase):
    def test_adapter_is_read_only_without_experiment(self) -> None:
        result = server.diagnose_design(DESIGN)
        self.assertTrue(result["success"])
        self.assertTrue(result["read_only"])
        self.assertFalse(result["source_design_modified"])
        self.assertFalse(result["simulation_performed"])

    def test_adapter_binds_experiment_before_forwarding_evidence(self) -> None:
        evidence = {"schema_version": 1, "design_binding": "verified-netlist-match"}
        service = Mock()
        service.run.return_value = {"success": True}
        with patch.object(
            server, "load_experiment_diagnosis_evidence", return_value=evidence
        ) as loader, patch.object(server, "DesignDiagnosisService", return_value=service):
            result = server.diagnose_design(
                DESIGN,
                "C:/completed-experiment",
                {"code": "solver", "message": "singular matrix"},
            )
        self.assertTrue(result["success"])
        normalized_design = loader.call_args.args[0]
        self.assertIsInstance(normalized_design, CircuitDesign)
        self.assertEqual(loader.call_args.args[1], "C:/completed-experiment")
        self.assertIs(service.run.call_args.args[0], normalized_design)
        self.assertIs(service.run.call_args.kwargs["experiment_evidence"], evidence)


if __name__ == "__main__":
    unittest.main()
