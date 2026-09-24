"""COM-free CLI surface tests for design patch evaluation."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from multisim_mcp.cli import main

from .test_design_patch_evaluation import _design, _patch, _spec


class DesignPatchEvaluationCliTest(unittest.TestCase):
    def test_cli_reads_strict_documents_and_forwards_runtime_limits(self) -> None:
        service = Mock()
        service.run.return_value = {
            "schema_version": 1,
            "success": True,
            "status": "candidate-improved-and-passed",
            "output_dir": "C:/result",
            "candidate_design": "C:/result/candidate-design.json",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design_path = root / "design.json"
            patch_path = root / "patch.json"
            spec_path = root / "spec.json"
            design_path.write_text(json.dumps(_design().to_dict()), encoding="utf-8")
            patch_path.write_text(json.dumps(_patch()), encoding="utf-8")
            spec_path.write_text(json.dumps(_spec()), encoding="utf-8")
            output = io.StringIO()
            with (
                patch(
                    "multisim_mcp.cli._verified_patch_experiment_service",
                    return_value=object(),
                ),
                patch(
                    "multisim_mcp.cli.DesignPatchEvaluationService",
                    return_value=service,
                ),
                contextlib.redirect_stdout(output),
            ):
                code = main(
                    [
                        "evaluate-design-patch",
                        "--design",
                        str(design_path),
                        "--patch",
                        str(patch_path),
                        "--spec",
                        str(spec_path),
                        "--output",
                        str(root / "result"),
                        "--regenerate-source-netlist",
                        "--timeout",
                        "27",
                        "--max-points",
                        "321",
                        "--json",
                    ]
                )
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output.getvalue())["success"])
        args, kwargs = service.run.call_args
        self.assertEqual(args[0].design_id, "divider-patch-evaluation")
        self.assertEqual(args[1].patch_id, "set-r2-for-vout")
        self.assertEqual(args[2]["title"], "Verify divider repair")
        self.assertTrue(kwargs["regenerate_source_netlist"])
        self.assertEqual(kwargs["timeout_per_experiment"], 27.0)
        self.assertEqual(kwargs["max_points"], 321)

    def test_duplicate_spec_field_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design_path = root / "design.json"
            patch_path = root / "patch.json"
            spec_path = root / "spec.json"
            design_path.write_text(json.dumps(_design().to_dict()), encoding="utf-8")
            patch_path.write_text(json.dumps(_patch()), encoding="utf-8")
            spec_path.write_text(
                '{"schema_version":1,"schema_version":1}', encoding="utf-8"
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(
                    [
                        "evaluate-design-patch",
                        "--design",
                        str(design_path),
                        "--patch",
                        str(patch_path),
                        "--spec",
                        str(spec_path),
                        "--output",
                        str(root / "result"),
                        "--json",
                    ]
                )
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(output.getvalue())["success"])


if __name__ == "__main__":
    unittest.main()
