"""Bounded, allowlisted tool loop for the future local engineering workbench."""

from __future__ import annotations

import json
import math
import threading
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .model_provider import (
    MAX_TOOL_SCHEMA_BYTES,
    ModelCancelled,
    ModelMessage,
    ModelProviderRegistry,
    ModelResponse,
    ModelRuntimeError,
    ModelUsage,
    ToolDefinition,
)

MAX_AGENT_ROUNDS = 16
MAX_AGENT_TOOL_CALLS = 64
MAX_TOOL_RESULT_BYTES = 262_144


class AgentLimitError(ModelRuntimeError):
    """The model exceeded an explicit round, call, or output bound."""


class ToolExecutionError(ModelRuntimeError):
    """A bound tool rejected its arguments or failed during execution."""


ToolArgumentValidator = Callable[[Mapping[str, Any]], Mapping[str, Any]]
ToolHandler = Callable[[Mapping[str, Any], threading.Event | None], Any]


def _require_json_tree(value: Any, field_name: str, depth: int = 0) -> None:
    if depth > 32:
        raise ToolExecutionError(f"{field_name} exceeds the nesting limit")
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ToolExecutionError(f"{field_name} contains a non-finite number")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ToolExecutionError(f"{field_name} keys must be strings")
            _require_json_tree(item, field_name, depth + 1)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _require_json_tree(item, field_name, depth + 1)
        return
    raise ToolExecutionError(f"{field_name} must contain JSON-compatible values")


@dataclass(frozen=True, slots=True)
class ToolBinding:
    """An exposed tool must have an independent validator and local handler."""

    definition: ToolDefinition
    validate_arguments: ToolArgumentValidator
    handler: ToolHandler

    def __post_init__(self) -> None:
        if not isinstance(self.definition, ToolDefinition):
            raise ValueError("tool binding requires a ToolDefinition")
        if not callable(self.validate_arguments):
            raise ValueError("tool binding requires an argument validator")
        if not callable(self.handler):
            raise ValueError("tool binding requires a handler")


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    final_response: ModelResponse
    transcript: tuple[ModelMessage, ...]
    rounds: int
    tool_call_count: int
    provider_ids: tuple[str, ...]
    usage: ModelUsage | None
    usage_complete: bool

    def __post_init__(self) -> None:
        if not isinstance(self.final_response, ModelResponse):
            raise ValueError("final_response must be ModelResponse")
        if not self.transcript or self.transcript[-1] != self.final_response.message:
            raise ValueError("transcript must end with the final model message")
        if not 1 <= self.rounds <= MAX_AGENT_ROUNDS:
            raise ValueError("round count is invalid")
        if not 0 <= self.tool_call_count <= MAX_AGENT_TOOL_CALLS:
            raise ValueError("tool call count is invalid")
        if len(self.provider_ids) != self.rounds:
            raise ValueError("provider_ids must identify every model round")
        if self.usage is not None and not isinstance(self.usage, ModelUsage):
            raise ValueError("usage must be ModelUsage or null")

    def to_dict(self) -> dict[str, Any]:
        """Return bounded run metadata without duplicating the full transcript."""
        return {
            "final_response": self.final_response.to_dict(),
            "rounds": self.rounds,
            "tool_call_count": self.tool_call_count,
            "provider_ids": list(self.provider_ids),
            "usage": self.usage.to_dict() if self.usage else None,
            "usage_complete": self.usage_complete,
            "transcript_message_count": len(self.transcript),
        }


def _json_result(value: Any) -> str:
    if isinstance(value, str):
        content = value
        try:
            encoded = content.encode("utf-8")
        except UnicodeError as exc:
            raise ToolExecutionError("tool returned invalid UTF-8 text") from exc
    else:
        _require_json_tree(value, "tool result")
        try:
            content = json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
            encoded = content.encode("utf-8")
        except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
            raise ToolExecutionError("tool result must be finite JSON or text") from exc
    if len(encoded) > MAX_TOOL_RESULT_BYTES:
        raise AgentLimitError("tool result exceeded 256 KiB")
    return content


def _validated_arguments(
    binding: ToolBinding, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    try:
        validated = binding.validate_arguments(arguments)
    except ModelCancelled:
        raise
    except Exception as exc:
        raise ToolExecutionError(
            f"tool {binding.definition.name!r} rejected its arguments"
        ) from exc
    if not isinstance(validated, Mapping):
        raise ToolExecutionError("tool argument validator must return an object")
    _require_json_tree(validated, "validated tool arguments")
    try:
        encoded = json.dumps(
            validated,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ToolExecutionError("validated tool arguments must be finite JSON") from exc
    if len(encoded) > MAX_TOOL_SCHEMA_BYTES:
        raise ToolExecutionError("validated tool arguments exceed 64 KiB")
    return json.loads(encoded.decode("utf-8"))


def _validate_initial_transcript(messages: Sequence[ModelMessage]) -> set[str]:
    pending: set[str] = set()
    seen: set[str] = set()
    for message in messages:
        if pending:
            if message.role != "tool" or message.tool_call_id not in pending:
                raise ValueError(
                    "assistant tool calls must be followed by matching tool messages"
                )
            pending.remove(message.tool_call_id)
            continue
        if message.role == "tool":
            raise ValueError("tool message does not match a preceding assistant call")
        if message.tool_calls:
            call_ids = [item.call_id for item in message.tool_calls]
            if len(call_ids) != len(set(call_ids)) or seen.intersection(call_ids):
                raise ValueError("transcript contains duplicate tool call IDs")
            pending.update(call_ids)
            seen.update(call_ids)
    if pending:
        raise ValueError("transcript contains unresolved assistant tool calls")
    return seen


class BoundedToolLoop:
    """Run an explicitly allowlisted and quantitatively bounded tool loop."""

    def __init__(
        self,
        registry: ModelProviderRegistry,
        bindings: Sequence[ToolBinding],
        *,
        max_rounds: int = 8,
        max_tool_calls: int = 16,
    ) -> None:
        if not isinstance(registry, ModelProviderRegistry):
            raise ValueError("registry must be ModelProviderRegistry")
        if (
            isinstance(max_rounds, bool)
            or not isinstance(max_rounds, int)
            or not 1 <= max_rounds <= MAX_AGENT_ROUNDS
        ):
            raise ValueError(f"max_rounds must be between 1 and {MAX_AGENT_ROUNDS}")
        if (
            isinstance(max_tool_calls, bool)
            or not isinstance(max_tool_calls, int)
            or not 1 <= max_tool_calls <= MAX_AGENT_TOOL_CALLS
        ):
            raise ValueError(
                f"max_tool_calls must be between 1 and {MAX_AGENT_TOOL_CALLS}"
            )
        by_name: dict[str, ToolBinding] = {}
        for binding in bindings:
            if not isinstance(binding, ToolBinding):
                raise ValueError("bindings must contain ToolBinding objects")
            name = binding.definition.name
            if name in by_name:
                raise ValueError(f"duplicate tool binding: {name}")
            by_name[name] = binding
        if not by_name:
            raise ValueError("the tool loop requires at least one explicit binding")
        if len(by_name) > 128:
            raise ValueError("the tool loop supports at most 128 bindings")
        self.registry = registry
        self.bindings = by_name
        self.max_rounds = max_rounds
        self.max_tool_calls = max_tool_calls

    def run(
        self,
        messages: Sequence[ModelMessage],
        *,
        provider_id: str | None = None,
        fallback_provider_ids: Sequence[str] = (),
        allow_failover: bool = False,
        max_tokens: int | None = None,
        temperature: float | None = None,
        cancel_event: threading.Event | None = None,
        timeout: float = 60.0,
    ) -> AgentRunResult:
        transcript = list(messages)
        if not transcript or any(
            not isinstance(item, ModelMessage) for item in transcript
        ):
            raise ValueError("messages must contain at least one ModelMessage")
        definitions = tuple(
            self.bindings[name].definition for name in sorted(self.bindings)
        )
        seen_call_ids = _validate_initial_transcript(transcript)
        provider_ids: list[str] = []
        total_calls = 0
        input_tokens = 0
        output_tokens = 0
        total_tokens = 0
        usage_complete = True

        for round_number in range(1, self.max_rounds + 1):
            if cancel_event is not None and cancel_event.is_set():
                raise ModelCancelled("agent run was cancelled")
            response = self.registry.complete(
                transcript,
                definitions,
                provider_id=provider_id,
                fallback_provider_ids=fallback_provider_ids,
                allow_failover=allow_failover,
                max_tokens=max_tokens,
                temperature=temperature,
                cancel_event=cancel_event,
                timeout=timeout,
            )
            transcript.append(response.message)
            provider_ids.append(response.provider_id)
            if response.usage is None:
                usage_complete = False
            else:
                input_tokens += response.usage.input_tokens
                output_tokens += response.usage.output_tokens
                total_tokens += response.usage.total_tokens

            calls = response.message.tool_calls
            if not calls:
                usage = (
                    ModelUsage(input_tokens, output_tokens, total_tokens)
                    if input_tokens or output_tokens or total_tokens
                    else None
                )
                return AgentRunResult(
                    final_response=response,
                    transcript=tuple(transcript),
                    rounds=round_number,
                    tool_call_count=total_calls,
                    provider_ids=tuple(provider_ids),
                    usage=usage,
                    usage_complete=usage_complete,
                )
            if response.finish_reason != "tool_calls":
                raise ModelRuntimeError(
                    "provider returned tool calls without finish_reason=tool_calls"
                )
            if round_number == self.max_rounds:
                raise AgentLimitError(
                    "agent requested tools without a remaining model round"
                )
            if total_calls + len(calls) > self.max_tool_calls:
                raise AgentLimitError("agent exceeded the configured tool call limit")

            call_ids = [call.call_id for call in calls]
            if len(call_ids) != len(set(call_ids)) or seen_call_ids.intersection(
                call_ids
            ):
                raise ModelRuntimeError("provider reused a tool call ID")
            unknown_tools = sorted(
                {call.name for call in calls if call.name not in self.bindings}
            )
            if unknown_tools:
                raise ToolExecutionError(
                    f"model requested unbound tools: {', '.join(unknown_tools)}"
                )
            validated_calls: list[tuple[Any, ToolBinding, Mapping[str, Any]]] = []
            for call in calls:
                binding = self.bindings[call.name]
                arguments = _validated_arguments(binding, call.arguments)
                validated_calls.append((call, binding, arguments))
            seen_call_ids.update(call_ids)

            for call, binding, arguments in validated_calls:
                if cancel_event is not None and cancel_event.is_set():
                    raise ModelCancelled("agent run was cancelled")
                try:
                    result = binding.handler(arguments, cancel_event)
                except ModelCancelled:
                    raise
                except Exception as exc:
                    raise ToolExecutionError(
                        f"tool {call.name!r} failed without exposing internal details"
                    ) from exc
                if cancel_event is not None and cancel_event.is_set():
                    raise ModelCancelled("agent run was cancelled")
                content = _json_result(result)
                try:
                    transcript.append(
                        ModelMessage(
                            role="tool",
                            content=content,
                            tool_call_id=call.call_id,
                        )
                    )
                except ValueError as exc:
                    raise ToolExecutionError(
                        f"tool {call.name!r} returned invalid text"
                    ) from exc
                total_calls += 1

        raise AgentLimitError("agent exceeded the configured model round limit")


__all__ = [
    "AgentLimitError",
    "AgentRunResult",
    "BoundedToolLoop",
    "ToolBinding",
    "ToolExecutionError",
]
