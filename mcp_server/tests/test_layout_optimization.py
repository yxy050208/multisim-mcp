from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from multisim_mcp.layout_optimization import (
    generate_local_layout_candidates,
    optimize_single_board_layout,
    rank_layout_candidates,
    rank_electrical_layout_candidates,
    rank_optimization_directory_candidates,
    score_layout_result,
)


class LayoutOptimizationTest(unittest.TestCase):
    def test_candidate_generation_is_bounded_and_deterministic(self) -> None:
        kwargs = {"step": 270, "moves": 2, "max_candidates": 6, "fixed_refdes": ["GND"]}
        first = generate_local_layout_candidates({"R1": [0, 0], "U1": [270, 0], "GND": [0, 270]}, **kwargs)
        second = generate_local_layout_candidates({"R1": [0, 0], "U1": [270, 0], "GND": [0, 270]}, **kwargs)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 6)
        self.assertEqual(first[0]["candidate_id"], "baseline")
        self.assertEqual(first[1]["candidate_id"], "R1:left:1")
        self.assertNotIn("GND", first[1]["candidate_id"])

    def test_ground_is_fixed_by_default(self) -> None:
        candidates = generate_local_layout_candidates({"R1": [0, 0], "0": [270, 0]}, max_candidates=4)
        self.assertTrue(all(not item["candidate_id"].startswith("0:") for item in candidates))

    def test_candidate_generation_accepts_validate_output_placement_shape(self) -> None:
        candidates = generate_local_layout_candidates(
            {"R1": {"x": 10, "y": 20}, "U1": {"x": 30, "y": 40}},
            max_candidates=1,
        )
        self.assertEqual(candidates[0]["positions"], {"R1": [10.0, 20.0], "U1": [30.0, 40.0]})

    def test_score_prefers_clean_pass_over_crossings_fallbacks_and_complexity(self) -> None:
        clean = score_layout_result({"layout_validation": {"status": "pass", "crossings_per_wire": 0.8}, "wire_fallbacks": 0, "wire_length": 100, "wire_bends": 2, "placement_span": 100})
        fallback = score_layout_result({"layout_validation": {"status": "pass", "crossings_per_wire": 0.1}, "wire_fallbacks": 1, "wire_length": 10, "wire_bends": 0, "placement_span": 10})
        long = score_layout_result({"layout_validation": {"status": "pass", "crossings_per_wire": 0.8}, "wire_fallbacks": 0, "wire_length": 200, "wire_bends": 4, "placement_span": 200})
        failed = score_layout_result({"layout_validation": {"status": "fail", "crossings_per_wire": 0.0, "findings": [{"severity": "error"}]}})
        functional_failure = score_layout_result({"layout_validation": {"status": "pass", "crossings_per_wire": 0.0}, "functional_status": "target-not-met"})
        nested_failure = score_layout_result({"layout_validation": {"status": "pass", "crossings_per_wire": 0.0}, "verification": {"overall_status": "unverified"}})
        passed_contract = score_layout_result({"layout_validation": {"status": "pass", "crossings_per_wire": 0.0}, "verification_status": "passed-supported-rc-contract"})
        self.assertTrue(clean["hard_pass"])
        self.assertLess(clean["score"], fallback["score"])
        self.assertFalse(fallback["hard_pass"])
        self.assertLess(clean["score"], long["score"])
        self.assertFalse(failed["hard_pass"])
        self.assertFalse(functional_failure["hard_pass"])
        self.assertFalse(nested_failure["hard_pass"])
        self.assertTrue(passed_contract["hard_pass"])

    def test_rank_requires_a_feasible_layout_and_breaks_ties_by_id(self) -> None:
        result = rank_layout_candidates([
            {"candidate_id": "z", "positions": {}, "result": {"layout_validation": {"status": "pass", "crossings_per_wire": 0.0}}},
            {"candidate_id": "a", "positions": {}, "result": {"layout_validation": {"status": "pass", "crossings_per_wire": 0.0}}},
        ])
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["selected"]["candidate_id"], "a")

        failed = rank_layout_candidates([
            {"candidate_id": "only", "positions": {}, "result": {"layout_validation": {"status": "fail", "findings": [{"severity": "error"}]}}},
        ])
        self.assertEqual(failed["status"], "no-feasible-layout")
        self.assertIsNone(failed["selected"])

    def test_rank_uses_normalized_electrical_score_before_geometry(self) -> None:
        result = rank_layout_candidates([
            {
                "candidate_id": "electrically-worse",
                "positions": {},
                "result": {
                    "layout_validation": {"status": "pass", "crossings_per_wire": 0.0},
                    "electrical_score": 2.0,
                },
            },
            {
                "candidate_id": "electrically-better",
                "positions": {},
                "result": {
                    "layout_validation": {"status": "pass", "crossings_per_wire": 1.0},
                    "electrical_score": 1.0,
                },
            },
        ])
        self.assertEqual(result["selected"]["candidate_id"], "electrically-better")

    def test_electrical_layout_adapter_fails_closed_on_electrical_status(self) -> None:
        result = rank_electrical_layout_candidates([
            {
                "candidate_id": "electrical-pass",
                "positions": {},
                "electrical_status": "pass",
                "electrical_score": 2.0,
                "layout_result": {"layout_validation": {"status": "pass", "crossings_per_wire": 0.0}},
            },
            {
                "candidate_id": "electrical-fail",
                "positions": {},
                "electrical_status": "target-not-met",
                "electrical_score": 0.1,
                "layout_result": {"layout_validation": {"status": "pass", "crossings_per_wire": 0.0}},
            },
        ])
        self.assertEqual(result["selected"]["candidate_id"], "electrical-pass")
        self.assertFalse(next(item for item in result["candidates"] if item["candidate_id"] == "electrical-fail")["quality"]["hard_pass"])

    def test_optimization_directory_bridge_reads_electrical_candidates(self) -> None:
        import json

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "optimization-state.json").write_text(json.dumps({
                "evaluations": [
                    {"evaluation_id": "candidate-a", "status": "feasible", "objective": {"score": 1.0}},
                    {"evaluation_id": "candidate-b", "status": "feasible", "objective": {"score": 2.0}},
                    {"evaluation_id": "candidate-fail", "status": "error", "objective": {"score": 0.0}},
                ]
            }), encoding="utf-8")
            layout = {
                key: {
                    "state": "materialized",
                    "positions": {"R1": [0, 0]},
                    "layout_validation": {"status": "pass", "crossings_per_wire": 0.0},
                }
                for key in ("candidate-a", "candidate-b")
            }
            result = rank_optimization_directory_candidates(root, layout)
        self.assertEqual(result["selected"]["candidate_id"], "candidate-a")
        self.assertEqual(result["electrical_candidate_count"], 3)
        failed = next(item for item in result["candidates"] if item["candidate_id"] == "candidate-fail")
        self.assertFalse(failed["quality"]["hard_pass"])

    def test_optimizer_preserves_evaluator_failures_and_selects_pass(self) -> None:
        def evaluate(positions):
            if positions["R1"][0] < 0:
                raise RuntimeError("blocked corridor")
            return {"layout_validation": {"status": "pass", "crossings_per_wire": 0.0}, "wire_fallbacks": 0}

        result = optimize_single_board_layout({"R1": [0, 0]}, evaluate, max_candidates=3)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["selected"]["candidate_id"], "baseline")
        self.assertEqual(result["evaluated_count"], 3)
        self.assertEqual(result["candidates"][-1]["quality"]["status"], "fail")

    def test_component_placement_wrapper_uses_the_real_dry_run_entrypoint(self) -> None:
        from multisim_mcp.component_placement import optimize_layout_positions

        with patch("multisim_mcp.component_placement.validate_layout_positions") as validate:
            validate.return_value = {
                "status": "pass",
                "crossings_per_wire": 0.0,
                "wire_fallbacks": 0,
            }
            result = optimize_layout_positions("V1 in 0 DC 1\n.end", {"R1": [0, 0]}, max_candidates=2)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(validate.call_count, 2)

    def test_component_placement_optimizer_propagates_probe_nets(self) -> None:
        from multisim_mcp.component_placement import optimize_layout_positions

        with patch("multisim_mcp.component_placement.validate_layout_positions") as validate:
            validate.return_value = {"status": "pass", "crossings_per_wire": 0.0, "wire_fallbacks": 0}
            optimize_layout_positions(
                "V1 in 0 DC 1\n.end", {"R1": [0, 0]}, probe_nets=["out"], max_candidates=1
            )
        self.assertEqual(validate.call_args.kwargs["probe_nets"], ["out"])

    def test_dry_run_exposes_length_and_bend_metrics(self) -> None:
        from multisim_mcp.component_placement import validate_layout_positions

        with patch("multisim_mcp.schematic_builder.build_schematic") as build:
            build.return_value = {
                "layout_validation": {"status": "pass", "crossings_per_wire": 0.0},
                "geometry": {
                    "placements": [],
                    "wires": {"n": [[(0, 0), (10, 0), (10, 5)]]},
                },
                "sheet": {},
            }
            result = validate_layout_positions("V1 in 0 DC 1\n.end", {})
        self.assertEqual(result["wire_length"], 15.0)
        self.assertEqual(result["wire_segments"], 2)
        self.assertEqual(result["wire_bends"], 1)
        self.assertEqual(result["placement_span"], 0.0)

    def test_mcp_validate_layout_can_opt_in_to_bounded_search(self) -> None:
        from multisim_mcp import server

        with patch("multisim_mcp.server.component_placement.optimize_layout_positions") as optimize:
            optimize.return_value = {"status": "pass", "selected": {"candidate_id": "baseline"}}
            result = server.validate_layout_positions(
                "V1 in 0 DC 1\n.end",
                positions={"R1": [0, 0]},
                optimize=True,
                max_candidates=4,
                fixed_refdes=["R1"],
            )
        self.assertEqual(result["selected"]["candidate_id"], "baseline")
        optimize.assert_called_once()
        self.assertEqual(optimize.call_args.kwargs["probe_nets"], None)

    def test_apply_selected_candidate_writes_and_reads_back_coordinates(self) -> None:
        from multisim_mcp.component_placement import apply_optimized_layout_candidate

        optimization = {
            "status": "pass",
            "selected": {
                "candidate_id": "R1:right:1",
                "positions": {"R1": [100, 200], "U1": {"x": 300, "y": 400}},
            },
        }
        with patch("multisim_mcp.component_placement.set_component_positions") as apply:
            with patch("multisim_mcp.component_placement.list_component_positions") as readback:
                apply.return_value = {"success": True, "ms14": "C:/tmp/optimized.ms14"}
                readback.return_value = {
                    "components": {
                        "R1": {"x": 100.0, "y": 200.0},
                        "U1": {"x": 300.0, "y": 400.0},
                    }
                }
                result = apply_optimized_layout_candidate(
                    "C:/tmp/source.ms14", optimization, output_ms14="C:/tmp/optimized.ms14"
                )
        self.assertTrue(result["success"])
        self.assertEqual(result["state"], "applied")
        self.assertTrue(result["readback_match"])
        apply.assert_called_once()
        self.assertEqual(apply.call_args.args[1], {"R1": [100.0, 200.0], "U1": [300.0, 400.0]})

    def test_apply_rejects_fallback_wire_paths_even_when_coordinates_read_back(self) -> None:
        from multisim_mcp.component_placement import apply_optimized_layout_candidate

        optimization = {
            "status": "pass",
            "selected": {"candidate_id": "baseline", "positions": {"R1": [100, 200]}},
        }
        with patch("multisim_mcp.component_placement.set_component_positions") as apply:
            with patch("multisim_mcp.component_placement.list_component_positions") as readback:
                apply.return_value = {
                    "success": True,
                    "ms14": "C:/tmp/unsafe.ms14",
                    "fallback_shifted_wires": 1,
                    "overlaps_after": [],
                }
                readback.return_value = {"components": {"R1": {"x": 100, "y": 200}}}
                result = apply_optimized_layout_candidate("C:/tmp/source.ms14", optimization)
        self.assertFalse(result["success"])
        self.assertEqual(result["state"], "rejected-geometry")
        self.assertEqual(result["geometry_blockers"][0]["code"], "wire-fallback")

    def test_materialize_regenerates_from_netlist_with_probe_contract(self) -> None:
        from multisim_mcp.component_placement import materialize_layout_positions

        class Codec:
            def encode(self, source_xml, output_ms14):
                Path(output_ms14).write_bytes(b"encoded-ms14")
                return {"ms14": output_ms14}

        optimization = {
            "status": "pass",
            "selected": {
                "candidate_id": "baseline",
                "positions": {"R1": [100, 200]},
                "quality": {"hard_pass": True},
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "optimized.ms14"
            with patch("multisim_mcp.schematic_builder.build_schematic") as build:
                build.return_value = {"layout_validation": {"status": "pass", "findings": []}}
                result = materialize_layout_positions(
                    "V1 in 0 DC 1\n.end\n",
                    optimization,
                    output,
                    probe_nets=["out"],
                    include_experimental_probes=True,
                    codec=Codec(),
                )
                output_exists = output.is_file()
        self.assertTrue(result["success"])
        self.assertEqual(result["state"], "materialized")
        self.assertTrue(output_exists)
        self.assertEqual(build.call_args.kwargs["probe_nets"], ["out"])

    def test_mcp_can_materialize_only_after_opt_in(self) -> None:
        from multisim_mcp import server

        optimization = {
            "status": "pass",
            "selected": {
                "candidate_id": "baseline",
                "positions": {"R1": [0, 0]},
                "quality": {"hard_pass": True},
            },
        }
        with patch("multisim_mcp.server.component_placement.optimize_layout_positions", return_value=optimization):
            with patch(
                "multisim_mcp.server.component_placement.materialize_layout_positions",
                return_value={"success": True, "state": "materialized"},
            ) as materialize:
                result = server.validate_layout_positions(
                    "V1 in 0 DC 1\n.end",
                    positions={"R1": [0, 0]},
                    optimize=True,
                    materialize=True,
                    output_ms14="C:/tmp/materialized.ms14",
                    probe_nets=["out"],
                    include_experimental_probes=True,
                )
        self.assertEqual(result["materialized"]["state"], "materialized")
        materialize.assert_called_once()


if __name__ == "__main__":
    unittest.main()
