"""Verify resources required by an installed wheel are present."""

import unittest
import xml.etree.ElementTree as ET

from multisim_mcp.schematic_builder import (
    TEMPLATE_DIR,
    TEMPLATE_ONLY_ENV,
    TEMPLATE_PACK_ENV,
    build_schematic,
    prepare_simulation_netlist,
    template_search_paths,
)


class PackageDataTest(unittest.TestCase):
    def setUp(self) -> None:
        if (
            self._testMethodName != "test_local_template_pack_precedes_package_fallback"
            and not any(
                (path / "minimal.ms14.xml").is_file()
                for path in template_search_paths()
            )
        ):
            self.skipTest(
                "Public code-only wheel requires a user-generated local template pack"
            )

    def test_local_template_pack_precedes_package_fallback(self) -> None:
        import os
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {TEMPLATE_PACK_ENV: tmp, TEMPLATE_ONLY_ENV: "0"}
        ):
            paths = template_search_paths()
        self.assertEqual(paths[0], Path(tmp).resolve())
        self.assertEqual(paths[-1], TEMPLATE_DIR)

    def test_extractor_writes_decoder_compatible_compact_ascii(self) -> None:
        import importlib.util
        import tempfile
        from pathlib import Path

        tool_path = (
            Path(__file__).resolve().parents[2]
            / "tools"
            / "extract_native_component_templates.py"
        )
        spec = importlib.util.spec_from_file_location(
            "template_extractor_test", tool_path
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        root = ET.Element("Item")
        child = ET.SubElement(root, "Value")
        child.text = "\n    "
        child.tail = "\n  "
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "fragment.xml"
            module._write_template(output, root)
            payload = output.read_bytes()
        self.assertIn(b"encoding='ASCII'", payload)
        self.assertNotIn(b"\n  ", payload)

    def test_schematic_templates_are_installed(self) -> None:
        required = {
            "minimal.ms14.xml",
            "r_element.xml",
            "c_element.xml",
            "l_element.xml",
            "d_element.xml",
            "i_element.xml",
            "qnpn_element.xml",
            "qpnp_element.xml",
            "mnmos_element.xml",
            "mpmos_element.xml",
            "opamp5_element.xml",
            "dnot4_element.xml",
            "dand5_element.xml",
            "dor5_element.xml",
            "djk7_element.xml",
            "v_element.xml",
            "wire.xml",
        }
        present = {
            path.name
            for directory in template_search_paths()
            for path in directory.glob("*.xml")
        }
        self.assertTrue(required.issubset(present), required - present)

    def test_builder_can_load_packaged_templates(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "smoke.xml"
            result = build_schematic(
                "V1 in 0 DC 5\nR1 in 0 1k\n.end\n",
                output,
                probe_nets=[],
            )
            generated = output.read_text(encoding="utf-8")
        self.assertEqual(result["counts"]["components"], 3)
        self.assertEqual(result["unsupported"], [])
        self.assertNotIn("ASC18331", generated)
        self.assertIn("ASCmultisim-mcp", generated)

    def test_builder_classifies_semiconductor_models(self) -> None:
        import tempfile
        from pathlib import Path

        netlist = """\
V1 vdd 0 DC 5
I1 0 b1 DC 10u
D1 vdd d 1N4001
Q1 c1 b1 0 2N3904
Q2 c2 b2 vdd PDEVICE
M1 d1 g1 0 0 NDEVICE L=1u W=2u
M2 d2 g2 vdd vdd PCHANNEL L=1u W=4u
.model PDEVICE PNP(IS=1e-15)
.model NDEVICE NMOS(Level=1)
.model PCHANNEL PMOS(Level=1)
.end
"""
        with tempfile.TemporaryDirectory() as tmp:
            result = build_schematic(
                netlist,
                Path(tmp) / "semiconductors.xml",
                probe_nets=[],
            )
        self.assertEqual(
            [item["kind"] for item in result["components"]],
            ["V", "I", "D", "QNPN", "QPNP", "MNMOS", "MPMOS", "GND"],
        )
        self.assertEqual(result["unsupported"], [])

    def test_builder_supports_rlc_components(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "rlc.xml"
            result = build_schematic(
                "V1 in 0 DC 5\nR1 in n1 1k\nL1 n1 out 10m\nC1 out 0 1u\n.end\n",
                output,
                probe_nets=[],
            )
        self.assertEqual(
            [item["kind"] for item in result["components"]],
            ["V", "R", "L", "C", "GND"],
        )
        self.assertEqual(result["unsupported"], [])

    def test_builder_supports_mutual_inductor_coupling(self) -> None:
        import tempfile
        from pathlib import Path

        netlist = """\
V1 pri 0 SIN(0 1 1k)
L1 pri 0 10m
L2 sec 0 2.5m
K1 L1 L2 0.99
R1 sec 0 1k
.end
"""
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "transformer.xml"
            result = build_schematic(netlist, output, probe_nets=[])
            generated = output.read_text(encoding="utf-8")
        self.assertEqual(
            [item["kind"] for item in result["components"]],
            ["V", "L", "L", "K", "R", "GND"],
        )
        self.assertIn("k%p L1 L2 0.99", generated)

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "missing inductors"):
                build_schematic(
                    "K1 L1 L2 0.9\n.end\n",
                    Path(tmp) / "invalid.xml",
                    probe_nets=[],
                )

    def test_builder_supports_five_terminal_opamp_and_skips_subcircuit_body(self) -> None:
        import tempfile
        from pathlib import Path

        netlist = """\
V1 vp 0 15
V2 vn 0 -15
XU1 in 0 vp vn out OPAMP5
R1 out 0 1k
.subckt UNUSED a b
R99 a b 99k
.ends UNUSED
.end
"""
        with tempfile.TemporaryDirectory() as tmp:
            result = build_schematic(
                netlist,
                Path(tmp) / "opamp.xml",
                probe_nets=[],
            )
        self.assertEqual(
            [item["refdes"] for item in result["components"]],
            ["V1", "V2", "XU1", "R1", "0"],
        )
        self.assertEqual(result["unsupported"], [])

    def test_builder_supports_linear_controlled_sources(self) -> None:
        import tempfile
        from pathlib import Path

        netlist = """\
VCTRL sense 0 DC 1
E1 eo 0 sense 0 10
F1 fo 0 VCTRL 2
G1 go 0 sense 0 3m
H1 ho 0 VCTRL 4k
.end
"""
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "controlled.xml"
            result = build_schematic(netlist, output, probe_nets=[])
            generated = output.read_text(encoding="utf-8")
        self.assertEqual(
            [item["kind"] for item in result["components"]],
            ["V", "E", "F", "G", "H", "GND"],
        )
        self.assertEqual(result["unsupported"], [])
        self.assertIn("e%p %tD %tG %tS %tSUB 10", generated)
        self.assertIn("f%p %t2 %t1 VCTRL 2", generated)
        self.assertIn("g%p %tD %tG %tS %tSUB 0.003", generated)
        self.assertIn("h%p %t1 %t2 VCTRL 4000", generated)

    def test_builder_supports_behavioral_voltage_and_current_sources(self) -> None:
        import tempfile
        from pathlib import Path

        netlist = """\
V1 in 0 DC 2
B1 out 0 V={V(in)*2}
B2 load 0 I={V(out)/1000}
.end
"""
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "behavioral.xml"
            result = build_schematic(netlist, output, probe_nets=[])
            generated = output.read_text(encoding="utf-8")
        self.assertEqual(
            [item["kind"] for item in result["components"]],
            ["V", "BV", "BI", "GND"],
        )
        self.assertEqual(result["unsupported"], [])
        self.assertIn("b%p %t1 %t2 V={V(in)*2}", generated)
        self.assertIn("b%p %t2 %t1 I={V(out)/1000}", generated)

    def test_builder_supports_waveform_sources_and_lossless_line(self) -> None:
        import tempfile
        from pathlib import Path

        netlist = """\
V1 in 0 SIN(0 1 1k)
I1 load 0 PULSE(0 1m 1u 1n 1n 5u 10u)
T1 in 0 out 0 Z0=50 TD=10n
.end
"""
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "waveforms.xml"
            result = build_schematic(netlist, output, probe_nets=[])
            generated = output.read_text(encoding="utf-8")
        self.assertEqual(
            [item["kind"] for item in result["components"]],
            ["V", "I", "T", "GND"],
        )
        self.assertEqual(result["unsupported"], [])
        self.assertIn("v%p %t1 %t2 SIN(0 1 1k)", generated)
        self.assertIn("i%p %t2 %t1 PULSE(0 1m 1u 1n 1n 5u 10u)", generated)
        self.assertIn("t%p %tD %tG %tS %tSUB Z0=50 TD=10n", generated)

    def test_builder_supports_lossy_and_uniform_rc_lines(self) -> None:
        import tempfile
        from pathlib import Path

        netlist = """\
O1 in 0 out 0 OMOD
U1 in out 0 UMOD L=1 N=8
.model OMOD LTRA(R=1 L=1u G=0 C=1p LEN=1)
.model UMOD URC(RPERL=1k CPERL=1u)
.end
"""
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "distributed_lines.xml"
            result = build_schematic(netlist, output, probe_nets=[])
            generated = output.read_text(encoding="utf-8")
        self.assertEqual(
            [item["kind"] for item in result["components"]],
            ["O", "U", "GND"],
        )
        self.assertIn("o%p %tD %tG %tS %tSUB omdl%p", generated)
        self.assertIn(".model omdl%p LTRA(R=1 L=1u G=0 C=1p LEN=1)", generated)
        self.assertIn("u%p %tC %tB %tE umdl%p L=1 N=8", generated)

    def test_builder_registers_native_oscilloscope_and_strips_it_for_spice(self) -> None:
        import tempfile
        from pathlib import Path

        netlist = """\
V1 a 0 SIN(0 1 1k)
R1 a b 1k
XSC1 a b 0 0 a 0 OSCILLOSCOPE
.end
"""
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "oscilloscope.xml"
            result = build_schematic(netlist, output, probe_nets=[])
            root = ET.parse(output).getroot()

        self.assertEqual(
            [item["kind"] for item in result["components"]],
            ["V", "R", "OSC6", "GND"],
        )
        state = root.find(".//InstrumentsData/CSourceSymbolCollectNode")
        self.assertIsNotNone(state)
        self.assertEqual(
            state.get("CompLongName"),
            "&ASCXSC1#1/minimal:",
        )
        simulation = prepare_simulation_netlist(netlist)
        self.assertNotIn("XSC1", simulation)
        self.assertIn("V1 a 0 SIN(0 1 1k)", simulation)
        self.assertTrue(simulation.rstrip().endswith(".end"))

    def test_builder_configures_native_function_generator_and_spice_equivalent(self) -> None:
        import tempfile
        import xml.etree.ElementTree as ET
        from pathlib import Path

        netlist = """\
XFG1 out 0 inv FGEN WAVE=TRIANGLE FREQ=2k AMPLITUDE=2 OFFSET=0.5
R1 out 0 1k
R2 inv 0 1k
.end
"""
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "function_generator.xml"
            result = build_schematic(netlist, output, probe_nets=[])
            root = ET.parse(output).getroot()

        self.assertEqual(
            [item["kind"] for item in result["components"]],
            ["XFG3", "R", "R", "GND"],
        )
        state = root.find(".//InstrumentsData/CSourceSymbolCollectNode")
        self.assertEqual(state.get("CompLongName"), "&ASCXFG1#1/minimal:")
        settings = {
            item.get("Key"): item.find("./CDataElement").get("Data")
            for item in state.findall(".//Element")
            if item.find("./CDataElement") is not None
        }
        self.assertEqual(settings["&ASCNI_EWB_WAVE_MODE"], "2")
        self.assertEqual(settings["&ASCNI_EWB_FREQUENCY_VALUE"], "2000")
        self.assertEqual(settings["&ASCNI_EWB_AMPLITUDE_VALUE"], "2")
        self.assertEqual(settings["&ASCNI_EWB_OFFSET_VALUE"], "0.5")

        simulation = prepare_simulation_netlist(netlist)
        self.assertNotIn("XFG1 out", simulation)
        self.assertIn("B__XFG1_P out 0 V={0.5+2*(2/pi)*asin(sin(", simulation)
        self.assertIn("B__XFG1_N inv 0 V={-(0.5+2*(2/pi)*asin(sin(", simulation)
        square = prepare_simulation_netlist(
            "XFG2 out 0 inv FGEN WAVE=SQUARE FREQ=1k AMPLITUDE=1 "
            "OFFSET=0 DUTY=25 RISE=1u\n.end\n"
        )
        self.assertIn(
            "V__XFG2_P out 0 PULSE(-1 1 0 1e-06 1e-06 0.00025 0.001)",
            square,
        )
        with self.assertRaisesRegex(ValueError, "DUTY must be between"):
            prepare_simulation_netlist(
                "XFG3 out 0 inv FGEN WAVE=SQUARE DUTY=100\n.end\n"
            )

    def test_builder_supports_generic_two_to_five_terminal_subcircuits(self) -> None:
        import tempfile
        from pathlib import Path

        netlist = """\
X2 a b TWO_PIN
X3 a b c THREE_PIN
X4 a b c d FOUR_PIN
X5 a b c d e FIVE_PIN
.subckt TWO_PIN p n
R99 p n 1k
.ends TWO_PIN
.end
"""
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "subcircuits.xml"
            result = build_schematic(netlist, output, probe_nets=[])
            generated = output.read_text(encoding="utf-8")
        self.assertEqual(
            [item["kind"] for item in result["components"]],
            ["R", "XSUB3", "XSUB4", "XSUB5"],
        )
        self.assertEqual(result["unsupported"], [])
        self.assertEqual(len(result["model_warnings"]), 4)
        self.assertTrue(result["components"][0]["refdes"].startswith("RX"))
        self.assertIn("x%p %tC %tB %tE THREE_PIN", generated)
        self.assertIn("x%p %tD %tG %tS %tSUB FOUR_PIN", generated)
        self.assertIn("x%p %tIN+ %tIN- %tVS+ %tVS- %tOUT FIVE_PIN", generated)

    def test_builder_expands_inline_subcircuit_library_and_instance_parameters(self) -> None:
        import tempfile
        import xml.etree.ElementTree as ET
        from pathlib import Path

        netlist = """\
V1 in 0 1
XU1 in out GAIN PARAMS: SCALE=2
XU2 out out2 GAIN SCALE=3
.param GLOBAL_GAIN=10
.global VREF
.model DCLAMP D(IS=1n)
.subckt CHILD a b
D1 a b DCLAMP
.ends CHILD
.subckt GAIN in out PARAMS: SCALE=1
XCLAMP in mid CHILD
E1 out 0 in 0 {SCALE}
B1 shaped 0 V={SCALE*V(in)}
R1 shaped VREF {GLOBAL_GAIN}
.ends GAIN
.end
"""
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "inline-library.xml"
            result = build_schematic(netlist, output, probe_nets=[])
            generated = output.read_text(encoding="utf-8")

        self.assertEqual(
            result["subcircuits"],
            [
                {"name": "CHILD", "pins": ["a", "b"]},
                {"name": "GAIN", "pins": ["in", "out"]},
            ],
        )
        self.assertEqual(
            result["expanded_subcircuits"],
            [
                {"refdes": "XU1", "model": "GAIN", "components": 4},
                {"refdes": "XU2", "model": "GAIN", "components": 4},
            ],
        )
        self.assertEqual(result["subcircuit_expansion_failures"], [])
        self.assertEqual(
            result["editable_model_coverage"],
            {
                "status": "complete",
                "expanded_instances": 2,
                "carrier_only_instances": 0,
            },
        )
        self.assertFalse(
            any(item["kind"].startswith("XSUB") for item in result["components"])
        )
        gains = [
            item["value"]
            for item in result["components"]
            if item["kind"] == "E"
        ]
        self.assertEqual(gains, ["2", "3"])
        behavioral_models = [
            item["model"]
            for item in result["components"]
            if item["kind"] == "BV"
        ]
        self.assertIn("V={(2)*V(in)}", behavioral_models)
        self.assertIn("V={(3)*V(out)}", behavioral_models)
        self.assertEqual(
            [item["value"] for item in result["components"] if item["kind"] == "R"],
            ["10", "10"],
        )
        self.assertIn("vref", result["nets"])
        self.assertNotIn("xu1__vref", result["nets"])
        self.assertGreaterEqual(generated.count("DXUXCLAMP"), 2)

    def test_builder_marks_function_dependent_macro_as_carrier_only(self) -> None:
        import tempfile
        from pathlib import Path

        netlist = """\
.func softclip(x) {limit(x,-1,1)}
XU1 in out AMP
.subckt AMP in out
B1 out 0 V={softclip(V(in))}
.ends AMP
.end
"""
        with tempfile.TemporaryDirectory() as tmp:
            result = build_schematic(
                netlist, Path(tmp) / "function-macro.xml", probe_nets=[]
            )

        self.assertEqual(
            [item["kind"] for item in result["components"]], ["XSUBN"]
        )
        self.assertEqual(result["editable_model_coverage"]["status"], "carrier_only")
        self.assertIn(
            ".func call 'softclip' is not expandable",
            result["subcircuit_expansion_failures"][0]["reason"],
        )

    def test_builder_rejects_subcircuit_pin_count_mismatch(self) -> None:
        import tempfile
        from pathlib import Path

        netlist = """\
XU1 a b c TWO_PIN
.subckt TWO_PIN p n
R1 p n 1k
.ends TWO_PIN
.end
"""
        with tempfile.TemporaryDirectory() as tmp:
            result = build_schematic(
                netlist, Path(tmp) / "pin-mismatch.xml", probe_nets=[]
            )
        self.assertEqual(result["components"], [])
        self.assertIn("expects 2 pins, received 3", result["unsupported"][0])
        self.assertEqual(result["editable_model_coverage"]["status"], "carrier_only")

    def test_builder_supports_variable_six_to_sixteen_pin_subcircuits(self) -> None:
        import tempfile
        import xml.etree.ElementTree as ET
        from pathlib import Path

        nodes = " ".join(f"n{i}" for i in range(1, 13))
        netlist = f"X12 {nodes} TWELVE_PIN\n.end\n"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "subcircuit12.xml"
            result = build_schematic(netlist, output, probe_nets=[])
            generated = output.read_text(encoding="utf-8")
            root = ET.parse(output).getroot()
        self.assertEqual([item["kind"] for item in result["components"]], ["XSUBN"])
        self.assertEqual(result["counts"]["ports"], 16)
        self.assertEqual(result["unsupported"], [])
        self.assertIn("%tR5B TWELVE_PIN", generated)
        for terminal in (
            "R1A", "R2A", "R3A", "R4A", "R5A", "R6A",
            "R7A", "R8A", "R8B", "R7B", "R6B", "R5B",
        ):
            self.assertIn(f'LocalName="&amp;ASC{terminal}"', generated)
        pin_items = root.findall(
            ".//CIITSymbolComp/Objects/Item[@Class='CIITPinSymbolComp']"
        )
        item_ids = [item.get("ID") for item in pin_items]
        connector_ids = [
            item.find(
                "./CIITPinSymbolComp/Objects/Item[@Class='CIITPinConnectorComp']"
            ).get("ID")
            for item in pin_items
        ]
        self.assertEqual(len(item_ids), len(set(item_ids)))
        self.assertEqual(len(connector_ids), len(set(connector_ids)))

    def test_builder_embeds_models_for_semiconductors_and_switches(self) -> None:
        import tempfile
        from pathlib import Path

        netlist = """\
D1 a 0 DMOD area=2
Q1 c b 0 QMOD
M1 d g 0 0 MMOD L=1u W=2u
S1 out 0 ctrl 0 SWMOD ON
J1 jd jg 0 JMOD
Z1 zd zg 0 ZMOD
.model DMOD D(IS=2e-12)
.model QMOD NPN(BF=200)
.model MMOD NMOS(Level=1)
.model SWMOD SW(Ron=1 Roff=1G Vt=2)
.model JMOD NJF(Beta=1m)
.model ZMOD NMF(Beta=2m)
.end
"""
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "models.xml"
            result = build_schematic(netlist, output, probe_nets=[])
            generated = output.read_text(encoding="utf-8")
        self.assertEqual(
            [item["kind"] for item in result["components"]],
            ["D", "QNPN", "MNMOS", "S", "JN", "ZN", "GND"],
        )
        self.assertEqual(result["unsupported"], [])
        self.assertIn(".model dmdl%p D(IS=2e-12)", generated)
        self.assertIn(".model qmdl%p NPN(BF=200)", generated)
        self.assertIn(".model mmdl%p NMOS(Level=1)", generated)
        self.assertIn("s%p %tD %tG %tS %tSUB smdl%p ON", generated)
        self.assertIn(".model smdl%p SW(Ron=1 Roff=1G Vt=2)", generated)
        self.assertIn(".model jmdl%p NJF(Beta=1m)", generated)
        self.assertIn(".model zmdl%p NMF(Beta=2m)", generated)

    def test_builder_folds_continuations_and_supports_current_controlled_switch(self) -> None:
        import tempfile
        from pathlib import Path

        netlist = """\
V1 in 0 PWL(0 0 1u 1
+ 2u 0 3u 1)
W1 out 0 V1 WMOD ON
.model WMOD CSW(Ron=1
+ Roff=1G It=1m Ih=0.1m)
.end
"""
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "continued.xml"
            result = build_schematic(netlist, output, probe_nets=[])
            generated = output.read_text(encoding="utf-8")
        self.assertEqual(
            [item["kind"] for item in result["components"]],
            ["V", "W", "GND"],
        )
        self.assertEqual(result["unsupported"], [])
        self.assertIn("PWL(0 0 1u 1 2u 0 3u 1)", generated)
        self.assertIn("w%p %t1 %t2 V1 wmdl%p ON", generated)
        self.assertIn(".model wmdl%p CSW(Ron=1 Roff=1G It=1m Ih=0.1m)", generated)

    def test_builder_supports_preview_digital_gates_and_jk_flip_flop(self) -> None:
        import tempfile
        from pathlib import Path

        netlist = """\
A1 din n1 vdd 0 NOT
A2 n1 enable n2 vdd 0 AND2
A3 n2 bypass dout vdd 0 OR2
A4 j k clk set reset q qbar JKFF
.end
"""
        with tempfile.TemporaryDirectory() as tmp:
            result = build_schematic(
                netlist,
                Path(tmp) / "digital.xml",
                probe_nets=[],
            )
        self.assertEqual(
            [item["kind"] for item in result["components"]],
            ["DNOT4", "DAND5", "DOR5", "DJK7", "GND"],
        )
        self.assertEqual(result["unsupported"], [])
        self.assertEqual(len(result["model_warnings"]), 4)
        simulation = prepare_simulation_netlist(netlist)
        self.assertIn("A__A4_ADC [j k clk set reset]", simulation)
        self.assertIn("A__A4_DAC [d_A4_q d_A4_qbar] [q qbar] MCP_DAC", simulation)
        self.assertIn(".model JKFF d_jkff", simulation)

    def test_builder_and_simulation_translation_support_derived_logic_gates(self) -> None:
        import tempfile
        from pathlib import Path

        netlist = """\
A1 a b y1 vdd 0 NAND2
A2 a b y2 vdd 0 NOR2
A3 a b y3 vdd 0 XOR2
A4 a b y4 vdd 0 XNOR2
.end
"""
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "derived_logic.xml"
            result = build_schematic(netlist, output, probe_nets=[])
            generated = output.read_text(encoding="utf-8")
        self.assertEqual(
            [item["kind"] for item in result["components"]],
            ["DNAND5", "DNOR5", "DXOR5", "DXNOR5", "GND"],
        )
        self.assertIn(".MODEL MCP_NAND2%p d_nand", generated)
        self.assertIn(".MODEL MCP_XNOR2%p d_xnor", generated)

        simulation = prepare_simulation_netlist(netlist)
        self.assertIn("B__A1 y1 0 V={if(", simulation)
        self.assertIn("B__A4 y4 0 V={if(", simulation)
        self.assertNotIn(".model NAND2", simulation)


if __name__ == "__main__":
    unittest.main()
