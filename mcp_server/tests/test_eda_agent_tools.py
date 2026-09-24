"""Tests for the fixed read-only EDA model-tool surface."""

from __future__ import annotations

import json
import threading
import unittest
from collections.abc import Mapping, Sequence

from multisim_mcp.agent_runtime import BoundedToolLoop, ToolBinding
from multisim_mcp.eda_agent_tools import create_readonly_eda_bindings
from multisim_mcp.eda_core import CircuitComponent, CircuitDesign, ModelReference
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
        revision=2,
        components=(
            CircuitComponent("R1", "R", ("in", "out"), value="1k"),
            CircuitComponent("C1", "C", ("out", "0"), value="10n"),
        ),
        nets=("in", "out", "0", "unused"),
        parameters={"corner_hz": 15915.49},
        annotations={"private_note": "do not expose"},
        model_references=(ModelReference("passive-model", "inline"),),
        source_netlist="R1 in out 1k\nC1 out 0 10n\n.end\n",
    )


def _binding_map(design: CircuitDesign | None = None) -> dict[str, ToolBinding]:
    return {
        item.definition.name: item
        for item in create_readonly_eda_bindings(design or _design())
    }


def _invoke(
    binding: ToolBinding,
    arguments: Mapping[str, object],
    cancel_event: threading.Event | None = None,
) -> object:
    validated = binding.validate_arguments(arguments)
    return binding.handler(validated, cancel_event)


class ReadOnlyEdaToolsTest(unittest.TestCase):
    def test_factory_exposes_only_four_read_only_tools(self) -> None:
        bindings = _binding_map()
        self.assertEqual(
            set(bindings),
            {
                "eda_get_design_summary",
                "eda_list_components",
                "eda_inspect_net",
                "eda_run_structural_checks",
            },
        )
        self.assertTrue(
            all(
                item.definition.parameters["additionalProperties"] is False
                for item in bindings.values()
            )
        )

    def test_summary_is_bounded_and_never_exposes_source_or_annotations(self) -> None:
        result = _invoke(_binding_map()["eda_get_design_summary"], {})
        self.assertEqual(result["design_id"], "filter-v1")
        self.assertEqual(result["component_count"], 2)
        self.assertEqual(result["net_count"], 4)
        self.assertEqual(result["unused_net_count"], 1)
        self.assertTrue(result["source_netlist_present"])
        self.assertFalse(result["source_netlist_exposed"])
        encoded = json.dumps(result)
        self.assertNotIn("R1 in out 1k", encoded)
        self.assertNotIn("private_note", encoded)

    def test_component_listing_validates_filters_and_paginates(self) -> None:
        binding = _binding_map()["eda_list_components"]
        first = _invoke(binding, {"limit": 1})
        self.assertEqual(first["returned_count"], 1)
        self.assertTrue(first["has_more"])
        self.assertEqual(first["next_offset"], 1)
        resistor = _invoke(binding, {"kind": "r", "offset": 0, "limit": 20})
        self.assertEqual(resistor["matching_count"], 1)
        self.assertEqual(resistor["components"][0]["refdes"], "R1")
        self.assertNotIn("parameters", resistor["components"][0])
        self.assertNotIn("annotations", resistor["components"][0])
        with self.assertRaisesRegex(ValueError, "unknown arguments"):
            binding.validate_arguments({"path": "secret.json"})
        with self.assertRaisesRegex(ValueError, "between 1 and 20"):
            binding.validate_arguments({"limit": 21})

    def test_net_inspection_is_exact_and_caps_large_fanout(self) -> None:
        binding = _binding_map()["eda_inspect_net"]
        found = _invoke(binding, {"net": "OUT"})
        self.assertTrue(found["found"])
        self.assertEqual(found["net"], "out")
        self.assertEqual(found["connection_count"], 2)
        self.assertEqual(
            [(item["refdes"], item["pin_index"]) for item in found["connections"]],
            [("R1", 2), ("C1", 1)],
        )
        missing = _invoke(binding, {"net": "missing"})
        self.assertFalse(missing["found"])
        self.assertEqual(missing["connections"], [])

        large = CircuitDesign(
            design_id="fanout",
            title="Large fanout",
            components=tuple(
                CircuitComponent(f"R{index}", "R", ("bus", "0"), value="1k")
                for index in range(1, 122)
            ),
        )
        large_result = _invoke(
            _binding_map(large)["eda_inspect_net"], {"net": "bus"}
        )
        self.assertEqual(large_result["connection_count"], 121)
        self.assertEqual(len(large_result["connections"]), 100)
        self.assertTrue(large_result["connections_truncated"])

    def test_structural_checks_are_labeled_non_simulation_and_bounded(self) -> None:
        result = _invoke(_binding_map()["eda_run_structural_checks"], {})
        self.assertEqual(result["scope"], "structural-only")
        self.assertFalse(result["simulation_performed"])
        self.assertFalse(result["electrical_correctness_proven"])
        self.assertIn("declared-net-unused", result["code_counts"])
        self.assertIn("single-connection-net", result["code_counts"])
        self.assertIn("model-digest-absent", result["code_counts"])
        self.assertIn("model-license-absent", result["code_counts"])

    def test_cancellation_is_checked_before_large_inspection(self) -> None:
        event = threading.Event()
        event.set()
        binding = _binding_map()["eda_get_design_summary"]
        with self.assertRaises(ModelCancelled):
            _invoke(binding, {}, event)


class _ScriptedProvider:
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
        del max_tokens, temperature, cancel_event, timeout
        self.calls.append(tuple(messages))
        self.asserted_tool_names = {item.name for item in tools}
        if len(self.calls) == 1:
            message = ModelMessage(
                "assistant",
                "",
                tool_calls=(ToolCall("call_summary", "eda_get_design_summary", {}),),
            )
            finish_reason = "tool_calls"
        else:
            message = ModelMessage("assistant", "The design has two components.")
            finish_reason = "stop"
        return ModelResponse(
            provider_id=self.provider_id,
            requested_model=self.model,
            model=self.model,
            message=message,
            finish_reason=finish_reason,
        )


class ReadOnlyEdaLoopIntegrationTest(unittest.TestCase):
    def test_bounded_loop_returns_summary_without_exposing_netlist(self) -> None:
        provider = _ScriptedProvider()
        registry = ModelProviderRegistry([provider], active_provider="fixture")
        loop = BoundedToolLoop(registry, create_readonly_eda_bindings(_design()))
        result = loop.run([ModelMessage("user", "Inspect the fixed design")])
        self.assertEqual(result.rounds, 2)
        self.assertEqual(result.tool_call_count, 1)
        self.assertEqual(
            provider.asserted_tool_names,
            {
                "eda_get_design_summary",
                "eda_list_components",
                "eda_inspect_net",
                "eda_run_structural_checks",
            },
        )
        tool_message = provider.calls[1][-1]
        self.assertEqual(tool_message.role, "tool")
        self.assertIn('"source_netlist_exposed":false', tool_message.content)
        self.assertNotIn("R1 in out 1k", tool_message.content)


if __name__ == "__main__":
    unittest.main()
