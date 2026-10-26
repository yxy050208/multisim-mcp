"""Fixture integrity and native handoff failures must occur before any backend."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from multisim_mcp.executable_approvals import approve_executable_netlist, validate_executable_netlist_approval
from multisim_mcp.executable_netlists import compile_executable_netlist
from multisim_mcp.generated_analog_run import run_generated_analog_project
from multisim_mcp.generated_analog_run import ideal_model_fingerprints, _analyze_saved_values
from multisim_mcp.linear_reference import solve_linear, validated_components
from multisim_mcp.simulation_approvals import approve_simulation_plan, validate_simulation_plan_approval
from tests.test_active_signal_compiler import active_draft, component_review, netlist_review


def fixture_contract(dc=2.5, source=100):
    return {"schema_version": 1, "target_version": "14.3", "dc_voltage_v": dc,
            "source_impedance_ohm": source, "included_load_ohm": 100000,
            "ac_amplitude_v": 1, "ac_phase_deg": 0}


def compiled_fixture(**inputs):
    draft = active_draft(**inputs)
    return compile_executable_netlist(draft, component_review(draft),
        experiment_fixture=fixture_contract(source=inputs.get("source_impedance_ohm", 100)))


def fixture_review(preview):
    return {**netlist_review(preview), "confirm_fixture": True,
            "fixture_sha256": preview["experiment_fixture"]["contract_sha256"]}


def native_proposal(preview):
    return {"title": "Reviewed signal fixture", "application": "Native OP/AC acceptance",
            "netlist": preview["spice_netlist"], "probe_nets": ["fixture_source", "path_04"],
            "experiments": [{"type": "op"}, {"type": "ac", "commands": "ac dec 10 10 10000"}],
            "checks": [{"analysis": "op", "net": "path_04", "quantity": "value", "min": 1.499, "max": 1.501}]}


class ExperimentFixturesTest(unittest.TestCase):
    def test_fixture_creates_distinct_preview_with_correct_dc_ac_and_single_load(self):
        preview = compiled_fixture()
        base = preview["experiment_fixture"]["base_preview"]
        self.assertNotEqual(preview["compiled_digest"], base["compiled_digest"])
        self.assertNotIn("VINPUT", base["spice_netlist"])
        parts = validated_components(preview["spice_netlist"])
        self.assertEqual(sum(p.refdes == "RLOAD" for p in parts), 1)
        self.assertAlmostEqual(solve_linear(parts)["path_04"].real, 1.5, places=9)
        expected = 0.4 / complex(1, 1)
        self.assertLess(abs(solve_linear(parts, 1000)["path_04"] - expected), 1e-10)
        approval = approve_executable_netlist(preview, fixture_review(preview))
        decoded = json.loads(json.dumps([preview, approval]))
        self.assertEqual(validate_executable_netlist_approval(*decoded), approval)
        self.assertEqual(approval["experiment_fixture"]["base_compiled_digest"], base["compiled_digest"])

    def test_zero_source_is_direct_and_ac_phase_is_respected(self):
        draft = active_draft(source_impedance_ohm=0)
        preview = compile_executable_netlist(draft, component_review(draft),
            experiment_fixture={**fixture_contract(source=0), "ac_phase_deg": 90})
        parts = validated_components(preview["spice_netlist"])
        self.assertFalse(any(p.refdes == "RSOURCE" for p in parts))
        self.assertAlmostEqual(solve_linear(parts, 1000)["path_04"].imag, 0.2, places=9)

    def test_invalid_fixture_fails_closed(self):
        draft = active_draft()
        approved = component_review(draft)
        cases = [{"source_impedance_ohm": 101}, {"included_load_ohm": 99999}, {"dc_voltage_v": -0.1},
                 {"dc_voltage_v": 5.1}, {"dc_voltage_v": True}, {"dc_voltage_v": "2.5V"},
                 {"dc_voltage_v": float("nan")}, {"ac_amplitude_v": 0}, {"ac_amplitude_v": float("inf")},
                 {"ac_phase_deg": 181}, {"waveform": "PULSE(0 5)"}, {"target_version": "14.2"},
                 {"schema_version": True}]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                compile_executable_netlist(draft, approved, experiment_fixture={**fixture_contract(), **changes})
        raw = fixture_contract()
        raw.pop("ac_amplitude_v")
        with self.assertRaisesRegex(ValueError, "phase requires"):
            compile_executable_netlist(draft, approved, experiment_fixture=raw)

    def test_explicit_fixture_confirmation_and_digest_are_required(self):
        preview = compiled_fixture()
        with self.assertRaisesRegex(ValueError, "confirm_fixture"):
            approve_executable_netlist(preview, netlist_review(preview))
        with self.assertRaisesRegex(ValueError, "fixture_sha256"):
            approve_executable_netlist(preview, {**fixture_review(preview), "fixture_sha256": "0" * 64})
        base = preview["experiment_fixture"]["base_preview"]
        old_approval = approve_executable_netlist(base, netlist_review(base))
        with self.assertRaises(ValueError):
            validate_executable_netlist_approval(preview, old_approval)

    def test_composition_reconstruction_rejects_metadata_pins_values_and_lineage_tampering(self):
        preview = compiled_fixture()
        approved = approve_executable_netlist(preview, fixture_review(preview))
        changes = [lambda p: p["experiment_fixture"]["contract"].update(dc_voltage_v=3),
                   lambda p: p["experiment_fixture"]["net_aliases"].update({"path-00": "wrong"}),
                   lambda p: p["circuit_design"]["components"][-1].update(value="200"),
                   lambda p: p["circuit_design"]["components"][-1].update(nodes=["path_04", "path_00"]),
                   lambda p: p["experiment_fixture"]["base_preview"].update(compiled_digest="0"*64),
                   lambda p: p["synthesis"]["interface_contract"].update(included_load_ohm=20000),
                   lambda p: p.update(spice_netlist=p["spice_netlist"].replace("DC 2.5", "DC 3")),
                   lambda p: p["experiment_fixture"]["base_preview"].update(experiment_fixture={})]
        for change in changes:
            modified = copy.deepcopy(preview)
            change(modified)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_executable_netlist_approval(modified, approved)

    def test_simulation_approval_binds_exact_fixture_and_rejects_added_source(self):
        preview = compiled_fixture()
        approved = approve_executable_netlist(preview, fixture_review(preview))
        spec = {"schema_version": 1, "title": "OP", "netlist": preview["spice_netlist"], "commands": "op",
                "requirements": [{"id": "out", "metric": "mean", "signal": "V(path_04)",
                                  "operator": "between", "lower": 1.499, "upper": 1.501}]}
        review = {"approved": True, "netlist_approval_id": approved["approval_id"],
                  "netlist_approval_digest": approved["approval_digest"], "confirm_netlist": True,
                  "confirm_commands": True, "confirm_measurements": True, "confirm_limits": True}
        plan = approve_simulation_plan(preview, approved, spec, review)
        self.assertEqual(validate_simulation_plan_approval(preview, approved, spec, plan), plan)
        with self.assertRaisesRegex(ValueError, "does not match"):
            approve_simulation_plan(preview, approved, {**spec, "netlist": spec["netlist"].replace("DC 2.5", "DC 3")}, review)

    def test_native_preview_records_exact_netlist_approval_without_writes(self):
        preview = compiled_fixture()
        approved = approve_executable_netlist(preview, fixture_review(preview))
        with tempfile.TemporaryDirectory() as tmp, patch("multisim_mcp.generated_analog_run.build_schematic") as backend:
            destination = Path(tmp) / "native"
            result = run_generated_analog_project(native_proposal(preview), str(destination),
                executable_netlist=preview, netlist_approval=approved)
            self.assertEqual(result["approval_provenance"]["compiled_digest"], preview["compiled_digest"])
            self.assertFalse(result["approval_provenance"]["simulation_plan_approval_present"])
            self.assertFalse(destination.exists())
            backend.assert_not_called()

    def test_native_mismatch_and_overrides_fail_before_backend(self):
        preview = compiled_fixture()
        approved = approve_executable_netlist(preview, fixture_review(preview))
        proposal = native_proposal(preview)
        options = [{"netlist_approval": None}, {"native_parameters": {}}, {"native_source": "other.ms14"}]
        with tempfile.TemporaryDirectory() as tmp, patch("multisim_mcp.generated_analog_run.build_schematic") as backend:
            for changes in options:
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    run_generated_analog_project(proposal, str(Path(tmp)/"run"), execute=True,
                        **{"executable_netlist": preview, "netlist_approval": approved, **changes})
            with self.assertRaisesRegex(ValueError, "does not match"):
                run_generated_analog_project({**proposal, "netlist": proposal["netlist"].replace("DC 2.5", "DC 3")},
                    str(Path(tmp)/"run"), execute=True, executable_netlist=preview, netlist_approval=approved)
            backend.assert_not_called()
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_mcp_compile_matches_python(self):
        from multisim_mcp import server
        draft = active_draft()
        approved = component_review(draft)
        self.assertEqual(server.compile_executable_netlist(draft, approved, fixture_contract()), compiled_fixture())

    def test_native_ac_requires_a_reviewed_ac_fixture(self):
        draft = active_draft()
        contract = fixture_contract()
        contract.pop("ac_amplitude_v")
        contract.pop("ac_phase_deg")
        preview = compile_executable_netlist(draft, component_review(draft), experiment_fixture=contract)
        approved = approve_executable_netlist(preview, fixture_review(preview))
        with self.assertRaisesRegex(ValueError, "reviewed AC fixture"):
            run_generated_analog_project(native_proposal(preview), "unused", execute=True,
                executable_netlist=preview, netlist_approval=approved)

    def test_alias_collisions_are_not_merged(self):
        from multisim_mcp.experiment_fixtures import compose_experiment_fixture
        base = copy.deepcopy(compiled_fixture()["experiment_fixture"]["base_preview"])
        base["circuit_design"]["nets"].append("path_00")
        with self.assertRaisesRegex(ValueError, "aliases collide"):
            compose_experiment_fixture(base, fixture_contract())

    def test_native_ideal_model_template_is_checked_even_when_pins_match(self):
        import xml.etree.ElementTree as ET
        parts = [p for p in validated_components(compiled_fixture()["spice_netlist"]) if p.kind == "OPAMP5"]
        root = ET.Element("Root")
        for p in parts:
            component = ET.SubElement(root, "CiComponent", LocalName="&ASC" + p.refdes)
            expression = ET.SubElement(ET.SubElement(ET.SubElement(component, "Attributes"), "Item"), "CiaSpiceTmpltExprt")
            expression.set("String", "&ASCe%p %tOUT 0 %tIN+ %tIN- 1e5")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "saved.xml"
            ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
            self.assertEqual(set(ideal_model_fingerprints(path, parts)), {p.refdes for p in parts})
            expression.set("String", "&ASCe%p %tOUT 0 %tIN+ %tIN- 1e6")
            ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
            with self.assertRaisesRegex(ValueError, "model contract"):
                ideal_model_fingerprints(path, parts)

    def test_native_small_capacitance_cannot_pass_with_absolute_tolerance(self):
        from unittest.mock import Mock
        from multisim_mcp.schematic_builder import parse_netlist
        parts = parse_netlist("* small cap\nC1 n 0 1e-15\n.end\n").components
        client = Mock()
        client.get_rlc_value.return_value = {"value": 2e-15}
        with self.assertRaisesRegex(RuntimeError, "RLC readback"):
            _analyze_saved_values(client, {}, Path("unused"), parts=parts, strict_values=True)
