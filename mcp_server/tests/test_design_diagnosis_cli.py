"""COM-free CLI tests for deterministic design diagnosis."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from multisim_mcp.cli import main


DESIGN = {
    "schema_version": 1,
    "design_id": "diagnosis-cli",
    "title": "Diagnosis CLI",
    "revision": 0,
    "components": [
        {
            "refdes": "R1",
            "kind": "R",
            "nodes": ["a", "b"],
            "value": "1k",
            "model": None,
            "parameters": {},
        }
    ],
    "parameters": {},
    "annotations": {},
}


class DesignDiagnosisCliTest(unittest.TestCase):
    def test_json_output_includes_deterministic_findings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            design_path = Path(tmp) / "design.json"
            failure_path = Path(tmp) / "failure.json"
            design_path.write_text(json.dumps(DESIGN), encoding="utf-8")
            failure_path.write_text(
                json.dumps({"stage": "op", "message": "singular matrix"}),
                encoding="utf-8",
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(
                    [
                        "diagnose-design",
                        "--design",
                        str(design_path),
                        "--failure",
                        str(failure_path),
                        "--json",
                    ]
                )
        self.assertEqual(code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["command"], "diagnose-design")
        self.assertTrue(result["read_only"])
        self.assertIn(
            "singular-matrix", {finding["code"] for finding in result["findings"]}
        )

    def test_invalid_failure_document_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            design_path = Path(tmp) / "design.json"
            failure_path = Path(tmp) / "failure.json"
            design_path.write_text(json.dumps(DESIGN), encoding="utf-8")
            failure_path.write_text('{"message":"a","message":"b"}', encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(
                    [
                        "diagnose-design",
                        "--design",
                        str(design_path),
                        "--failure",
                        str(failure_path),
                        "--json",
                    ]
                )
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(output.getvalue())["success"])


if __name__ == "__main__":
    unittest.main()
