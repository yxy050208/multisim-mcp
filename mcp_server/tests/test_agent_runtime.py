"""Tests for the bounded, explicitly allowlisted model tool loop."""

from __future__ import annotations

import threading
import unittest
from typing import Any, Mapping

from multisim_mcp.agent_runtime import (
    AgentLimitError,
    BoundedToolLoop,
    ToolBinding,
    ToolExecutionError,
)
from multisim_mcp.model_provider import (
    ModelCancelled,
    ModelMessage,
    ModelProviderRegistry,
    ModelResponse,
    ModelRuntimeError,
    ModelUsage,
    ToolCall,
    ToolDefinition,
)


def _model_response(
    *,
    content: str = "done",
    calls: tuple[ToolCall, ...] = (),
    provider_id: str = "fixture",
) -> ModelResponse:
    return ModelResponse(
        provider_id=provider_id,
        requested_model="fixture-model",
        model="fixture-model",
        message=ModelMessage("assistant", content, tool_calls=calls),
        finish_reason="tool_calls" if calls else "stop",
        usage=ModelUsage(10, 5, 15),
    )


class _SequenceProvider:
    provider_id = "fixture"
    model = "fixture-model"

    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses = responses
        self.requests: list[tuple[tuple[ModelMessage, ...], tuple[ToolDefinition, ...]]] = []

    def complete(
        self,
        messages: Any,
        tools: Any = (),
        **kwargs: Any,
    ) -> ModelResponse:
        self.requests.append((tuple(messages), tuple(tools)))
        return self.responses.pop(0)


def _measure_binding(
    calls: list[Mapping[str, Any]],
    *,
    validator_error: bool = False,
    handler_error: bool = False,
) -> ToolBinding:
    definition = ToolDefinition(
        "measure",
        "Measure a circuit net.",
        {
            "type": "object",
            "properties": {"net": {"type": "string"}},
            "required": ["net"],
            "additionalProperties": False,
        },
    )

    def validate(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        if validator_error or set(arguments) != {"net"} or not isinstance(
            arguments["net"], str
        ):
            raise ValueError("invalid measurement arguments")
        return {"net": arguments["net"]}

    def handle(
        arguments: Mapping[str, Any], cancel_event: threading.Event | None
    ) -> Mapping[str, Any]:
        calls.append(arguments)
        if handler_error:
            raise RuntimeError("internal path and secret must not escape")
        return {"net": arguments["net"], "volts": 5.0}

    return ToolBinding(definition, validate, handle)


def _loop(
    responses: list[ModelResponse],
    bindings: list[ToolBinding],
    **limits: Any,
) -> tuple[BoundedToolLoop, _SequenceProvider]:
    provider = _SequenceProvider(responses)
    registry = ModelProviderRegistry([provider], active_provider="fixture")
    return BoundedToolLoop(registry, bindings, **limits), provider


class BoundedToolLoopTest(unittest.TestCase):
    def test_two_round_run_validates_executes_and_accumulates_usage(self) -> None:
        handler_calls: list[Mapping[str, Any]] = []
        call = ToolCall("call_1", "measure", {"net": "out"})
        loop, provider = _loop(
            [_model_response(content="", calls=(call,)), _model_response(content="5 V")],
            [_measure_binding(handler_calls)],
        )
        result = loop.run([ModelMessage("user", "Measure the output")])
        self.assertEqual(handler_calls, [{"net": "out"}])
        self.assertEqual(result.rounds, 2)
        self.assertEqual(result.tool_call_count, 1)
        self.assertEqual(result.final_response.message.content, "5 V")
        self.assertEqual(result.usage.total_tokens, 30)
        self.assertTrue(result.usage_complete)
        second_messages = provider.requests[1][0]
        self.assertEqual(second_messages[-1].role, "tool")
        self.assertEqual(second_messages[-1].tool_call_id, "call_1")
        self.assertEqual(len(provider.requests[0][1]), 1)

    def test_unbound_tool_is_rejected_before_any_handler(self) -> None:
        handler_calls: list[Mapping[str, Any]] = []
        call = ToolCall("call_1", "delete_everything", {})
        loop, _ = _loop(
            [_model_response(content="", calls=(call,))],
            [_measure_binding(handler_calls)],
        )
        with self.assertRaisesRegex(ToolExecutionError, "unbound"):
            loop.run([ModelMessage("user", "hello")])
        self.assertEqual(handler_calls, [])

    def test_all_arguments_are_prevalidated_before_side_effects(self) -> None:
        handler_calls: list[Mapping[str, Any]] = []
        calls = (
            ToolCall("call_1", "measure", {"net": "out"}),
            ToolCall("call_2", "measure", {"wrong": "out"}),
        )
        loop, _ = _loop(
            [_model_response(content="", calls=calls)],
            [_measure_binding(handler_calls)],
        )
        with self.assertRaisesRegex(ToolExecutionError, "rejected"):
            loop.run([ModelMessage("user", "hello")])
        self.assertEqual(handler_calls, [])

    def test_duplicate_call_ids_are_rejected_before_execution(self) -> None:
        handler_calls: list[Mapping[str, Any]] = []
        calls = (
            ToolCall("call_1", "measure", {"net": "a"}),
            ToolCall("call_1", "measure", {"net": "b"}),
        )
        loop, _ = _loop(
            [_model_response(content="", calls=calls)],
            [_measure_binding(handler_calls)],
        )
        with self.assertRaisesRegex(ModelRuntimeError, "reused"):
            loop.run([ModelMessage("user", "hello")])
        self.assertEqual(handler_calls, [])

    def test_call_limit_and_final_round_prevent_side_effects(self) -> None:
        handler_calls: list[Mapping[str, Any]] = []
        calls = (
            ToolCall("call_1", "measure", {"net": "a"}),
            ToolCall("call_2", "measure", {"net": "b"}),
        )
        limited, _ = _loop(
            [_model_response(content="", calls=calls)],
            [_measure_binding(handler_calls)],
            max_tool_calls=1,
        )
        with self.assertRaisesRegex(AgentLimitError, "call limit"):
            limited.run([ModelMessage("user", "hello")])
        final_round, _ = _loop(
            [_model_response(content="", calls=(calls[0],))],
            [_measure_binding(handler_calls)],
            max_rounds=1,
        )
        with self.assertRaisesRegex(AgentLimitError, "remaining model round"):
            final_round.run([ModelMessage("user", "hello")])
        self.assertEqual(handler_calls, [])

    def test_cancellation_prevents_tool_execution(self) -> None:
        handler_calls: list[Mapping[str, Any]] = []
        loop, _ = _loop(
            [
                _model_response(
                    content="",
                    calls=(ToolCall("call_1", "measure", {"net": "out"}),),
                )
            ],
            [_measure_binding(handler_calls)],
        )
        event = threading.Event()
        event.set()
        with self.assertRaises(ModelCancelled):
            loop.run([ModelMessage("user", "hello")], cancel_event=event)
        self.assertEqual(handler_calls, [])

    def test_handler_failure_does_not_expose_internal_message(self) -> None:
        handler_calls: list[Mapping[str, Any]] = []
        loop, _ = _loop(
            [
                _model_response(
                    content="",
                    calls=(ToolCall("call_1", "measure", {"net": "out"}),),
                )
            ],
            [_measure_binding(handler_calls, handler_error=True)],
        )
        with self.assertRaises(ToolExecutionError) as caught:
            loop.run([ModelMessage("user", "hello")])
        self.assertNotIn("internal path", str(caught.exception))

    def test_initial_transcript_must_have_resolved_unique_tool_calls(self) -> None:
        handler_calls: list[Mapping[str, Any]] = []
        loop, _ = _loop([_model_response()], [_measure_binding(handler_calls)])
        unresolved = ModelMessage(
            "assistant",
            "",
            tool_calls=(ToolCall("call_1", "measure", {"net": "out"}),),
        )
        with self.assertRaisesRegex(ValueError, "unresolved"):
            loop.run([ModelMessage("user", "hello"), unresolved])
        with self.assertRaisesRegex(ValueError, "does not match"):
            loop.run(
                [
                    ModelMessage("user", "hello"),
                    ModelMessage("tool", "result", tool_call_id="call_orphan"),
                ]
            )


if __name__ == "__main__":
    unittest.main()
