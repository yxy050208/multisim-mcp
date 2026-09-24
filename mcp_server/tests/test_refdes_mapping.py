"""Check native component identity without licensed templates or Multisim."""

import unittest
import xml.etree.ElementTree as ET

from multisim_mcp.schematic_builder import (
    ComponentSpec,
    _add_component_refdes_map,
    _refdes_info,
)


class RefdesMappingTest(unittest.TestCase):
    def test_zero_designators_keep_identity_and_section_separate(self) -> None:
        root = ET.fromstring("<Root><RefDesInfoContainer /></Root>")
        path = "C:/实验/电路.ms14"
        specs = [
            ComponentSpec("R", "R0", ["out", "0"]),
            ComponentSpec("DNOT4", "AINV0", ["in", "out", "vdd", "0"]),
        ]
        _add_component_refdes_map(root, specs, path, "minimal")
        mapping = root.find(".//CIRToInfoMap")
        entries = {item.get("CIRKey"): item.find("RefDesInfo") for item in mapping}
        self.assertEqual(set(entries), {"&ASCR0", "&ASCAINV0A"})
        for key, refdes, prefix, section in (
            ("&ASCR0", "R0", "R", ""),
            ("&ASCAINV0A", "AINV0", "AINV", "A"),
        ):
            with self.subTest(refdes=refdes):
                info = entries[key]
                self.assertEqual(info.get("IRPrefix"), f"&ASC{prefix}")
                self.assertEqual(info.get("IRNumber"), "0")
                self.assertEqual(info.get("IRSection"), "&ASCA" if section else "")
                data = info.find("RefDesInfoData")
                expected = f"&ASC!0!0!0{refdes}!0{path}!01!0minimal!0"
                self.assertEqual(data.get("RefDes"), expected)
                self.assertEqual(int(data.get("RefDesStrSize")), len(expected))

    def test_registration_preserves_existing_probe_and_component_records(self) -> None:
        root = ET.fromstring("<Root><RefDesInfoContainer><CIRToInfoMap />"
                             "</RefDesInfoContainer></Root>")
        mapping = root.find(".//CIRToInfoMap")
        probe = _refdes_info("PR1", "minimal", "circuit.ms14")
        mapping.append(probe)
        specs = [ComponentSpec("R", "R0", ["out", "0"])]
        _add_component_refdes_map(root, specs, "circuit.ms14", "minimal")
        before = ET.tostring(mapping)
        _add_component_refdes_map(root, specs, "circuit.ms14", "minimal")
        self.assertEqual(ET.tostring(mapping), before)
        self.assertIs(mapping[0], probe)
        self.assertEqual(len(mapping), 2)

    def test_ground_and_unrepresentable_designators_are_not_invented(self) -> None:
        root = ET.fromstring("<Root><RefDesInfoContainer /></Root>")
        specs = [
            ComponentSpec("GND", "GND1", ["0"]),
            ComponentSpec("R", "R_LOAD", ["out", "0"]),
        ]
        _add_component_refdes_map(root, specs, "circuit.ms14", "minimal")
        self.assertEqual(len(root.find(".//CIRToInfoMap")), 0)


if __name__ == "__main__":
    unittest.main()
