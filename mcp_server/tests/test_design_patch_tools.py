"""Tests for in-memory, non-persistent DesignPatch previews."""

from __future__ import annotations

import threading
import unittest
from collections.abc import Mapping, Sequence

from multisim_mcp.agent_runtime import BoundedToolLoop, ToolBinding
from multisim_mcp.design_patch_tools import ReadOnlyDesignPatchPreview
from multisim_mcp.eda_core import CircuitComponent, CircuitDesign
from multisim_mcp.model_provider import (
    ModelCancelled,
    ModelMessage,
    ModelProviderRegistry,
    ModelResponse,
    ToolCall,
    ToolDefinition,
)


def _design() -> CircuitDesign:
    return CircuitDesign(
        design_id="filter-v1",
        title="RC low-pass",
        revision=3,
        components=(
            CircuitComponent("R1", "R", ("in", "out"), value="1030"),
            CircuitComponent("C1", "C", ("out", "0"), value="10n"),
        ),
        parameters={"corner_hz": 15451.0},
        annotations={"review.status": "baseline"},
        source_netlist="R1 in out 1030\nC1 out 0 10n\n.end\n",
    )


def _patch() -> dict[str, object]:
    return {
        "schema_version": 1,
        "patch_id": "patch-e24-r1",
        "design_id": "filter-v1",
        "base_revision": 3,
        "description": "Move R1 to an E24 value and mark it for review",
        "operations": [
            {
                "operation": "set_component_value",
                "target": "R1.value",
                "before": "1030",
                "after": "1k",
                "reason": "Use the nearest available E24 value",
            },
            {
                "operation": "set_parameter",
                "target": "corner_hz",
                "before": 15451.0,
                "after": 15915.49,
                "reason": "Keep the declared target aligned with the candidate",
            },
            {
                "operation": "set_annotation",
                "target": "optimization.status",
                "before": None,
                "after": "candidate",
                "reason": "Record that this proposal still requires approval",
            },
        ],
        "metadata": {"source": "model-proposal"},
    }


def _binding(preview: ReadOnlyDesignPatchPreview) -> ToolBinding:
    bindings = preview.bindings()
    assert len(bindings) == 1
    return bindings[0]


def _invoke(
    binding: ToolBinding,
    arguments: Mapping[str, object],
    cancel_event: threading.Event | None = None,
) -> object:
    validated = binding.validate_arguments(arguments)
    return binding.handler(validated, cancel_event)


class ReadOnlyDesignPatchPreviewTest(unittest.TestCase):
    def test_exposes_one_explicit_non_persistent_preview_tool(self) -> None:
        binding = _binding(ReadOnlyDesignPatchPreview(_design()))
        self.assertEqual(binding.definition.name, "eda_preview_design_patch")
        self.assertFalse(binding.definition.parameters["additionalProperties"])
        patch_schema = binding.definition.parameters["properties"]["patch"]
        self.assertEqual(patch_schema["properties"]["operations"]["maxItems"], 64)

    def test_topology_patch_adds_net_and_component_and_is_reversible(self) -> None:
        design = _design()
        component = {
            "refdes": "R2",
            "kind": "R",
            "nodes": ["out", "sense"],
            "value": "10k",
            "model": None,
            "parameters": {},
            "annotations": {},
        }
        patch = {
            "schema_version": 1,
            "patch_id": "add-sense-branch",
            "design_id": design.design_id,
            "base_revision": design.revision,
            "description": "Add a reviewable sensing branch",
            "operations": [
                {
                    "operation": "add_net",
                    "target": "sense",
                    "before": None,
                    "after": "sense",
                    "reason": "Create the sensing node before attaching R2",
                },
                {
                    "operation": "add_component",
                    "target": "R2",
                    "before": None,
                    "after": component,
                    "reason": "Add the proposed sensing branch",
                },
            ],
            "metadata": {"source": "topology-search"},
        }
        result = _invoke(
            _binding(ReadOnlyDesignPatchPreview(design)), {"patch": patch}
        )
        self.assertTrue(result["structural_delta"]["topology_changed"])
        self.assertEqual(result["candidate"]["component_count"], 3)
        inverse = result["inverse_patch"]["operations"]
        self.assertEqual(inverse[0]["operation"], "remove_component")
        self.assertEqual(inverse[1]["operation"], "remove_net")
        self.assertEqual(inverse[0]["before"]["refdes"], "R2")

    def test_valid_patch_returns_inverse_and_keeps_original_unchanged(self) -> None:
        design = _design()
        preview = ReadOnlyDesignPatchPreview(design)
        result = _invoke(_binding(preview), {"patch": _patch()})

        self.assertTrue(result["preview_valid"])
        self.assertEqual(result["candidate"]["candidate_revision"], 4)
        self.assertTrue(result["candidate"]["source_netlist_update_required"])
        self.assertFalse(result["candidate"]["source_netlist_consistent"])
        self.assertEqual(result["changes"][0]["after"], "1k")
        inverse = result["inverse_patch"]
        self.assertEqual(inverse["base_revision"], 4)
        self.assertEqual(inverse["operations"][0]["target"], "optimization.status")
        self.assertEqual(inverse["operations"][-1]["after"], "1030")
        self.assertTrue(result["original_design_unchanged"])
        self.assertFalse(result["persisted"])
        self.assertFalse(result["backend_called"])
        self.assertFalse(result["electrical_correctness_proven"])
        self.assertTrue(result["approval_required_before_apply"])
        self.assertEqual(design.revision, 3)
        self.assertEqual(design.components[0].value, "1030")
        self.assertNotIn("optimization.status", design.annotations)

        captured = preview.captured_previews()
        self.assertEqual(len(captured), 1)
        captured[0]["changes"][0]["after"] = "tampered"
        self.assertEqual(
            preview.captured_previews()[0]["changes"][0]["after"], "1k"
        )

    def test_annotation_only_patch_does_not_stale_source_netlist(self) -> None:
        patch = _patch()
        patch["operations"] = [
            {
                "operation": "set_annotation",
                "target": "review.status",
                "before": "baseline",
                "after": "candidate",
                "reason": "Move the review state without changing the circuit",
            }
        ]
        result = _invoke(
            _binding(ReadOnlyDesignPatchPreview(_design())), {"patch": patch}
        )
        self.assertFalse(result["candidate"]["source_netlist_update_required"])
        self.assertTrue(result["candidate"]["source_netlist_consistent"])

    def test_rejects_stale_wrong_duplicate_or_broad_patch(self) -> None:
        preview = ReadOnlyDesignPatchPreview(_design())
        binding = _binding(preview)

        wrong_design = _patch()
        wrong_design["design_id"] = "another-design"
        with self.assertRaisesRegex(ValueError, "design_id"):
            binding.validate_arguments({"patch": wrong_design})

        stale = _patch()
        stale["base_revision"] = 2
        with self.assertRaisesRegex(ValueError, "base_revision"):
            binding.validate_arguments({"patch": stale})

        wrong_before = _patch()
        wrong_before["operations"][0]["before"] = "999"
        with self.assertRaisesRegex(ValueError, "before value"):
            binding.validate_arguments({"patch": wrong_before})

        duplicate = _patch()
        duplicate["operations"].append(
            {
                "operation": "set_component_value",
                "target": "r1.value",
                "before": "1030",
                "after": "1.1k",
                "reason": "A conflicting second edit",
            }
        )
        with self.assertRaisesRegex(ValueError, "duplicate targets"):
            binding.validate_arguments({"patch": duplicate})

        broad = _patch()
        broad["operations"][0]["operation"] = "replace_topology"
        with self.assertRaisesRegex(ValueError, "unsupported patch"):
            binding.validate_arguments({"patch": broad})

        missing_component = _patch()
        missing_component["operations"][0]["target"] = "R99.value"
        with self.assertRaisesRegex(ValueError, "does not exist"):
            binding.validate_arguments({"patch": missing_component})

    def test_rejects_unknown_arguments_and_honors_cancellation(self) -> None:
        binding = _binding(ReadOnlyDesignPatchPreview(_design()))
        with self.assertRaisesRegex(ValueError, "unknown arguments"):
            binding.validate_arguments({"patch": _patch(), "apply": True})
        validated = binding.validate_arguments({"patch": _patch()})
        event = threading.Event()
        event.set()
        with self.assertRaises(ModelCancelled):
            binding.handler(validated, event)

    def test_rejects_ambiguous_null_map_target_to_preserve_inverse(self) -> None:
        design = CircuitDesign(
            design_id="nullable-design",
            title="Nullable annotation",
            components=(
                CircuitComponent("R1", "R", ("in", "0"), value="1k"),
            ),
            annotations={"review.note": None},
        )
        patch = {
            "schema_version": 1,
            "patch_id": "nullable-annotation",
            "design_id": "nullable-design",
            "base_revision": 0,
            "description": "Attempt to replace an ambiguous null value",
            "operations": [
                {
                    "operation": "set_annotation",
                    "target": "review.note",
                    "before": None,
                    "after": "reviewed",
                    "reason": "Exercise inverse semantics",
                }
            ],
            "metadata": {},
        }
        binding = _binding(ReadOnlyDesignPatchPreview(design))
        with self.assertRaisesRegex(ValueError, "cannot be patched reversibly"):
            binding.validate_arguments({"patch": patch})


class _PatchProvider:
    provider_id = "fixture"
    model = "fixture-model"

    def __init__(self) -> None:
        self.calls: list[tuple[ModelMessage, ...]] = []

    def complete(
        self,
        messages: Sequence[ModelMessage],
        tools: Sequence[ToolDefinition] = (),
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        cancel_event: threading.Event | None = None,
        timeout: float = 60.0,
    ) -> ModelResponse:
        del tools, max_tokens, temperature, cancel_event, timeout
        self.calls.append(tuple(messages))
        if len(self.calls) == 1:
            message = ModelMessage(
                "assistant",
                "",
                tool_calls=(
                    ToolCall(
                        "call_patch",
                        "eda_preview_design_patch",
                        {"patch": _patch()},
                    ),
                ),
            )
            finish_reason = "tool_calls"
        else:
            message = ModelMessage("assistant", "The patch remains a preview.")
            finish_reason = "stop"
        return ModelResponse(
            provider_id=self.provider_id,
            requested_model=self.model,
            model=self.model,
            message=message,
            finish_reason=finish_reason,
        )


class DesignPatchLoopIntegrationTest(unittest.TestCase):
    def test_bounded_loop_captures_preview_and_audits_without_result_content(
        self,
    ) -> None:
        provider = _PatchProvider()
        registry = ModelProviderRegistry([provider], active_provider="fixture")
        preview = ReadOnlyDesignPatchPreview(_design())
        events: list[tuple[str, Mapping[str, object]]] = []
        loop = BoundedToolLoop(registry, preview.bindings())
        run = loop.run(
            [ModelMessage("user", "Preview a bounded correction")],
            audit_event=lambda event, details: events.append((event, details)),
        )

        self.assertEqual(run.tool_call_count, 1)
        self.assertEqual(len(preview.captured_previews()), 1)
        tool_message = provider.calls[1][-1]
        self.assertIn('"persisted":false', tool_message.content)
        self.assertNotIn("R1 in out 1030", tool_message.content)
        validated = next(details for event, details in events if event == "tool_call_validated")
        self.assertEqual(validated["arguments"]["patch"]["patch_id"], "patch-e24-r1")
        completed = next(details for event, details in events if event == "tool_call_completed")
        self.assertIn("result", completed)
        self.assertNotIn("patch", completed)


if __name__ == "__main__":
    unittest.main()
