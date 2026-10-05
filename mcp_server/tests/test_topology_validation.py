from __future__ import annotations

import unittest

from multisim_mcp.topology_validation import (
    compare_pin_connections,
    compare_roundtrip_topology,
    digital_port_net_map,
    source_port_net_map,
)


class TopologyValidationTest(unittest.TestCase):
    def test_roundtrip_passes_for_expected_names(self) -> None:
        result = compare_roundtrip_topology(["R1"], ["vin", "out"], "R1 vin out 1k")
        self.assertEqual(result["status"], "pass")

    def test_roundtrip_reports_missing_names(self) -> None:
        result = compare_roundtrip_topology(["R1", "C1"], ["vin"], "R1 vin 0 1k")
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["missing_components"], ["C1"])

    def test_roundtrip_accepts_multisim_hidden_xspice_supply_alias(self) -> None:
        result = compare_roundtrip_topology(
            ["A1"], ["high"], "VDD circuit A1\nA1 circuit 1"
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["accepted_net_aliases"], {"high": "vdd"})

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

    def test_numeric_pin_order_follows_native_port_inventory(self) -> None:
        # Generic AC voltage carriers expose native ports in 2,1 order.  The
        # source net list follows that native order: bus is pin 2 and ground
        # is pin 1.  A fixed numeric 1,2 comparison would reject this valid
        # connection.
        report = """title
-----
header
-----
0 circuit V1 1
bus circuit V1 2
-----
"""
        result = compare_pin_connections(
            {"V1": ["bus", "0"]},
            report,
            declared_ports={"V1": ["2", "1"]},
        )
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

    def test_named_xspice_ports_are_checked_against_model_inventory(self) -> None:
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
            {"A1": ["din", "dout", "high", "0"]},
            report,
            declared_ports={"A1": ["I1", "O1", "VDD", "VSS"]},
        )
        self.assertEqual(result["model_port_evidence"][0]["state"], "present")
        self.assertEqual(result["model_port_evidence"][0]["unknown_ports"], [])

    def test_named_digital_signal_rows_are_checked_against_source_nodes(self) -> None:
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
            {"A1": ["din", "dout", "high", "0"]},
            report,
            declared_ports={"A1": ["I1", "O1", "VDD", "VSS"]},
            expected_named_ports={
                "A1": digital_port_net_map("DNOT4", ["din", "dout", "high", "0"])
            },
        )
        self.assertEqual(result["status"], "unverified")
        self.assertEqual(result["named_pin_counts"]["pass"], 2)
        self.assertEqual(result["named_pin_counts"]["unverified"], 2)

    def test_named_digital_signal_row_mismatch_fails(self) -> None:
        report = """title
-----
header
-----
wrong circuit A1A I1
dout circuit A1A O1
VDD circuit A1A
VSS circuit A1A
-----
"""
        result = compare_pin_connections(
            {"A1": ["din", "dout", "high", "0"]},
            report,
            declared_ports={"A1": ["I1", "O1", "VDD", "VSS"]},
            expected_named_ports={
                "A1": digital_port_net_map("DNOT4", ["din", "dout", "high", "0"])
            },
        )
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["named_pin_counts"]["fail"], 1)

    def test_native_xml_mapping_completes_hidden_power_rows(self) -> None:
        report = """title
-----
header
-----
din circuit A1A I1
dout circuit A1A O1
-----
"""
        result = compare_pin_connections(
            {"A1": ["din", "dout", "high", "0"]},
            report,
            declared_ports={"A1": ["I1", "O1", "VDD", "VSS"]},
            expected_named_ports={
                "A1": digital_port_net_map("DNOT4", ["din", "dout", "high", "0"])
            },
            native_port_nets={
                "A1": {"VDD": ["high"], "VSS": ["0"]}
            },
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["named_pin_counts"], {"pass": 4, "fail": 0, "unverified": 0})

    def test_digital_port_contract_rejects_wrong_arity(self) -> None:
        self.assertEqual(digital_port_net_map("DNOT4", ["in", "out"]), {})

    def test_diode_named_ports_are_checked_against_source_nodes(self) -> None:
        report = """title
-----
header
-----
raw circuit D1 A
filt circuit D1 K
-----
"""
        result = compare_pin_connections(
            {"D1": ["raw", "filt"]},
            report,
            declared_ports={"D1": ["A", "K"]},
            expected_named_ports={
                "D1": source_port_net_map("D", ["raw", "filt"])
            },
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["named_pin_counts"], {"pass": 2, "fail": 0, "unverified": 0})

    def test_vcvs_carrier_ports_are_checked_against_source_nodes(self) -> None:
        report = """title
-----
header
-----
raw circuit E1 D
0 circuit E1 G
ctrl circuit E1 S
0 circuit E1 SUB
-----
"""
        result = compare_pin_connections(
            {"E1": ["raw", "0", "ctrl", "0"]},
            report,
            declared_ports={"E1": ["D", "G", "S", "SUB"]},
            expected_named_ports={
                "E1": source_port_net_map("E", ["raw", "0", "ctrl", "0"])
            },
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["named_pin_counts"], {"pass": 4, "fail": 0, "unverified": 0})


if __name__ == "__main__":
    unittest.main()
