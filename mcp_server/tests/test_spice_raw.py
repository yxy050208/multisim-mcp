"""COM-free tests for the SPICE3 raw parser and chart helpers."""

import os
import tempfile
import unittest

from multisim_mcp.spice_raw import parse_raw, plot_svg, summarize_columns, write_csv


SAMPLE_RAW = """\
Title: test circuit
Date: Tue Jan 1 00:00:00 2026
Plotname: DC transfer characteristic
Flags: real
No. Variables: 2
No. Points: 3
Variables:
0	vin	voltage	V(vin)
1	vout	voltage	V(vout)
Values:
0	0.000000e+00
	1.000000e+01
1	5.000000e+00
	5.000000e+00
2	1.000000e+01
	0.000000e+00
"""


class ParseRawTest(unittest.TestCase):
    def test_header_variables_and_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_path = os.path.join(tmp, "result.raw")
            with open(raw_path, "w", encoding="utf-8") as fh:
                fh.write(SAMPLE_RAW)
            parsed = parse_raw(raw_path)

        self.assertEqual(parsed["header"]["plotname"], "DC transfer characteristic")
        self.assertEqual(parsed["columns"], ["V(vin)", "V(vout)"])
        self.assertEqual(parsed["n_points"], 3)
        self.assertEqual(parsed["rows"][0], [0.0, 10.0])
        self.assertEqual(parsed["rows"][2], [10.0, 0.0])

    def test_rejects_error_text_and_count_mismatches(self) -> None:
        invalid_documents = (
            "Multisim error: no plot available\n",
            SAMPLE_RAW.replace("No. Points: 3", "No. Points: 4"),
            SAMPLE_RAW.replace("No. Variables: 2", "No. Variables: 3"),
        )
        for document in invalid_documents:
            with self.subTest(document=document[:30]):
                with tempfile.TemporaryDirectory() as tmp:
                    raw_path = os.path.join(tmp, "result.raw")
                    with open(raw_path, "w", encoding="utf-8") as fh:
                        fh.write(document)
                    with self.assertRaises(ValueError):
                        parse_raw(raw_path)


class WriteCsvTest(unittest.TestCase):
    def test_writes_header_and_rows(self) -> None:
        parsed = {
            "columns": ["vin", "vout"],
            "rows": [[0.0, 10.0], [5.0, 5.0]],
        }
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = write_csv(os.path.join(tmp, "data.csv"), parsed)
            with open(csv_path, encoding="utf-8") as fh:
                content = fh.read()

        self.assertIn("vin,vout", content)
        self.assertIn("5.0,5.0", content)


class SummarizeColumnsTest(unittest.TestCase):
    def test_computes_reproducible_basic_measurements(self) -> None:
        parsed = {
            "columns": ["vin", "vout"],
            "rows": [[0.0, 0.0], [5.0, 2.5], [10.0, 5.0]],
        }
        result = summarize_columns(parsed)
        self.assertEqual(result[1]["column"], "vout")
        self.assertEqual(result[1]["first"], 0.0)
        self.assertEqual(result[1]["last"], 5.0)
        self.assertEqual(result[1]["mean"], 2.5)


class PlotSvgTest(unittest.TestCase):
    def test_writes_svg(self) -> None:
        series = [
            {
                "name": "vout",
                "x": [0.0, 5.0, 10.0],
                "y": [10.0, 5.0, 0.0],
                "color": "#2563eb",
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            svg_path = plot_svg(
                os.path.join(tmp, "chart.svg"),
                series,
                title="DC sweep",
                x_label="VIN",
                y_label="VOUT",
            )
            with open(svg_path, encoding="utf-8") as fh:
                content = fh.read()

        self.assertIn("<svg", content)
        self.assertIn("DC sweep", content)
        self.assertIn("<polyline", content)

    def test_escapes_user_controlled_svg_text(self) -> None:
        series = [{"name": "<script>", "x": [0.0, 1.0], "y": [1.0, 2.0]}]
        with tempfile.TemporaryDirectory() as tmp:
            svg_path = plot_svg(
                os.path.join(tmp, "chart.svg"),
                series,
                title="A & B <unsafe>",
            )
            with open(svg_path, encoding="utf-8") as fh:
                content = fh.read()

        self.assertNotIn("<script>", content)
        self.assertIn("A &amp; B &lt;unsafe&gt;", content)


if __name__ == "__main__":
    unittest.main()
