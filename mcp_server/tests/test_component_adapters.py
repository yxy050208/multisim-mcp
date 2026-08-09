"""COM-free coverage for the public component adapter API."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from multisim_mcp.component_adapters import (
    component_adapter_catalog,
    expand_component_adapters,
)
from multisim_mcp.schematic_builder import parse_netlist, prepare_simulation_netlist


class BuiltinAdapterTest(unittest.TestCase):
    def test_expands_analog_and_power_models(self) -> None:
        text = """\
XT p1 p2 s1 s2 @TRANSFORMER LP=2m LS=500u K=.98
XP vdd w 0 @POTENTIOMETER R=10k POSITION=.25
XR cp 0 a b @RELAY RCOIL=200 VON=2
XC a b @CRYSTAL
XD a b @POWER_DIODE BV=200
XM d g 0 @POWER_NMOS VTO=4
.end
"""
        expanded = expand_component_adapters(text)
        self.assertIn("L1 p1 p2 0.002", expanded)
        self.assertIn("R1 vdd w 2500", expanded)
        self.assertIn(".model MR_RELAY SW", expanded)
        self.assertIn(".model MD_PD D", expanded)
        self.assertIn(".model MM_NMOS NMOS", expanded)
        parsed = parse_netlist(text)
        self.assertEqual(parsed.unsupported, [])

    def test_expands_digital_and_mixed_signal_adapters(self) -> None:
        text = """\
VDD high 0 5
XDFF d clk set reset q qb high 0 @DFF
XTFF t clk set reset tq tqb high 0 @TFF
XCOUNT clk reset q0 q1 q2 q3 high 0 @COUNTER4
XSHIFT d clk reset s0 s1 s2 s3 high 0 @SHIFT_REGISTER4
XADC analog digital high 0 @ADC1 THRESHOLD=.6
XDAC digital analog2 high 0 @DAC1
.end
"""
        parsed = parse_netlist(text)
        self.assertEqual(parsed.unsupported, [])
        self.assertGreaterEqual(sum(item.kind == "DJK7" for item in parsed.components), 10)
        executable = prepare_simulation_netlist(text)
        self.assertIn("d_jkff", executable)
        self.assertIn("B1 digital 0", executable)
        self.assertNotIn("@DFF", executable)

    def test_rejects_unknown_parameters_and_non_finite_values(self) -> None:
        with self.assertRaises(ValueError):
            expand_component_adapters("X1 a b @CRYSTAL UNKNOWN=1\n")
        with self.assertRaises(ValueError):
            expand_component_adapters("X1 a b c @POTENTIOMETER POSITION=nan\n")

    def test_catalog_is_bilingual(self) -> None:
        catalog = component_adapter_catalog()
        self.assertGreaterEqual(len(catalog["adapters"]), 13)
        self.assertTrue(all(item["description_zh"] and item["description_en"] for item in catalog["adapters"]))


class CommunityAdapterPackTest(unittest.TestCase):
    def test_loads_strict_declarative_pack(self) -> None:
        manifest = {
            "schema_version": 1,
            "kind": "SERIES_RESISTOR",
            "terminals": ["p", "n"],
            "parameters": [{"name": "R", "default": 1000, "minimum": 0.001}],
            "expansion": ["R{stem} {p} {n} {R}"],
            "description_zh": "串联电阻",
            "description_en": "Series resistor",
        }
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "adapter.json"), "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            with patch.dict(os.environ, {"MULTISIM_MCP_ADAPTER_DIR": tmp}):
                expanded = expand_component_adapters("XU in out @SERIES_RESISTOR R=2k\n")
        self.assertEqual(expanded, "R1 in out 2000\n")

    def test_rejects_directive_expansion(self) -> None:
        manifest = {
            "schema_version": 1,
            "kind": "UNSAFE",
            "terminals": ["p"],
            "parameters": [],
            "expansion": [".include {p}"],
            "description_zh": "x",
            "description_en": "x",
        }
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "adapter.json"), "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            with patch.dict(os.environ, {"MULTISIM_MCP_ADAPTER_DIR": tmp}):
                with self.assertRaises(ValueError):
                    component_adapter_catalog()

    def test_rejects_inverted_parameter_bounds(self) -> None:
        manifest = {
            "schema_version": 1,
            "kind": "BADBOUNDS",
            "terminals": ["p", "n"],
            "parameters": [
                {"name": "R", "default": 5, "minimum": 10, "maximum": 1}
            ],
            "expansion": ["R{stem} {p} {n} {R}"],
            "description_zh": "test",
            "description_en": "test",
        }
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "adapter.json"), "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            with patch.dict(os.environ, {"MULTISIM_MCP_ADAPTER_DIR": tmp}):
                with self.assertRaises(ValueError):
                    component_adapter_catalog()


if __name__ == "__main__":
    unittest.main()
