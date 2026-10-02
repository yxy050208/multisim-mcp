import json
import math
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from multisim_mcp.natural_common_emitter import parse_natural_common_emitter
from multisim_mcp.natural_common_emitter_run import (
    _candidate_proposal,
    _distortion_acceptance,
    _frequency_response_acceptance,
    _run_amplitude_scan,
    _sine_amplitude_proposal,
    run_natural_common_emitter,
)
from multisim_mcp.linear_reference import validate_native_source
from multisim_mcp.schematic_builder import parse_netlist, voltage_pin_order
from multisim_mcp.preferred_values import parse_spice_scalar


class CommonEmitterTest(unittest.TestCase):
    def test_bounded_plan(self):
        plan = parse_natural_common_emitter("设计一个12V单电源、增益10倍的NPN共射放大器")
        self.assertEqual(plan["topology"], "single_npn_common_emitter")
        self.assertEqual(plan["derived"]["target_gain"], 10)
        self.assertGreaterEqual(len(plan["candidate_resistors_ohm"]), 1)
        self.assertLessEqual(len(plan["candidate_resistors_ohm"]), 5)
        self.assertTrue(all(value > 0 for value in plan["candidate_resistors_ohm"]))

    def test_sine_request_enables_native_thd_mode(self):
        plan = parse_natural_common_emitter(
            "设计一个12V单电源、增益10倍的NPN共射放大器，正弦输入，THD不超过1%"
        )
        self.assertEqual(plan["waveform"], "sine")
        self.assertEqual(plan["thd_limit_percent"], 1.0)
        self.assertIn("SIN(0 1m 1k)", plan["proposal"]["netlist"])
        self.assertEqual(plan["proposal"]["experiments"][-1]["commands"], "tran 10u 3m")

    def test_sine_amplitude_proposal_changes_source_and_keeps_checks_broad(self):
        plan = parse_natural_common_emitter(
            "设计一个12V单电源、增益10倍的NPN共射放大器，正弦输入，THD不超过1%"
        )
        proposal = _sine_amplitude_proposal(plan["proposal"], 0.05)
        self.assertIn("SIN(0 50m 1k)", proposal["netlist"])
        tran_checks = [item for item in proposal["checks"] if item["analysis"] == "tran"]
        self.assertAlmostEqual(tran_checks[0]["min"], -0.055)
        self.assertAlmostEqual(tran_checks[1]["max"], 2.5)

    def test_amplitude_scan_refines_first_thd_failure_with_native_midpoints(self):
        plan = parse_natural_common_emitter(
            "设计一个12V单电源、增益10倍的NPN共射放大器，正弦输入，THD不超过1%"
        )

        def fake_native(_proposal, _output, *, execute):
            self.assertTrue(execute)
            return {"success": True, "verification_status": "passed"}

        def fake_distortion(_directory, scan_plan):
            source = scan_plan["proposal"]["netlist"]
            token = re.search(r"SIN\(0\s+(\S+)\s+1k", source, re.I).group(1)
            amplitude = float(parse_spice_scalar(token))
            passed = amplitude <= 0.3
            return {
                "status": "passed-thd" if passed else "target-not-met",
                "peak_to_peak_v": amplitude * 10,
                "thd_percent": 0.1 if passed else 1.1,
            }

        with tempfile.TemporaryDirectory() as tmp, \
                patch("multisim_mcp.natural_common_emitter_run.run_generated_analog_project",
                      side_effect=fake_native), \
                patch("multisim_mcp.natural_common_emitter_run._distortion_acceptance",
                      side_effect=fake_distortion):
            result = _run_amplitude_scan(
                Path(tmp) / "result", plan, {"resistance_ohm": 560.0})

        self.assertEqual(result["status"], "passed-amplitude-scan")
        self.assertEqual(result["refinement_iterations"], 4)
        self.assertEqual(len(result["points"]), 12)
        bracket = result["refined_bracket_input_peak_v"]
        self.assertLessEqual(bracket["passed"], 0.3)
        self.assertGreater(bracket["exceeded"], 0.3)
        self.assertLess(bracket["width_v"], 0.02)
        self.assertEqual(result["max_undistorted_input_peak_v"], bracket["passed"])

    def test_candidate_changes_only_emitter_resistor(self):
        plan = parse_natural_common_emitter("设计一个12V单电源、增益10倍的NPN共射放大器")
        proposal = _candidate_proposal(plan, 560.0)
        self.assertIn("RE emitter 0 560", proposal["netlist"])
        self.assertIn("RC vcc collector", proposal["netlist"])
        self.assertEqual(proposal["netlist"].count("RE emitter 0"), 1)

    def test_frequency_response_reports_unverified_when_edges_are_outside_sweep(self):
        plan = parse_natural_common_emitter("设计一个12V单电源、增益10倍的NPN共射放大器")
        with tempfile.TemporaryDirectory() as tmp:
            native = Path(tmp) / "native" / "analysis-002"
            native.mkdir(parents=True)
            path = native / "data.csv"
            path.write_text(
                "frequency_hz,V(OutProbe).real,V(OutProbe).imaginary,"
                "V(OutProbe1).real,V(OutProbe1).imaginary\n"
                "10,1,0,9,0\n100,1,0,9.5,0\n"
                "1000,1,0,10,0\n10000,1,0,9.5,0\n100000,1,0,9,0\n",
                encoding="utf-8",
            )
            response = _frequency_response_acceptance(Path(tmp), plan)
            self.assertEqual(response["status"], "unverified")
            self.assertEqual(response["samples"], 5)
            self.assertIsNone(response["lower_cutoff_hz"])
            self.assertIsNone(response["upper_cutoff_hz"])

    def test_frequency_response_interpolates_both_minus_three_db_edges(self):
        plan = parse_natural_common_emitter("设计一个12V单电源、增益10倍的NPN共射放大器")
        with tempfile.TemporaryDirectory() as tmp:
            native = Path(tmp) / "native" / "analysis-002"
            native.mkdir(parents=True)
            (native / "data.csv").write_text(
                "frequency_hz,V(OutProbe).real,V(OutProbe).imaginary,"
                "V(OutProbe1).real,V(OutProbe1).imaginary\n"
                "10,1,0,1,0\n100,1,0,8,0\n"
                "1000,1,0,10,0\n10000,1,0,8,0\n100000,1,0,1,0\n",
                encoding="utf-8",
            )
            response = _frequency_response_acceptance(Path(tmp), plan)
            self.assertEqual(response["status"], "passed-sweep-minus3db")
            self.assertGreater(response["lower_cutoff_hz"], 10)
            self.assertLess(response["lower_cutoff_hz"], 100)
            self.assertGreater(response["upper_cutoff_hz"], 10000)
            self.assertLess(response["upper_cutoff_hz"], 100000)
            self.assertGreater(response["bandwidth_hz"], 0)

    def test_distortion_acceptance_measures_sine_window(self):
        plan = parse_natural_common_emitter(
            "设计一个12V单电源、增益10倍的NPN共射放大器，正弦输入，THD不超过1%"
        )
        with tempfile.TemporaryDirectory() as tmp:
            native = Path(tmp) / "native" / "analysis-003"
            native.mkdir(parents=True)
            lines = ["time_s,V(OutProbe1).value"]
            for index in range(32):
                time = .001 + index * (.001 / 31)
                value = .01 * math.sin(2 * math.pi * 1000 * (time - .001))
                lines.append(f"{time},{value}")
            (native / "data.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
            result = _distortion_acceptance(Path(tmp), plan)
            self.assertEqual(result["status"], "passed-thd")
            self.assertLess(result["thd_percent"], 1.0)

    def test_rejects_power_stage(self):
        with self.assertRaises(ValueError):
            parse_natural_common_emitter("设计一个24V MOSFET功率放大器")

    def test_sine_source_can_carry_ac_small_signal_metadata(self):
        parsed = parse_netlist("V1 in 0 DC 0 AC 1 SIN(0 1m 1k)\nR1 in 0 1k\n.end\n")
        source = next(part for part in parsed.components if part.refdes == "V1")
        validate_native_source(source)
        self.assertEqual(voltage_pin_order(source), [1, 2])

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
