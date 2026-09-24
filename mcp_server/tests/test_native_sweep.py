from __future__ import annotations

import unittest
import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from multisim_mcp import server
from multisim_mcp.design_patch_service import prepare_design_patch
from multisim_mcp.eda_core import CircuitComponent, CircuitDesign
from multisim_mcp.native_sweep import (
    prepare_native_sweep,
    prepare_native_sweep_patch,
    rank_native_sweep_results,
)


def _readiness() -> dict:
    payload = {
        "state": "ready-for-com-parameter-sweep",
        "design_id": "design-native-test",
        "design_revision": 3,
        "candidates": [
            {"refdes": "R1", "target": "R1.value", "value": "1000"},
            {"refdes": "C1", "target": "C1.value", "value": "1e-7"},
        ],
    }
    payload["readiness_digest"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    return payload


def _approval() -> dict:
    return {
        "approved": True,
        "runtime_gate": True,
        "restore_original_values": True,
        "review_note": "test",
    }


def _readiness_for(candidates: list[dict[str, str]]) -> dict:
    payload = _readiness()
    payload["candidates"] = candidates
    payload.pop("readiness_digest")
    payload["readiness_digest"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    return payload


class NativeSweepTest(unittest.TestCase):
    def test_expands_bounded_grid(self) -> None:
        combinations, refs = prepare_native_sweep(
            _readiness(),
            [{"refdes": "R1", "values": [1000, 2000]}, {"refdes": "C1", "values": [1e-7]}],
            _approval(),
        )
        self.assertEqual(refs, ["R1", "C1"])
        self.assertEqual(combinations, [{"R1": 1000.0, "C1": 1e-7}, {"R1": 2000.0, "C1": 1e-7}])

    def test_rejects_unapproved_or_oversized_grid(self) -> None:
        with self.assertRaisesRegex(ValueError, "approval.approved"):
            prepare_native_sweep(_readiness(), [{"refdes": "R1", "values": [1]}], {"approved": False})
        with self.assertRaisesRegex(ValueError, "maximum"):
            prepare_native_sweep(
                _readiness(),
                [{"refdes": "R1", "values": [1, 2, 3, 4, 5, 6, 7, 8]} , {"refdes": "C1", "values": [1, 2, 3, 4, 5, 6, 7, 8, 9]}],
                _approval(),
            )

    def test_rejects_tampered_readiness(self) -> None:
        tampered = _readiness()
        tampered["candidates"][0]["refdes"] = "R9"
        with self.assertRaisesRegex(ValueError, "readiness_digest"):
            prepare_native_sweep(tampered, [{"refdes": "R1", "values": [1]}], _approval())

    def test_ranks_results_by_target_distance(self) -> None:
        result = rank_native_sweep_results(
            {
                "state": "completed",
                "results": [
                    {"parameters": {"R1": 900.0}, "analysis": {"rows": [[0.7, 0.8, 0.9]]}},
                    {"parameters": {"R1": 1000.0}, "analysis": {"rows": [[1.0, 1.0, 1.0]]}},
                ],
            },
            {"signal": "V(out)", "metric": "mean", "direction": "target", "target": 1.0},
        )
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["best"]["index"], 1)
        self.assertRegex(result["ranking_digest"], r"^[0-9a-f]{64}$")
        self.assertEqual(result["quality_state"], "valid")

    def test_rejects_invalid_objective(self) -> None:
        with self.assertRaisesRegex(ValueError, "objective.metric"):
            rank_native_sweep_results(
                {"state": "completed", "results": [{"analysis": {"rows": [[1.0]]}}]},
                {"signal": "V(out)", "metric": "median", "direction": "minimize"},
            )

    def test_marks_degenerate_zero_outputs(self) -> None:
        result = rank_native_sweep_results(
            {
                "state": "completed",
                "results": [
                    {"parameters": {"R1": 900.0}, "analysis": {"rows": [[0.0]]}},
                    {"parameters": {"R1": 1000.0}, "analysis": {"rows": [[0.0]]}},
                ],
            },
            {"signal": "V(out)", "metric": "final", "direction": "maximize"},
        )
        self.assertEqual(result["quality_state"], "degenerate-output")
        self.assertEqual(result["quality_counts"]["degenerate_zero"], 2)
        self.assertTrue(result["warnings"])

    def test_prepares_standard_reversible_patch(self) -> None:
        ranking = rank_native_sweep_results(
            {
                "state": "completed",
                "original_values": {"R1": 1000.0, "C1": 1e-7},
                "results": [
                    {
                        "parameters": {"R1": 1200.0, "C1": 1e-7},
                        "analysis": {"rows": [[1.0]]},
                    }
                ],
            },
            {"signal": "V(out)", "metric": "final", "direction": "maximize"},
        )
        draft = prepare_native_sweep_patch(_readiness(), ranking)
        self.assertEqual(draft["state"], "ready-for-approval")
        self.assertEqual(draft["patch"]["design_id"], "design-native-test")
        self.assertEqual(draft["patch"]["base_revision"], 3)
        self.assertEqual(len(draft["patch"]["operations"]), 1)
        self.assertEqual(draft["patch"]["operations"][0]["target"], "R1.value")
        self.assertEqual(draft["patch"]["operations"][0]["before"], "1000")
        self.assertEqual(draft["patch"]["operations"][0]["after"], "1.2k")
        self.assertRegex(draft["draft_digest"], r"^[0-9a-f]{64}$")
        prepared = prepare_design_patch(
            CircuitDesign(
                design_id="design-native-test",
                title="Native test",
                revision=3,
                components=(
                    CircuitComponent("R1", "R", ("in", "out"), value="1000"),
                    CircuitComponent("C1", "C", ("out", "0"), value="1e-7"),
                ),
            ),
            draft["patch"],
        )
        self.assertEqual(prepared.candidate.components[0].value, "1.2k")
        self.assertEqual(prepared.inverse_patch.operations[0].after, "1000")

    def test_patch_rejects_stale_design_value(self) -> None:
        ranking = rank_native_sweep_results(
            {
                "state": "completed",
                "original_values": {"R1": 999.0},
                "results": [
                    {"parameters": {"R1": 1200.0}, "analysis": {"rows": [[1.0]]}}
                ],
            },
            {"signal": "V(out)", "metric": "final", "direction": "maximize"},
        )
        with self.assertRaisesRegex(ValueError, "measured COM value"):
            prepare_native_sweep_patch(_readiness(), ranking)

    def test_server_sweep_restores_original_values(self) -> None:
        fake = Mock()
        fake.circuit_info.return_value = {"name": "Native Test", "file": "C:/circuits/native.ms14"}
        fake.enum_components.return_value = ["R1"]
        fake.get_rlc_value.return_value = {"component": "R1", "value": 1000.0}
        fake.run_dc_operating_point.return_value = {"ready": True, "results": {"V(out)": {"rows": [[1.0]]}}}
        with patch.object(server, "client", fake):
            result = server.run_native_parameter_sweep(
                _readiness_for([{"refdes": "R1", "target": "R1.value"}]),
                [{"refdes": "R1", "values": [900.0, 1100.0]}],
                "V(out)",
                _approval(),
            )
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["combination_count"], 2)
        self.assertTrue(result["restored_original_values"])
        self.assertEqual(fake.set_rlc_value.call_args_list[-1].args, ("R1", 1000.0))
        self.assertFalse(result["source_mutated"])

    def test_native_failure_does_not_source_connectivity_table(self) -> None:
        for outcome in (RuntimeError("output not found"), {"ready": False, "timed_out": True}):
            with self.subTest(outcome=outcome):
                fake = Mock()
                fake.circuit_info.return_value = {"name": "Native Test"}
                fake.enum_components.return_value = ["R1"]
                fake.get_rlc_value.return_value = {"value": 1000.0}
                if isinstance(outcome, Exception):
                    fake.run_dc_operating_point.side_effect = outcome
                else:
                    fake.run_dc_operating_point.return_value = outcome
                with patch.object(server, "client", fake):
                    result = server.run_native_parameter_sweep(
                        _readiness_for([{"refdes": "R1", "target": "R1.value"}]),
                        [{"refdes": "R1", "values": [900.0]}], "V(out)", _approval())
                self.assertEqual(result["state"], "failed")
                self.assertEqual(fake.set_rlc_value.call_args_list[-1].args, ("R1", 1000.0))
                fake.run_command_file.assert_not_called()
                fake.report_netlist.assert_not_called()

    def test_applies_only_to_copy_and_reopens_source(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            source = f"{root}/source.ms14"
            destination = f"{root}/optimized.ms14"
            with open(source, "wb") as handle:
                handle.write(b"source-circuit")
            readiness = _readiness()
            ranking = rank_native_sweep_results(
                {
                    "state": "completed",
                    "original_values": {"R1": 1000.0},
                    "circuit": {"file": source},
                    "results": [
                        {"parameters": {"R1": 1200.0}, "analysis": {"rows": [[1.0]]}}
                    ],
                },
                {"signal": "V(out)", "metric": "final", "direction": "maximize"},
            )
            draft = prepare_native_sweep_patch(readiness, ranking)
            fake = Mock()
            fake.circuit_info.return_value = {"file": source}
            fake.get_rlc_value.return_value = {"value": 1000.0}
            fake.open_circuit.return_value = {"file": destination}
            fake.save_circuit.return_value = destination
            with patch.object(server, "client", fake):
                result = server.apply_native_sweep_patch_to_copy(
                    draft,
                    destination,
                    {
                        "approved": True,
                        "write_copy": True,
                        "reopen_source": True,
                        "preserve_source": True,
                        "source_saved": True,
                        "draft_digest": draft["draft_digest"],
                    },
                )
            self.assertEqual(result["state"], "completed")
            self.assertTrue(result["saved_copy"])
            self.assertTrue(result["reopened_source"])
            self.assertEqual(Path(source).read_bytes(), b"source-circuit")
            self.assertEqual(Path(destination).read_bytes(), b"source-circuit")
            self.assertEqual(fake.set_rlc_value.call_args.args, ("R1", 1200.0))
            self.assertEqual(Path(fake.open_circuit.call_args_list[-1].args[0]), Path(source).resolve())


if __name__ == "__main__":
    unittest.main()
