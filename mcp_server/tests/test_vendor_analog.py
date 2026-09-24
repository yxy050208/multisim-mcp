"""Regression coverage for model preservation and honest native acceptance."""
import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from multisim_mcp.generated_analog_run import evaluate_evidence, validate_proposal
from multisim_mcp.linear_reference import expected_native_pins, solve_linear
from multisim_mcp.native_xml import parse_native_xml, write_native_xml
from multisim_mcp.native_netlist_validation import validate_native_netlist
from multisim_mcp.schematic_builder import ComponentSpec, _configure_component_semantics, build_schematic, _load_template, template_search_paths

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("thesis_test_case", ROOT / "tools/run_thesis_plugin_acceptance.py")
case = importlib.util.module_from_spec(spec)
spec.loader.exec_module(case)


class NativeXmlTests(unittest.TestCase):
    def test_voltage_pin_semantics_do_not_depend_on_template_filename_order(self):
        if not any((p/"minimal.ms14.xml").is_file() for p in template_search_paths()):
            self.skipTest("requires local templates")
        original = _load_template
        def swapped(name):
            return original({"v_port1.xml":"v_port2.xml", "v_port2.xml":"v_port1.xml"}.get(name,name))
        with tempfile.TemporaryDirectory() as tmp, patch("multisim_mcp.schematic_builder._load_template",side_effect=swapped), patch("multisim_mcp.schematic_builder.voltage_source_stem",return_value="v"):
            target=Path(tmp)/"source.xml"
            build_schematic("V1 in 0 DC 1\nR1 in 0 1k\n.end",target)
            root=parse_native_xml(target).getroot()
            nodes={item.get("CiID"):item.find("CiNode").get("LocalName").removeprefix("&ASC") for item in root.iter("Item") if item.find("CiNode") is not None}
            comp_id=next(item.get("CiID") for item in root.iter("Item") if item.find("CiComponent") is not None and item.find("CiComponent").get("LocalName")=="&ASCV1")
            pins={port.get("LocalName"):nodes[port.find("Nodes/Item").get("CiID")] for port in root.iter("CiPort") if port.get("Component")==comp_id}
            self.assertEqual(pins,{"&ASC2":"in","&ASC1":"0"})

    def test_model_lines_and_literal_character_reference_survive_roundtrip(self):
        # Literal numeric references in content must not be confused with
        # XML's encoding of a real newline. The encoder does not decode them.
        model = "* example\r\n.SUBCKT TEST a b\nR1 a b 1k\n.ENDS\t"
        root = ET.Element("Item", {"Value": model, "Literal":"&#10;"})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"source.xml"
            write_native_xml(ET.ElementTree(root), path)
            payload = path.read_bytes()
            self.assertIn(b"\r\n.SUBCKT", payload)
            self.assertIn(b"&amp;#10;", payload)
            parsed = parse_native_xml(path).getroot()
            self.assertEqual(parsed.attrib, root.attrib)

    def test_standard_parser_would_flatten_raw_decoder_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"source.xml"
            path.write_text('<Item Value="* comment\n.SUBCKT TEST a b\n.ENDS"/>', encoding="utf-8")
            self.assertNotIn("\n", ET.parse(path).getroot().get("Value"))
            self.assertEqual(parse_native_xml(path).getroot().get("Value").count("\n"),2)

    def test_vendor_template_cannot_silently_use_flattened_model(self):
        item = ET.Element("Item")
        component = ET.SubElement(item,"CiComponent")
        for value in ("&ASCLM324AJ", "&ASC* comment .SUBCKT LM158_4 a b .ENDS"):
            ET.SubElement(component,"Item",Value=value)
        with self.assertRaisesRegex(ValueError,"intact local"):
            _configure_component_semantics(item,ComponentSpec(kind="LM324AJ",refdes="U1",nodes=["a","b","c","d","e"],model="LM324AJ"))


class VendorContractTests(unittest.TestCase):
    def test_numeric_prefix_section_pins_are_checked_not_dropped(self):
        expected={"U1A":{"1IN+":"in","1IN-":"fb","1OUT":"out"}}
        text="in design U1A 1IN+\nfb design U1A 1IN-\nout design U1A 1OUT\n"
        self.assertTrue(validate_native_netlist(text,expected,strict=True)["ok"])
        self.assertFalse(validate_native_netlist(text.replace("in design","other design"),expected,strict=True)["ok"])

    def test_vendor_has_explicit_native_verification_and_section_pin_map(self):
        q,parts = validate_proposal(case.proposal())
        self.assertEqual(q["verification_method"],"native-vendor-sampled")
        self.assertEqual(expected_native_pins(parts)["U1A"]["1IN+"],"lp1")
        with self.assertRaisesRegex(ValueError,"no ideal"):
            solve_linear(parts)

    def test_all_experiments_need_declared_checks(self):
        q=case.proposal();q["checks"]=[c for c in q["checks"] if c["analysis"]!="tran"]
        with self.assertRaisesRegex(ValueError,"every requested analysis"):
            validate_proposal(q)

    def test_transient_window_must_be_inside_duration(self):
        q=case.proposal();q["checks"][-1]["time_max_s"]=1
        with self.assertRaisesRegex(ValueError,"inside the requested duration"):
            validate_proposal(q)

    def test_bad_pulse_and_unverified_variant_rejected(self):
        q=case.proposal();q["netlist"]=q["netlist"].replace("5m 10m)","15m 10m)")
        with self.assertRaisesRegex(ValueError,"PULSE timing"):
            validate_proposal(q)
        q=case.proposal();q["netlist"]=q["netlist"].replace("LM324AJ","LM324M")
        with self.assertRaisesRegex(ValueError,"vendor"):
            validate_proposal(q)

    def test_vendor_measurements_never_claim_independent_reference_pass(self):
        q,parts=validate_proposal(case.proposal())
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for index,experiment in enumerate(q["experiments"],1):
                folder=root/f"analysis-{index:03d}";folder.mkdir()
                kind=experiment["type"]
                columns=[{"op":"op_index","ac":"frequency_hz","tran":"time_s"}[kind]]
                for signal in experiment["outputs"]:
                    columns.extend([signal+".real",signal+".imaginary"] if kind=="ac" else [signal+".value"])
                rows = [[0, .1,.2,1]] if kind=="op" else [[10,1,0,2,0,10,0],[10000,1,0,.02,0,.2,0]] if kind=="ac" else [[0,0,0,0],[.0005,0,0,0],[.002,.1,.2,1],[.005,.1,.2,1]]
                with (folder/"data.csv").open("w",newline="",encoding="utf-8") as stream:
                    writer=csv.writer(stream);writer.writerow(columns);writer.writerows(rows)
            result=evaluate_evidence(root,q,parts)
            self.assertTrue(result["requirements_verified"])
            self.assertIsNone(result["reference_passed"])
            self.assertFalse(result["reference_applicable"])
            self.assertEqual(result["reference_comparisons"],[])
            q["checks"][-1]["time_min_s"]=.003
            q["checks"][-1]["time_max_s"]=.004
            self.assertFalse(evaluate_evidence(root,q,parts)["requirements_verified"])


if __name__ == "__main__":
    unittest.main()
