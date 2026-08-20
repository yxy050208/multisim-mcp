from __future__ import annotations

import unittest

from multisim_mcp.eda_core import CircuitComponent, CircuitDesign
from multisim_mcp.spice_adapter import (
    circuit_design_from_spice,
    circuit_design_to_spice,
)


class SpiceAdapterTest(unittest.TestCase):
    def test_import_preserves_authoritative_source_and_structured_evidence(self) -> None:
        netlist = """\
.param GAIN=2
V1 in 0 PULSE(0 5 0 1n 1n 1u 2u)
R1 in out 1k
D1 out 0 DCLAMP
.model DCLAMP D(IS=1n)
.end
"""
        design = circuit_design_from_spice(
            netlist, design_id="imported-divider", title="Imported divider"
        )

        self.assertEqual(design.design_id, "imported-divider")
        self.assertEqual(design.parameters["GAIN"], "2")
        self.assertEqual(
            [(item.refdes, item.kind) for item in design.components],
            [("V1", "V"), ("R1", "R"), ("D1", "D")],
        )
        self.assertEqual(design.components[0].model, "PULSE(0 5 0 1n 1n 1u 2u)")
        self.assertEqual(design.model_references[0].name, "model:DCLAMP")
        self.assertEqual(circuit_design_to_spice(design), netlist)

        source_with_trailing_blank_line = netlist + "\n"
        self.assertEqual(
            circuit_design_to_spice(
                circuit_design_from_spice(source_with_trailing_blank_line)
            ),
            source_with_trailing_blank_line,
        )

    def test_structured_design_compiles_only_documented_spice_subset(self) -> None:
        design = CircuitDesign(
            design_id="structured-filter",
            title="Structured filter",
            parameters={"GAIN": "2"},
            components=(
                CircuitComponent("V1", "V", ("in", "0"), value="5"),
                CircuitComponent("R1", "R", ("in", "out"), value="1k"),
                CircuitComponent(
                    "B1", "BV", ("shaped", "0"), model="V=V(out)*2"
                ),
                CircuitComponent(
                    "X1",
                    "XSUB2",
                    ("out", "shaped"),
                    model="AMP",
                    parameters={"tokens": ["GAIN=2"]},
                ),
            ),
        )

        self.assertEqual(
            circuit_design_to_spice(design),
            """\
.param GAIN=2
V1 in 0 5
R1 in out 1k
B1 shaped 0 V=V(out)*2
X1 out shaped AMP GAIN=2
.end
""",
        )

    def test_inline_subcircuit_import_reports_expansion_without_losing_source(self) -> None:
        netlist = """\
X1 in out AMP
.subckt AMP a b
R1 a b 1k
.ends AMP
.end
"""
        design = circuit_design_from_spice(netlist)

        evidence = design.annotations["spice_import"]
        self.assertEqual(evidence["expanded_subcircuits"][0]["refdes"], "X1")
        self.assertEqual(design.model_references[0].name, "subckt:AMP")
        self.assertEqual([item.kind for item in design.components], ["R"])
        self.assertEqual(circuit_design_to_spice(design), netlist)
        expanded = circuit_design_to_spice(design, prefer_source=False)
        self.assertIn("R", expanded)
        self.assertNotIn(".subckt", expanded.lower())

    def test_structured_compiler_covers_extended_existing_component_families(self) -> None:
        cases = (
            (
                CircuitComponent(
                    "V1", "V", ("p", "0"), model="PULSE(0 5 0 1n 1n 1u 2u)"
                ),
                "V1 p 0 PULSE(0 5 0 1n 1n 1u 2u)",
            ),
            (CircuitComponent("E1", "E", ("o", "0", "i", "0"), value="2"), "E1 o 0 i 0 2"),
            (
                CircuitComponent(
                    "F1", "F", ("o", "0"), value="3", parameters={"tokens": ["VSENSE"]}
                ),
                "F1 o 0 VSENSE 3",
            ),
            (CircuitComponent("B1", "BV", ("o", "0"), model="V=V(i)*2"), "B1 o 0 V=V(i)*2"),
            (
                CircuitComponent("T1", "T", ("a", "0", "b", "0"), model="Z0=50 TD=1n"),
                "T1 a 0 b 0 Z0=50 TD=1n",
            ),
            (CircuitComponent("D1", "D", ("a", "0"), model="DFAST"), "D1 a 0 DFAST"),
            (
                CircuitComponent("Q1", "QNPN", ("c", "b", "e"), model="2N3904"),
                "Q1 c b e 2N3904",
            ),
            (
                CircuitComponent("M1", "MNMOS", ("d", "g", "s", "b"), model="NM"),
                "M1 d g s b NM",
            ),
            (
                CircuitComponent(
                    "W1",
                    "W",
                    ("a", "b"),
                    model="SWI",
                    parameters={"tokens": ["VSENSE"]},
                ),
                "W1 a b VSENSE SWI",
            ),
            (
                CircuitComponent("O1", "O", ("a", "0", "b", "0"), model="LOSSY"),
                "O1 a 0 b 0 LOSSY",
            ),
            (CircuitComponent("U1", "U", ("a", "b", "0"), model="URC"), "U1 a b 0 URC"),
            (
                CircuitComponent("X1", "XSUB3", ("a", "b", "0"), model="MACRO"),
                "X1 a b 0 MACRO",
            ),
            (
                CircuitComponent("A1", "DNOT4", ("i", "o", "vdd", "0"), model="NOT"),
                "A1 i o vdd 0 NOT",
            ),
            (
                CircuitComponent(
                    "XFG1",
                    "XFG3",
                    ("p", "n", "com"),
                    model="FGEN",
                    parameters={"tokens": ["FREQ=1k"]},
                ),
                "XFG1 p n com FGEN FREQ=1k",
            ),
            (
                CircuitComponent(
                    "XSC1", "OSC6", ("a", "b", "c", "d", "trig", "0"), model="OSCILLOSCOPE"
                ),
                "XSC1 a b c d trig 0 OSCILLOSCOPE",
            ),
        )
        for index, (component, expected) in enumerate(cases):
            with self.subTest(kind=component.kind):
                design = CircuitDesign(
                    design_id=f"family-{index}",
                    title=component.kind,
                    components=(component,),
                )
                self.assertEqual(
                    circuit_design_to_spice(design), f"{expected}\n.end\n"
                )

    def test_import_and_compile_supports_node_less_coupling_records(self) -> None:
        netlist = "L1 a 0 1m\nL2 b 0 2m\nK1 L1 L2 0.9\n.end\n"
        design = circuit_design_from_spice(netlist)
        self.assertEqual([item.kind for item in design.components], ["L", "L", "K"])
        self.assertEqual(design.components[-1].nodes, ())
        self.assertEqual(circuit_design_to_spice(design, prefer_source=False), netlist)

    def test_import_and_compiler_fail_closed_on_unsafe_or_unsupported_records(self) -> None:
        with self.assertRaisesRegex(ValueError, "not allowed"):
            circuit_design_from_spice(".include vendor.lib\n.end\n")
        with self.assertRaisesRegex(ValueError, "unsupported schematic"):
            circuit_design_from_spice("Y1 a b UNKNOWN\n.end\n")
        unsupported = CircuitDesign(
            design_id="unsupported",
            title="Unsupported",
            components=(
                CircuitComponent("A1", "PROPRIETARY", ("a", "0"), model="P"),
            ),
        )
        with self.assertRaisesRegex(ValueError, "unsupported structured"):
            circuit_design_to_spice(unsupported)

    def test_compiler_rejects_non_scalar_parameters_and_multiline_tokens(self) -> None:
        nested_parameter = CircuitDesign(
            design_id="nested-parameter",
            title="Nested parameter",
            components=(CircuitComponent("R1", "R", ("a", "0"), value="1k"),),
            parameters={"VALUES": [1, 2]},
        )
        with self.assertRaisesRegex(ValueError, "not a scalar"):
            circuit_design_to_spice(nested_parameter)
        multiline_node = CircuitDesign(
            design_id="multiline-node",
            title="Multiline node",
            components=(
                CircuitComponent("R1", "R", ("a\nb", "0"), value="1k"),
            ),
        )
        with self.assertRaisesRegex(ValueError, "one SPICE token"):
            circuit_design_to_spice(multiline_node)


if __name__ == "__main__":
    unittest.main()
