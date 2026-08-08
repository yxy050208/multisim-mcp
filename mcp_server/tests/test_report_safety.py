"""Security tests for generated Markdown experiment reports."""

from pathlib import Path
import tempfile
import unittest

from multisim_mcp.server import _write_experiment_report


class ReportSafetyTest(unittest.TestCase):
    def test_escapes_structure_and_uses_a_longer_code_fence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "report.md"
            _write_experiment_report(
                output,
                "</h1><script>alert(1)</script>\n# forged",
                "V1 in 0 5\n* ``` escape\n.end",
                "dc VIN 0 5 1",
                {"ms14": "circuit`bad.ms14", "image": "schematic.png"},
                {
                    "success": True,
                    "columns": ["V(out)|forged"],
                    "rows": [[1.0]],
                    "measurements": [],
                    "last_error": "<img src=x onerror=alert(1)>",
                },
                None,
            )
            content = output.read_text(encoding="utf-8")

        self.assertNotIn("<script>", content)
        self.assertNotIn("<img src=x", content)
        self.assertNotIn("\n# forged", content)
        self.assertIn("````spice", content)
        self.assertIn("V(out)&#124;forged", content)


if __name__ == "__main__":
    unittest.main()
