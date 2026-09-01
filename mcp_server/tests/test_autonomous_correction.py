from __future__ import annotations

import json
import tempfile
import threading
import unittest
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from multisim_mcp.autonomous_correction import (
    AutonomousDesignCorrectionService,
    ModelRepairPlanner,
    validate_autonomous_correction_spec,
)
from multisim_mcp.eda_core import CircuitDesign
from multisim_mcp.model_provider import (
    ModelMessage,
    ModelProviderRegistry,
    ModelResponse,
    ModelUsage,
    ToolCall,
    ToolDefinition,
)
from tests.test_design_patch_evaluation import _design, _patch, _service, _spec


def _correction_spec() -> dict[str, object]:
    verification = _spec()
    return {
        **verification,
        "max_rounds": 3,
        "max_candidates_per_round": 3,
        "require_strict_improvement": True,
        "stop_on_first_pass": False,
        "objectives": [
            {
                "requirement_id": "vout",
                "goal": "target",
                "target": 6.6666666667,
            }
        ],
    }


class _Planner:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(
        self,
        design: CircuitDesign,
        diagnosis: object,
        spec: object,
        history: object,
        round_number: int,
    ) -> list[dict[str, object]]:
        del diagnosis, spec, history, round_number
        self.calls += 1
        patches: list[dict[str, object]] = []
        for suffix, value in (("weak", "500"), ("passing", "2k")):
            patch = _patch(value)
            patch["patch_id"] = f"repair-{suffix}-{self.calls}"
            patch["design_id"] = design.design_id
            patch["base_revision"] = design.revision
            patch["operations"][0]["before"] = design.components[2].value  # type: ignore[index]
            patches.append(patch)
        return patches


class _ModelPlannerProvider:
    provider_id = "fixture"
    model = "fixture-model"

    def __init__(self) -> None:
        self.calls = 0

    def complete(
        self,
        messages: Sequence[ModelMessage],
        tools: Sequence[ToolDefinition] = (),
        **kwargs: Any,
    ) -> ModelResponse:
        del messages, kwargs
        self.calls += 1
        if self.calls == 1:
            self.assert_tool_names = {item.name for item in tools}
            message = ModelMessage(
                "assistant",
                "",
                tool_calls=(
                    ToolCall(
                        "repair_preview",
                        "eda_preview_design_patch",
                        {"patch": _patch("2k")},
                    ),
                ),
            )
            finish_reason = "tool_calls"
        else:
            message = ModelMessage("assistant", "Candidate submitted for simulation.")
            finish_reason = "stop"
        return ModelResponse(
            provider_id=self.provider_id,
            requested_model=self.model,
            model=self.model,
            message=message,
            finish_reason=finish_reason,
            usage=ModelUsage(10, 5, 15),
        )


class AutonomousCorrectionTest(unittest.TestCase):
    def test_resume_reuses_baseline_and_replans_only_the_interrupted_round(self) -> None:
        design = _design()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "correction"
            first_calls: list[str] = []
            first = AutonomousDesignCorrectionService(
                _service(interrupt_at=3, calls=first_calls), _Planner()
            ).run(design, _correction_spec(), str(output))
            self.assertEqual(first["status"], "cancelled")
            self.assertEqual(len(first_calls), 3)

            resumed_calls: list[str] = []
            resumed = AutonomousDesignCorrectionService(
                _service(calls=resumed_calls), _Planner()
            ).run(design, _correction_spec(), str(output), resume=True)
            self.assertTrue(resumed["success"])
            self.assertEqual(resumed["status"], "corrected")
            self.assertEqual(resumed["resume_count"], 1)
            self.assertEqual(resumed["experiments_attempted"], 5)
            self.assertEqual(len(resumed_calls), 2)
            self.assertTrue(all("attempt-002" in item for item in resumed_calls))
            state = json.loads(
                (output / "autonomous-correction.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(state["interrupted_rounds"]), 1)
            self.assertEqual(state["rounds"][0]["status"], "selected")

    def test_resume_rejects_tampered_baseline_and_changed_planner_contract(self) -> None:
        design = _design()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "correction"
            AutonomousDesignCorrectionService(
                _service(interrupt_at=3), _Planner()
            ).run(design, _correction_spec(), str(output))

            class _AnotherPlanner(_Planner):
                pass

            with self.assertRaisesRegex(ValueError, "runtime"):
                AutonomousDesignCorrectionService(
                    _service(), _AnotherPlanner()
                ).run(design, _correction_spec(), str(output), resume=True)

            verification = output / "baseline" / "experiment" / "verification.json"
            payload = json.loads(verification.read_text(encoding="utf-8"))
            payload["counts"]["fail"] = 999
            verification.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "evidence|verification|artifact"):
                AutonomousDesignCorrectionService(_service(), _Planner()).run(
                    design, _correction_spec(), str(output), resume=True
                )

    def test_model_planner_captures_only_validated_previews_and_secret_free_metadata(
        self,
    ) -> None:
        provider = _ModelPlannerProvider()
        registry = ModelProviderRegistry([provider], active_provider="fixture")
        planner = ModelRepairPlanner(registry)
        design = _design()
        proposals = planner(
            design,
            {"summary": {"status": "issues_found"}},
            validate_autonomous_correction_spec(_correction_spec(), design),
            (),
            1,
        )
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].operations[0].after, "2k")
        self.assertIn("eda_preview_design_patch", provider.assert_tool_names)
        metadata = planner.last_run_metadata()
        self.assertEqual(metadata["captured_candidate_count"], 1)
        self.assertEqual(metadata["provider_ids"], ["fixture", "fixture"])
        self.assertFalse(metadata["transcript_persisted"])
        self.assertFalse(metadata["credential_values_persisted"])

    def test_model_independent_loop_selects_verified_repair_without_persisting(self) -> None:
        design = _design()
        before = design.to_dict()
        planner = _Planner()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "correction"
            result = AutonomousDesignCorrectionService(_service(), planner).run(
                design, _correction_spec(), str(output)
            )
            self.assertTrue(result["success"])
            self.assertEqual(result["status"], "corrected")
            self.assertEqual(result["stop_reason"], "requirements_passed")
            self.assertEqual(result["experiments_attempted"], 3)
            self.assertTrue(result["adoption_eligible"])
            self.assertTrue(result["approval_required_before_apply"])
            self.assertEqual(planner.calls, 1)
            self.assertEqual(design.to_dict(), before)
            final_patch = json.loads(
                Path(result["final_patch_path"]).read_text(encoding="utf-8")
            )
            self.assertEqual(final_patch["base_revision"], design.revision)
            self.assertEqual(final_patch["operations"][0]["operation"], "replace_component")
            self.assertEqual(final_patch["operations"][0]["after"]["value"], "2k")
            state = json.loads(
                (output / "autonomous-correction.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                state["rounds"][0]["selected_candidate_id"],
                "round-001-candidate-002",
            )

    def test_no_proposals_stops_safely_with_original_unchanged(self) -> None:
        def planner(*args: object) -> list[object]:
            del args
            return []

        design = _design()
        with tempfile.TemporaryDirectory() as tmp:
            result = AutonomousDesignCorrectionService(_service(), planner).run(
                design, _correction_spec(), str(Path(tmp) / "correction")
            )
            self.assertFalse(result["success"])
            self.assertEqual(result["status"], "not_corrected")
            self.assertEqual(result["stop_reason"], "planner_returned_no_candidates")
            self.assertIsNone(result["final_patch_path"])
            self.assertFalse(result["source_design_modified"])

    def test_validation_rejects_unbounded_rounds(self) -> None:
        spec = _correction_spec()
        spec["max_rounds"] = 9
        with self.assertRaisesRegex(ValueError, "max_rounds"):
            validate_autonomous_correction_spec(spec, _design())


if __name__ == "__main__":
    unittest.main()
