"""COM-free coverage for bilingual formal report artifacts."""

import json
import tempfile
import unittest
from pathlib import Path

from multisim_mcp.formal_report import export_formal_reports


RAW = """Title: report\nPlotname: DC transfer characteristic\nFlags: real\nNo. Variables: 2\nNo. Points: 2\nVariables:\n0 vin voltage V(in)\n1 vout voltage V(out)\nValues:\n0 0\n 0\n1 5\n 2.5\n"""


class FormalReportTest(unittest.TestCase):
    def test_exports_bilingual_html_pdf_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures = {
                "report.md": "# Divider\n\nPASS",
                "schematic.png": "not-empty-test-placeholder",
                "plot.svg": "<svg xmlns='http://www.w3.org/2000/svg'/>",
                "result.raw": RAW,
                "circuit.cir": "V1 in 0 5\nR1 in out 1k\n.end\n",
                "run.txt": "dc V1 0 5 1",
            }
            for name, content in fixtures.items():
                (root / name).write_text(content, encoding="utf-8")
            result = export_formal_reports(root, "exp-0123456789abcdef01234567")
            zh_html = (root / "report.zh-CN.html").read_text(encoding="utf-8")
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            zh_pdf = (root / "report.zh-CN.pdf").read_bytes()
            en_pdf = (root / "report.en.pdf").read_bytes()

        self.assertIn("Multisim 电路实验报告", zh_html)
        self.assertTrue(zh_pdf.startswith(b"%PDF-1.4"))
        self.assertIn(b"/FontDescriptor", zh_pdf)
        self.assertTrue(en_pdf.endswith(b"%%EOF\n"))
        self.assertIn("report.zh-CN.pdf", {item["filename"] for item in manifest["artifacts"]})
        self.assertNotIn("manifest.json", {item["filename"] for item in manifest["artifacts"]})
        self.assertEqual(result["schema_version"], 1)
        self.assertIn("data:image/png;base64,", zh_html)

    def test_rejects_symlink_outputs_when_supported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp)
            for name, content in {
                "report.md": "# Test",
                "schematic.png": "png",
                "plot.svg": "<svg/>",
                "result.raw": RAW,
                "circuit.cir": ".end\n",
                "run.txt": "op",
            }.items():
                (root / name).write_text(content, encoding="utf-8")
            target = Path(outside) / "outside.html"
            try:
                (root / "report.en.html").symlink_to(target)
            except OSError:
                self.skipTest("symlink creation is unavailable")
            with self.assertRaises(ValueError):
                export_formal_reports(root, "exp-0123456789abcdef01234567")


if __name__ == "__main__":
    unittest.main()
