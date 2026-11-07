"""Regression: a failing ReportNetlist must degrade, not abort the pipeline.

Multisim 14.0 answers E_ACCESSDENIED for ``ReportNetlist`` on every document,
including NI's own samples.  Before this guard the whole schematic/experiment
pipeline raised, so ``run_circuit_experiment`` and the natural-language
engineering entrypoints were unusable on that build.  The verification must now
keep its honest, partial verdict.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from multisim_mcp import server

NETLIST = "VIN vin 0 DC 10\nR1 vin vout 1k\nR2 vout 0 1k\n.end\n"

ACCESS_DENIED = "(-2147024891, '拒绝访问。', None, None)"


class _FakeClient:
    """Minimal COM stand-in for the reopened-design verification path."""

    FULL_TEXT = "VIN vin 0\nR1 vin vout\nR2 vout 0\n"
    LOST_R2_TEXT = "VIN vin 0\nR1 vin vout\n"

    def __init__(
        self,
        enumerated: list[str],
        report_error: Exception | None,
        report_text: str | None = None,
    ):
        self._enumerated = enumerated
        self._report_error = report_error
        self._report_text = self.FULL_TEXT if report_text is None else report_text
        self.report_calls = 0

    def open_circuit(self, path: str) -> dict:
        return {"name": Path(path).stem, "file": path, "state": 0, "last_error": ""}

    def enum_components(self, *args) -> list[str]:
        return list(self._enumerated)

    def enum_inputs(self, *args) -> list[str]:
        return ["VIN"]

    def enum_outputs(self, *args) -> list[str]:
        return []

    def report_netlist(self, path: str, probes_flag=False, fmt=0) -> str:
        self.report_calls += 1
        if self._report_error is not None:
            raise self._report_error
        Path(path).write_text(self._report_text, encoding="utf-8")
        return path


class ReportNetlistDegradedTest(unittest.TestCase):
    def _run(self, fake: _FakeClient) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            out = str(Path(tmp) / "divider.ms14")
            with mock.patch.object(server, "client", fake):
                return server._create_schematic_impl(
                    netlist=NETLIST,
                    output_ms14=out,
                    probe_nets=None,
                    include_experimental_probes=False,
                    open_after_build=True,
                    image_path=None,
                    overwrite=True,
                    verify=True,
                )

    def test_access_denied_degrades_instead_of_raising(self) -> None:
        fake = _FakeClient(["R1", "R2", "VIN"], RuntimeError(ACCESS_DENIED))
        result = self._run(fake)

        self.assertTrue(result["success"])
        self.assertEqual(fake.report_calls, 1)

        verification = result["verification"]
        self.assertFalse(verification["netlist_export"]["available"])
        self.assertIn("-2147024891", verification["netlist_export"]["error"])

        # Text-based checks must be reported as unverified, never as pass.
        self.assertEqual(result["topology_diff"]["status"], "unverified")
        self.assertEqual(result["topology_diff"]["pin_connections"]["status"], "unverified")

        # Presence falls back to enumeration: every component is still proven
        # to exist, but the netlist-level verdict itself stays unknown.
        self.assertTrue(all(verification["native_netlist_components"].values()))
        self.assertIsNone(verification["native_netlist_complete"])

    def test_working_reportnetlist_keeps_strict_verdict(self) -> None:
        fake = _FakeClient(["R1", "R2", "VIN"], None)
        result = self._run(fake)

        verification = result["verification"]
        self.assertTrue(verification["netlist_export"]["available"])
        self.assertEqual(result["topology_diff"]["status"], "pass")
        self.assertIs(verification["native_netlist_complete"], True)

    def test_missing_component_still_raises_when_netlist_is_available(self) -> None:
        # ReportNetlist works but the reopened design lost a part: strict.
        fake = _FakeClient(
            ["R1", "VIN"], None, report_text=_FakeClient.LOST_R2_TEXT
        )
        with self.assertRaises(RuntimeError):
            self._run(fake)


if __name__ == "__main__":
    unittest.main()
