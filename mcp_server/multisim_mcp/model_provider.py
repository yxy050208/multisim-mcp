"""Transport-neutral model-provider contracts and OpenAI-compatible runtime.

This module is intentionally outside the MCP and COM boundaries.  Credentials
are resolved from their configured environment variable for each request and
are never stored on provider objects, results, or exceptions.
"""

from __future__ import annotations

import http.client
import json
import math
import os
import re
import socket
import ssl
import threading
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Final, Mapping, Protocol, Sequence, runtime_checkable
from urllib.parse import urlsplit

from .provider_config import validate_provider, validate_provider_config

MODEL_RUNTIME_SCHEMA_VERSION = 1
MAX_REQUEST_BYTES: Final = 1_048_576
MAX_RESPONSE_BYTES: Final = 2_097_152
MAX_MESSAGE_CHARS: Final = 262_144
MAX_MESSAGES: Final = 256
MAX_TOOLS: Final = 128
MAX_TOOL_SCHEMA_BYTES: Final = 65_536

_ROLES: Final = frozenset({"system", "user", "assistant", "tool"})
_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TOOL_CALL_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class ModelRuntimeError(RuntimeError):
    """Base exception for model-provider and tool-loop failures."""


class ModelCancelled(ModelRuntimeError):
    """Raised when cooperative cancellation is requested."""


class ModelProtocolError(ModelRuntimeError):
    """Raised when a provider response violates the bounded contract."""


class ModelProviderError(ModelRuntimeError):
    """Sanitized provider failure with explicit retry classification."""

    def __init__(
        self,
        message: str,
        *,
        provider_id: str,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.provider_id = provider_id
        self.status_code = status_code
        self.retryable = retryable


class ProviderFailoverError(ModelProviderError):
    """All explicitly selected retryable providers failed."""

    def __init__(self, attempts: Sequence[ModelProviderError]) -> None:
        self.attempts = tuple(attempts)
        summary = ", ".join(
            f"{item.provider_id}:{item.status_code or 'network'}"
            for item in self.attempts
        )
        last = self.attempts[-1]
        super().__init__(
            f"all explicitly selected providers failed ({summary})",
            provider_id=last.provider_id,
            status_code=last.status_code,
            retryable=last.retryable,
        )


def _require_text(
    value: object,
    field_name: str,
    *,
    maximum: int = MAX_MESSAGE_CHARS,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    if len(value) > maximum:
        raise ValueError(f"{field_name} exceeds {maximum} characters")
    if not allow_empty and not value:
        raise ValueError(f"{field_name} must not be empty")
    if _CONTROL_RE.search(value):
        raise ValueError(f"{field_name} contains unsupported control characters")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError(f"{field_name} must be valid UTF-8 text") from exc
    return value


def _json_bytes(value: object, field_name: str) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError(f"{field_name} must be finite JSON data") from exc


def _freeze_json(value: object, field_name: str) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field_name} must contain finite numbers")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{field_name} keys must be strings")
            frozen[key] = _freeze_json(item, field_name)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item, field_name) for item in value)
    raise ValueError(f"{field_name} must contain JSON-compatible values")


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class ToolCall:
    call_id: str
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.call_id, str) or not _TOOL_CALL_ID_RE.fullmatch(
            self.call_id
        ):
            raise ValueError("tool call ID is invalid")
        if not isinstance(self.name, str) or not _TOOL_NAME_RE.fullmatch(self.name):
            raise ValueError("tool name is invalid")
        if not isinstance(self.arguments, Mapping):
            raise ValueError("tool arguments must be an object")
        if len(_json_bytes(self.arguments, "tool arguments")) > MAX_TOOL_SCHEMA_BYTES:
            raise ValueError("tool arguments exceed the size limit")
        object.__setattr__(
            self,
            "arguments",
            _freeze_json(dict(self.arguments), "tool arguments"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.call_id,
            "name": self.name,
            "arguments": _thaw_json(self.arguments),
        }


@dataclass(frozen=True, slots=True)
class ModelMessage:
    role: str
    content: str = ""
    tool_call_id: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    reasoning_content: str | None = None

    def __post_init__(self) -> None:
        if self.role not in _ROLES:
            raise ValueError(f"unsupported message role: {self.role!r}")
        content = _require_text(
            self.content,
            "message content",
            allow_empty=self.role == "assistant" and bool(self.tool_calls),
        )
        calls = tuple(self.tool_calls)
        if any(not isinstance(item, ToolCall) for item in calls):
            raise ValueError("message tool_calls must contain ToolCall objects")
        if len(calls) > MAX_TOOLS:
            raise ValueError("message contains too many tool calls")
        if self.role != "assistant" and calls:
            raise ValueError("only assistant messages may contain tool calls")
        if self.role == "tool":
            if not self.tool_call_id or not _TOOL_CALL_ID_RE.fullmatch(
                self.tool_call_id
            ):
                raise ValueError("tool messages require a valid tool_call_id")
        elif self.tool_call_id is not None:
            raise ValueError("tool_call_id is valid only for tool messages")
        if self.reasoning_content is not None:
            if self.role != "assistant":
                raise ValueError("reasoning_content is valid only for assistant messages")
            _require_text(
                self.reasoning_content,
                "reasoning content",
                maximum=MAX_MESSAGE_CHARS,
                allow_empty=True,
            )
        object.__setattr__(self, "content", content)
        object.__setattr__(self, "tool_calls", calls)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_call_id is not None:
            result["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            result["tool_calls"] = [item.to_dict() for item in self.tool_calls]
        if self.reasoning_content is not None:
            result["reasoning_content"] = self.reasoning_content
        return result


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    parameters: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _TOOL_NAME_RE.fullmatch(self.name):
            raise ValueError("tool name is invalid")
        object.__setattr__(
            self,
            "description",
            _require_text(self.description, "tool description", maximum=4096),
        )
        if not isinstance(self.parameters, Mapping):
            raise ValueError("tool parameters must be a JSON Schema object")
        raw = dict(self.parameters)
        if raw.get("type") != "object":
            raise ValueError("tool parameter schema must have type=object")
        if len(_json_bytes(raw, "tool parameter schema")) > MAX_TOOL_SCHEMA_BYTES:
            raise ValueError("tool parameter schema exceeds the size limit")
        object.__setattr__(
            self,
            "parameters",
            _freeze_json(raw, "tool parameter schema"),
        )

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": _thaw_json(self.parameters),
            },
        }


@dataclass(frozen=True, slots=True)
class ModelUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int

    def __post_init__(self) -> None:
        for field_name in ("input_tokens", "output_tokens", "total_tokens"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        if self.total_tokens < max(self.input_tokens, self.output_tokens):
            raise ValueError("total_tokens is inconsistent")

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass(frozen=True, slots=True)
class ModelResponse:
    provider_id: str
    requested_model: str
    model: str
    message: ModelMessage
    finish_reason: str
    usage: ModelUsage | None = None
    response_id: str | None = None
    request_id: str | None = None
    schema_version: int = MODEL_RUNTIME_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MODEL_RUNTIME_SCHEMA_VERSION:
            raise ValueError(
                f"ModelResponse schema_version must be {MODEL_RUNTIME_SCHEMA_VERSION}"
            )
        for field_name in ("provider_id", "requested_model", "model", "finish_reason"):
            _require_text(getattr(self, field_name), field_name, maximum=256)
        if not isinstance(self.message, ModelMessage) or self.message.role != "assistant":
            raise ValueError("model response must contain an assistant message")
        if self.usage is not None and not isinstance(self.usage, ModelUsage):
            raise ValueError("usage must be ModelUsage or null")
        if self.response_id is not None:
            _require_text(self.response_id, "response_id", maximum=256)
        if self.request_id is not None:
            _require_text(self.request_id, "request_id", maximum=256)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "provider_id": self.provider_id,
            "requested_model": self.requested_model,
            "model": self.model,
            "message": self.message.to_dict(),
            "finish_reason": self.finish_reason,
            "usage": self.usage.to_dict() if self.usage else None,
            "response_id": self.response_id,
            "request_id": self.request_id,
        }


@runtime_checkable
class ModelProvider(Protocol):
    provider_id: str
    model: str

    def complete(
        self,
        messages: Sequence[ModelMessage],
        tools: Sequence[ToolDefinition] = (),
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        cancel_event: threading.Event | None = None,
        timeout: float = 60.0,
    ) -> ModelResponse: ...


@dataclass(frozen=True, slots=True)
class _HttpResponse:
    status: int
    body: bytes
    headers: Mapping[str, str]


_Transport = Callable[
    [str, Mapping[str, str], bytes, float, threading.Event | None], _HttpResponse
]


def _shutdown_connection(connection: http.client.HTTPConnection) -> None:
    active_socket = connection.sock
    if active_socket is not None:
        try:
            active_socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
    connection.close()


def _post_json_blocking(
    endpoint: str,
    headers: Mapping[str, str],
    body: bytes,
    timeout: float,
    cancel_event: threading.Event | None,
    connections: list[http.client.HTTPConnection] | None = None,
) -> _HttpResponse:
    """Perform one bounded POST without redirects."""
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise OSError("provider endpoint is not an absolute HTTP(S) URL")
    connection_class: type[http.client.HTTPConnection]
    kwargs: dict[str, Any] = {"timeout": timeout}
    if parsed.scheme == "https":
        connection_class = http.client.HTTPSConnection
        kwargs["context"] = ssl.create_default_context()
    else:
        connection_class = http.client.HTTPConnection
    connection = connection_class(parsed.hostname, parsed.port, **kwargs)
    if connections is not None:
        connections.append(connection)
    path = parsed.path or "/"
    if parsed.query:
        path += f"?{parsed.query}"
    try:
        if cancel_event is not None and cancel_event.is_set():
            raise ModelCancelled("model request was cancelled")
        connection.request("POST", path, body=body, headers=dict(headers))
        response = connection.getresponse()
        response_body = response.read(MAX_RESPONSE_BYTES + 1)
        response_headers = {
            key.lower(): value for key, value in response.getheaders()
        }
        if len(response_body) > MAX_RESPONSE_BYTES:
            raise ModelProtocolError("model response exceeded 2 MiB")
        if cancel_event is not None and cancel_event.is_set():
            raise ModelCancelled("model request was cancelled")
        return _HttpResponse(response.status, response_body, response_headers)
    finally:
        connection.close()


def _post_json(
    endpoint: str,
    headers: Mapping[str, str],
    body: bytes,
    timeout: float,
    cancel_event: threading.Event | None,
) -> _HttpResponse:
    """Perform a bounded POST and return promptly on cooperative cancellation."""
    if cancel_event is None:
        return _post_json_blocking(endpoint, headers, body, timeout, None)
    if cancel_event.is_set():
        raise ModelCancelled("model request was cancelled")
    completed = threading.Event()
    connections: list[http.client.HTTPConnection] = []
    outcome: list[tuple[str, Any]] = []

    def request_worker() -> None:
        try:
            result = _post_json_blocking(
                endpoint,
                headers,
                body,
                timeout,
                cancel_event,
                connections,
            )
            outcome.append(("result", result))
        except BaseException as exc:
            outcome.append(("error", exc))
        finally:
            completed.set()

    worker = threading.Thread(
        target=request_worker,
        name="multisim-model-request",
        daemon=True,
    )
    worker.start()
    while not completed.wait(0.05):
        if cancel_event.is_set():
            if connections:
                _shutdown_connection(connections[-1])
            raise ModelCancelled("model request was cancelled")
    if cancel_event.is_set():
        raise ModelCancelled("model request was cancelled")
    if not outcome:
        raise OSError("model request ended without a result")
    kind, value = outcome[0]
    if kind == "error":
        raise value
    return value


def _redact(text: str, secret: str) -> str:
    result = text.replace(secret, "[REDACTED]") if secret else text
    return result[:1024]


def _error_message(body: bytes, secret: str) -> str:
    message = "provider request failed"
    try:
        payload = json.loads(body[:65_536].decode("utf-8"))
        if isinstance(payload, Mapping):
            error = payload.get("error")
            if isinstance(error, Mapping) and isinstance(error.get("message"), str):
                message = error["message"]
            elif isinstance(error, str):
                message = error
    except (UnicodeError, ValueError, RecursionError):
        pass
    message = _CONTROL_RE.sub("?", message)
    return _redact(message, secret)


class OpenAICompatibleProvider:
    """Bounded non-streaming Chat Completions provider."""

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        transport: _Transport = _post_json,
    ) -> None:
        item = validate_provider(config)
        self.provider_id = item["id"]
        self.provider_kind = item["provider"]
        self.model = item["model"]
        self.base_url = item["base_url"]
        self._credential = item["credential"]
        self._transport = transport

    def _secret(self) -> str:
        if not self._credential:
            return ""
        variable = self._credential["name"]
        secret = str(os.environ.get(variable, "")).strip()
        if not secret:
            raise ModelProviderError(
                f"provider credential environment variable is not set: {variable}",
                provider_id=self.provider_id,
                retryable=False,
            )
        return secret

    def _message_payload(self, message: ModelMessage) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.role == "assistant" and message.tool_calls:
            payload["content"] = message.content or None
            payload["tool_calls"] = [
                {
                    "id": item.call_id,
                    "type": "function",
                    "function": {
                        "name": item.name,
                        "arguments": _json_bytes(
                            _thaw_json(item.arguments), "tool arguments"
                        ).decode("utf-8"),
                    },
                }
                for item in message.tool_calls
            ]
        if message.role == "tool":
            payload["tool_call_id"] = message.tool_call_id
        if (
            self.provider_kind == "deepseek"
            and message.role == "assistant"
            and message.reasoning_content is not None
        ):
            payload["reasoning_content"] = message.reasoning_content
        return payload

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
        message_items = tuple(messages)
        tool_items = tuple(tools)
        if not message_items or len(message_items) > MAX_MESSAGES:
            raise ValueError(f"messages must contain 1-{MAX_MESSAGES} items")
        if any(not isinstance(item, ModelMessage) for item in message_items):
            raise ValueError("messages must contain ModelMessage objects")
        if len(tool_items) > MAX_TOOLS or any(
            not isinstance(item, ToolDefinition) for item in tool_items
        ):
            raise ValueError(f"tools must contain at most {MAX_TOOLS} definitions")
        names = [item.name for item in tool_items]
        if len(names) != len(set(names)):
            raise ValueError("tool definitions contain duplicate names")
        if max_tokens is not None and (
            isinstance(max_tokens, bool)
            or not isinstance(max_tokens, int)
            or not 1 <= max_tokens <= 1_000_000
        ):
            raise ValueError("max_tokens must be between 1 and 1000000")
        if temperature is not None and (
            isinstance(temperature, bool)
            or not isinstance(temperature, (int, float))
            or not math.isfinite(float(temperature))
            or not 0 <= float(temperature) <= 2
        ):
            raise ValueError("temperature must be between 0 and 2")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(float(timeout))
            or not 0.1 <= float(timeout) <= 300
        ):
            raise ValueError("timeout must be between 0.1 and 300 seconds")
        timeout_value = float(timeout)
        if cancel_event is not None and cancel_event.is_set():
            raise ModelCancelled("model request was cancelled")

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [self._message_payload(item) for item in message_items],
            "stream": False,
        }
        if tool_items:
            payload["tools"] = [item.to_api_dict() for item in tool_items]
            payload["tool_choice"] = "auto"
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if temperature is not None:
            payload["temperature"] = float(temperature)
        body = _json_bytes(payload, "model request")
        if len(body) > MAX_REQUEST_BYTES:
            raise ValueError("model request exceeds 1 MiB")

        secret = self._secret()
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            "User-Agent": "multisim-mcp-model-runtime/1",
        }
        if secret:
            headers["Authorization"] = f"Bearer {secret}"
        endpoint = self.base_url + "/chat/completions"
        try:
            response = self._transport(
                endpoint, headers, body, timeout_value, cancel_event
            )
        except ModelCancelled:
            raise
        except ModelProtocolError:
            raise
        except (OSError, TimeoutError, http.client.HTTPException) as exc:
            if cancel_event is not None and cancel_event.is_set():
                raise ModelCancelled("model request was cancelled") from exc
            raise ModelProviderError(
                _redact(f"provider is unreachable: {exc}", secret),
                provider_id=self.provider_id,
                retryable=True,
            ) from exc
        if cancel_event is not None and cancel_event.is_set():
            raise ModelCancelled("model request was cancelled")
        if not 200 <= response.status < 300:
            retryable = response.status in {408, 409, 429} or response.status >= 500
            raise ModelProviderError(
                _error_message(response.body, secret),
                provider_id=self.provider_id,
                status_code=response.status,
                retryable=retryable,
            )
        return self._parse_response(response)

    def _parse_response(self, response: _HttpResponse) -> ModelResponse:
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise ModelProtocolError("provider returned invalid JSON") from exc
        if not isinstance(payload, Mapping):
            raise ModelProtocolError("provider response must be an object")
        choices = payload.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise ModelProtocolError("provider response must contain one choice")
        choice = choices[0]
        if not isinstance(choice, Mapping) or not isinstance(
            choice.get("message"), Mapping
        ):
            raise ModelProtocolError("provider choice is missing a message")
        message = choice["message"]
        if message.get("role", "assistant") != "assistant":
            raise ModelProtocolError("provider message role must be assistant")
        content = message.get("content")
        if content is None:
            content = ""
        if not isinstance(content, str):
            raise ModelProtocolError("provider message content must be text or null")
        calls_raw = message.get("tool_calls", [])
        if calls_raw is None:
            calls_raw = []
        if not isinstance(calls_raw, list) or len(calls_raw) > MAX_TOOLS:
            raise ModelProtocolError("provider tool_calls must be a bounded array")
        calls: list[ToolCall] = []
        for raw in calls_raw:
            if not isinstance(raw, Mapping) or not isinstance(
                raw.get("function"), Mapping
            ):
                raise ModelProtocolError("provider returned an invalid tool call")
            function = raw["function"]
            arguments = function.get("arguments", "{}")
            if isinstance(arguments, str):
                if len(arguments.encode("utf-8")) > MAX_TOOL_SCHEMA_BYTES:
                    raise ModelProtocolError("tool arguments exceed the size limit")
                try:
                    arguments = json.loads(arguments)
                except (ValueError, RecursionError) as exc:
                    raise ModelProtocolError("tool arguments are not valid JSON") from exc
            if not isinstance(arguments, Mapping):
                raise ModelProtocolError("tool arguments must decode to an object")
            try:
                calls.append(
                    ToolCall(
                        call_id=raw.get("id", ""),
                        name=function.get("name", ""),
                        arguments=arguments,
                    )
                )
            except ValueError as exc:
                raise ModelProtocolError(f"invalid provider tool call: {exc}") from exc
        reasoning = message.get("reasoning_content")
        if reasoning is not None and not isinstance(reasoning, str):
            raise ModelProtocolError("reasoning_content must be text or null")
        try:
            assistant = ModelMessage(
                role="assistant",
                content=content,
                tool_calls=tuple(calls),
                reasoning_content=reasoning,
            )
        except ValueError as exc:
            raise ModelProtocolError(f"invalid assistant message: {exc}") from exc
        finish_reason = choice.get("finish_reason")
        if not isinstance(finish_reason, str) or not finish_reason:
            raise ModelProtocolError("provider choice is missing finish_reason")

        usage: ModelUsage | None = None
        usage_raw = payload.get("usage")
        if usage_raw is not None:
            if not isinstance(usage_raw, Mapping):
                raise ModelProtocolError("provider usage must be an object")
            try:
                usage = ModelUsage(
                    input_tokens=usage_raw.get("prompt_tokens", 0),
                    output_tokens=usage_raw.get("completion_tokens", 0),
                    total_tokens=usage_raw.get("total_tokens", 0),
                )
            except ValueError as exc:
                raise ModelProtocolError(f"invalid provider usage: {exc}") from exc
        model = payload.get("model", self.model)
        if not isinstance(model, str) or not model:
            raise ModelProtocolError("provider model must be text")
        response_id = payload.get("id")
        if response_id is not None and not isinstance(response_id, str):
            raise ModelProtocolError("provider response id must be text")
        request_id = response.headers.get("x-request-id")
        return ModelResponse(
            provider_id=self.provider_id,
            requested_model=self.model,
            model=model,
            message=assistant,
            finish_reason=finish_reason,
            usage=usage,
            response_id=response_id,
            request_id=request_id,
        )


class ModelProviderRegistry:
    """Provider selection with failover disabled unless explicitly authorized."""

    def __init__(
        self,
        providers: Sequence[ModelProvider] = (),
        *,
        active_provider: str | None = None,
    ) -> None:
        self._providers: dict[str, ModelProvider] = {}
        for provider in providers:
            self.register(provider)
        self.active_provider = active_provider
        if self.active_provider is not None and self.active_provider not in self._providers:
            raise ValueError("active provider is not registered")

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        transport_factory: Callable[[Mapping[str, Any]], _Transport] | None = None,
    ) -> "ModelProviderRegistry":
        normalized = validate_provider_config(config)
        providers: list[OpenAICompatibleProvider] = []
        for item in normalized["providers"].values():
            transport = transport_factory(item) if transport_factory else _post_json
            providers.append(OpenAICompatibleProvider(item, transport=transport))
        return cls(providers, active_provider=normalized["active_provider"])

    def register(self, provider: ModelProvider) -> None:
        if not isinstance(provider, ModelProvider):
            raise ValueError("provider must implement ModelProvider")
        if provider.provider_id in self._providers:
            raise ValueError(f"provider is already registered: {provider.provider_id}")
        self._providers[provider.provider_id] = provider

    def provider_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))

    def complete(
        self,
        messages: Sequence[ModelMessage],
        tools: Sequence[ToolDefinition] = (),
        *,
        provider_id: str | None = None,
        fallback_provider_ids: Sequence[str] = (),
        allow_failover: bool = False,
        max_tokens: int | None = None,
        temperature: float | None = None,
        cancel_event: threading.Event | None = None,
        timeout: float = 60.0,
    ) -> ModelResponse:
        selected = provider_id or self.active_provider
        if selected is None:
            raise ValueError("no active model provider is configured")
        fallbacks = tuple(fallback_provider_ids)
        if fallbacks and not allow_failover:
            raise ValueError("fallback providers require allow_failover=True")
        order = (selected,) + fallbacks
        if len(order) != len(set(order)):
            raise ValueError("provider selection contains duplicates")
        missing = [item for item in order if item not in self._providers]
        if missing:
            raise ValueError(f"unknown model providers: {', '.join(missing)}")
        failures: list[ModelProviderError] = []
        for index, item in enumerate(order):
            try:
                return self._providers[item].complete(
                    messages,
                    tools,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    cancel_event=cancel_event,
                    timeout=timeout,
                )
            except ModelProviderError as exc:
                failures.append(exc)
                has_next = index + 1 < len(order)
                if not has_next or not allow_failover or not exc.retryable:
                    if len(failures) == 1:
                        raise
                    raise ProviderFailoverError(failures) from exc
        raise ProviderFailoverError(failures)


__all__ = [
    "MODEL_RUNTIME_SCHEMA_VERSION",
    "ModelCancelled",
    "ModelMessage",
    "ModelProtocolError",
    "ModelProvider",
    "ModelProviderError",
    "ModelProviderRegistry",
    "ModelResponse",
    "ModelRuntimeError",
    "ModelUsage",
    "OpenAICompatibleProvider",
    "ProviderFailoverError",
    "ToolCall",
    "ToolDefinition",
]
