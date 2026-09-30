import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from multisim_mcp.natural_common_emitter import parse_natural_common_emitter
from multisim_mcp.natural_common_emitter_run import _candidate_proposal, run_natural_common_emitter
from multisim_mcp.preferred_values import parse_spice_scalar


class CommonEmitterTest(unittest.TestCase):
    def test_bounded_plan(self):
        plan = parse_natural_common_emitter("设计一个12V单电源、增益10倍的NPN共射放大器")
        self.assertEqual(plan["topology"], "single_npn_common_emitter")
        self.assertEqual(plan["derived"]["target_gain"], 10)
        self.assertGreaterEqual(len(plan["candidate_resistors_ohm"]), 1)
        self.assertLessEqual(len(plan["candidate_resistors_ohm"]), 5)
        self.assertTrue(all(value > 0 for value in plan["candidate_resistors_ohm"]))

    def test_candidate_changes_only_emitter_resistor(self):
        plan = parse_natural_common_emitter("设计一个12V单电源、增益10倍的NPN共射放大器")
        proposal = _candidate_proposal(plan, 560.0)
        self.assertIn("RE emitter 0 560", proposal["netlist"])
        self.assertIn("RC vcc collector", proposal["netlist"])
        self.assertEqual(proposal["netlist"].count("RE emitter 0"), 1)

    def test_rejects_power_stage(self):
        with self.assertRaises(ValueError):
            parse_natural_common_emitter("设计一个24V MOSFET功率放大器")

    def test_preview_lists_native_candidates_without_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "new"
            result = run_natural_common_emitter(
                "设计一个12V单电源、增益10倍的NPN共射放大器", str(root))
            self.assertEqual(result["mode"], "preview")
            self.assertFalse(root.exists())
            self.assertFalse(result["simulation_started"])
            self.assertEqual(
                len(result["candidates"]),
                len(result["natural_language_plan"]["candidate_resistors_ohm"]),
            )

    def test_execute_selects_lowest_measured_gain_error(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch("multisim_mcp.natural_common_emitter_run.run_generated_analog_project") as run, \
                patch("multisim_mcp.natural_common_emitter_run.voltage_source_stem",
                      side_effect=lambda part: {"VCC": "vdc", "VIN": "vpulse"}[part.refdes]), \
                patch("multisim_mcp.natural_common_emitter_run.verify_ce_presentation",
                      return_value={"ok": True, "checks": {}}):
            def fake_run(proposal, output, *, execute):
                Path(output).mkdir(parents=True)
                (Path(output) / "native-model.xml").write_text("<root />", encoding="utf-8")
                token = proposal["netlist"].split("RE emitter 0 ", 1)[1].splitlines()[0]
                resistance = float(parse_spice_scalar(token))
                gain = 10.0 + abs(resistance - 560.0) / 10000.0
                return {
                    "success": True,
                    "verification_status": "passed-declared-sampled-requirements",
                    "measurement_acceptance": {"checks": [{
                        "requirement": {"analysis": "ac", "quantity": "magnitude"},
                        "measured_max": gain,
                    }]},
                }

            run.side_effect = fake_run
            root = Path(tmp) / "new"
            result = run_natural_common_emitter(
                "设计一个12V单电源、增益10倍的NPN共射放大器", str(root), execute=True)
            self.assertTrue(result["success"])
            self.assertIsNotNone(result["selected"])
            self.assertEqual(result["verification_status"], "passed-common-emitter-candidate-search")
            self.assertTrue((root / "acceptance.json").is_file())
            persisted = json.loads((root / "acceptance.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["selected"]["measured_gain"], result["selected"]["measured_gain"])
