from __future__ import annotations

import unittest

from multisim_mcp.topology_validation import compare_pin_connections, compare_roundtrip_topology


class TopologyValidationTest(unittest.TestCase):
    def test_roundtrip_passes_for_expected_names(self) -> None:
        result = compare_roundtrip_topology(["R1"], ["vin", "out"], "R1 vin out 1k")
        self.assertEqual(result["status"], "pass")

    def test_roundtrip_reports_missing_names(self) -> None:
        result = compare_roundtrip_topology(["R1", "C1"], ["vin"], "R1 vin 0 1k")
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["missing_components"], ["C1"])

    def test_pin_connections_report_wrong_order_or_net(self) -> None:
        result = compare_pin_connections({"R1": ["vin", "out"]}, "R1 out 0 1k")
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["mismatches"][0]["refdes"], "R1")

    def test_multisim_connection_table_checks_numeric_pins(self) -> None:
        report = """title
-----
header
-----
vin circuit R1 1
out circuit R1 2
-----
"""
        result = compare_pin_connections({"R1": ["vin", "out"]}, report)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["checked_components"], 1)

    def test_multisim_named_xspice_pins_are_explicitly_unverified(self) -> None:
        report = """title
-----
header
-----
din circuit A1A I1
dout circuit A1A O1
VDD circuit A1A
VSS circuit A1A
-----
"""
        result = compare_pin_connections(
            {"A1": ["din", "dout", "vdd", "0"]}, report
        )
        self.assertEqual(result["status"], "unverified")
        self.assertEqual(result["unverified_components"], ["A1"])


if __name__ == "__main__":
    unittest.main()
