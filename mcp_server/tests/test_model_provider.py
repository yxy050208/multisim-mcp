"""Tests for the transport-neutral model-provider runtime."""

from __future__ import annotations

import json
import os
import threading
import time
import unittest
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from multisim_mcp.model_provider import (
    _HttpResponse,
    ModelCancelled,
    ModelMessage,
    ModelProtocolError,
    ModelProviderError,
    ModelProviderRegistry,
    OpenAICompatibleProvider,
    ProviderFailoverError,
    ToolCall,
    ToolDefinition,
)
from multisim_mcp.provider_config import build_provider, make_provider_config


def _response(
    *,
    content: str | None = "fixture reply",
    finish_reason: str = "stop",
    tool_calls: list[dict[str, object]] | None = None,
    model: str = "fixture-model",
) -> _HttpResponse:
    message: dict[str, object] = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    body = {
        "id": "chatcmpl-fixture",
        "model": model,
        "choices": [
            {"index": 0, "message": message, "finish_reason": finish_reason}
        ],
        "usage": {
            "prompt_tokens": 12,
            "completion_tokens": 4,
            "total_tokens": 16,
        },
    }
    return _HttpResponse(
        status=200,
        body=json.dumps(body).encode("utf-8"),
        headers={"x-request-id": "req-fixture"},
    )


class ModelContractTest(unittest.TestCase):
    def test_message_role_and_tool_relationships_fail_closed(self) -> None:
        call = ToolCall("call_1", "measure", {"net": "out"})
        with self.assertRaisesRegex(ValueError, "only assistant"):
            ModelMessage("user", "hello", tool_calls=(call,))
        with self.assertRaisesRegex(ValueError, "tool_call_id"):
            ModelMessage("tool", "result")
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            ModelMessage("assistant", "")
        assistant = ModelMessage("assistant", "", tool_calls=(call,))
        self.assertEqual(assistant.tool_calls[0].name, "measure")

    def test_tool_arguments_and_schema_are_frozen_and_bounded(self) -> None:
        arguments = {"nested": {"value": 1}}
        call = ToolCall("call_1", "measure", arguments)
        arguments["nested"]["value"] = 2
        self.assertEqual(call.to_dict()["arguments"]["nested"]["value"], 1)
        with self.assertRaisesRegex(ValueError, "type=object"):
            ToolDefinition("bad", "bad schema", {"type": "array"})
        definition = ToolDefinition(
            "measure",
            "Measure a named circuit net.",
            {
                "type": "object",
                "properties": {"net": {"type": "string"}},
                "required": ["net"],
                "additionalProperties": False,
            },
        )
        self.assertEqual(definition.to_api_dict()["type"], "function")

    def test_unpaired_unicode_is_rejected_before_transport(self) -> None:
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            ModelMessage("user", "\ud800")


class OpenAICompatibleProviderTest(unittest.TestCase):
    def test_real_loopback_transport_posts_json_without_redirects(self) -> None:
        captured: dict[str, object] = {}
        response_payload = json.loads(_response().body)

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                captured["path"] = self.path
                captured["body"] = json.loads(self.rfile.read(length))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("X-Request-Id", "req-loopback")
                self.end_headers()
                self.wfile.write(json.dumps(response_payload).encode("utf-8"))

            def log_message(self, format: str, *args: object) -> None:
                return None

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            provider = OpenAICompatibleProvider(
                build_provider(
                    "openai-compatible",
                    provider_id="loopback",
                    base_url=f"http://127.0.0.1:{server.server_port}/v1",
                    model="fixture-model",
                    api_key_env="",
                )
            )
            result = provider.complete([ModelMessage("user", "hello")], timeout=2)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)
        self.assertEqual(captured["path"], "/v1/chat/completions")
        self.assertEqual(captured["body"]["model"], "fixture-model")
        self.assertFalse(captured["body"]["stream"])
        self.assertEqual(result.request_id, "req-loopback")

    def test_real_loopback_transport_closes_on_cancellation(self) -> None:
        request_started = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                request_started.set()
                time.sleep(2)
                with suppress(OSError, BrokenPipeError):
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(_response().body)

            def log_message(self, format: str, *args: object) -> None:
                return None

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        event = threading.Event()

        def cancel_request() -> None:
            request_started.wait(1)
            event.set()

        cancel_thread = threading.Thread(target=cancel_request, daemon=True)
        cancel_thread.start()
        try:
            provider = OpenAICompatibleProvider(
                build_provider(
                    "openai-compatible",
                    provider_id="loopback",
                    base_url=f"http://127.0.0.1:{server.server_port}/v1",
                    model="fixture-model",
                    api_key_env="",
                )
            )
            started = time.monotonic()
            with self.assertRaises(ModelCancelled):
                provider.complete(
                    [ModelMessage("user", "hello")],
                    cancel_event=event,
                    timeout=5,
                )
            elapsed = time.monotonic() - started
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=1)
            cancel_thread.join(timeout=1)
        self.assertLess(elapsed, 1.5)

    def test_completion_sends_bounded_payload_and_parses_usage(self) -> None:
        captured: dict[str, object] = {}

        def transport(
            endpoint: str,
            headers: dict[str, str],
            body: bytes,
            timeout: float,
            cancel_event: threading.Event | None,
        ) -> _HttpResponse:
            captured.update(
                endpoint=endpoint,
                headers=headers,
                payload=json.loads(body),
                timeout=timeout,
                cancel_event=cancel_event,
            )
            return _response(model="deepseek-v4-flash")

        provider = OpenAICompatibleProvider(
            build_provider("deepseek"),
            transport=transport,
        )
        with patch.dict(
            os.environ, {"DEEPSEEK_API_KEY": "fixture-secret"}, clear=True
        ):
            result = provider.complete(
                [ModelMessage("system", "Be precise."), ModelMessage("user", "Hi")],
                max_tokens=100,
                temperature=0.2,
                timeout=1.5,
            )
        self.assertEqual(
            captured["endpoint"], "https://api.deepseek.com/chat/completions"
        )
        self.assertEqual(
            captured["headers"]["Authorization"], "Bearer fixture-secret"
        )
        self.assertFalse(captured["payload"]["stream"])
        self.assertEqual(captured["payload"]["max_tokens"], 100)
        self.assertEqual(result.message.content, "fixture reply")
        self.assertEqual(result.usage.total_tokens, 16)
        self.assertEqual(result.request_id, "req-fixture")
        self.assertNotIn("fixture-secret", json.dumps(result.to_dict()))

    def test_tool_call_is_parsed_and_round_tripped_into_history(self) -> None:
        requests: list[dict[str, object]] = []
        responses = [
            _response(
                content=None,
                finish_reason="tool_calls",
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "measure",
                            "arguments": '{"net":"out"}',
                        },
                    }
                ],
            ),
            _response(content="The output is 5 V."),
        ]

        def transport(*args: object) -> _HttpResponse:
            requests.append(json.loads(args[2]))
            return responses.pop(0)

        provider = OpenAICompatibleProvider(
            build_provider("ollama", model="fixture-model"),
            transport=transport,
        )
        first = provider.complete(
            [ModelMessage("user", "Measure output")],
            [
                ToolDefinition(
                    "measure",
                    "Measure a net.",
                    {"type": "object", "properties": {}},
                )
            ],
        )
        self.assertEqual(first.message.tool_calls[0].to_dict()["arguments"]["net"], "out")
        provider.complete(
            [
                ModelMessage("user", "Measure output"),
                first.message,
                ModelMessage("tool", '{"volts":5}', tool_call_id="call_1"),
            ]
        )
        assistant_history = requests[1]["messages"][1]
        self.assertEqual(assistant_history["tool_calls"][0]["id"], "call_1")
        self.assertEqual(
            json.loads(assistant_history["tool_calls"][0]["function"]["arguments"]),
            {"net": "out"},
        )

    def test_secret_is_resolved_for_every_request_and_redacted_from_errors(self) -> None:
        authorizations: list[str] = []

        def transport(*args: object) -> _HttpResponse:
            authorizations.append(args[1]["Authorization"])
            if len(authorizations) == 1:
                return _response(model="deepseek-v4-flash")
            error = {"error": {"message": "bad second-secret"}}
            return _HttpResponse(401, json.dumps(error).encode(), {})

        provider = OpenAICompatibleProvider(
            build_provider("deepseek"), transport=transport
        )
        with patch.dict(
            os.environ, {"DEEPSEEK_API_KEY": "first-secret"}, clear=True
        ):
            provider.complete([ModelMessage("user", "first")])
            os.environ["DEEPSEEK_API_KEY"] = "second-secret"
            with self.assertRaises(ModelProviderError) as caught:
                provider.complete([ModelMessage("user", "second")])
        self.assertEqual(
            authorizations, ["Bearer first-secret", "Bearer second-secret"]
        )
        self.assertNotIn("second-secret", str(caught.exception))
        self.assertFalse(caught.exception.retryable)

    def test_missing_credential_and_precancel_never_call_transport(self) -> None:
        calls = 0

        def transport(*args: object) -> _HttpResponse:
            nonlocal calls
            calls += 1
            return _response()

        provider = OpenAICompatibleProvider(
            build_provider("deepseek"), transport=transport
        )
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ModelProviderError, "not set"):
                provider.complete([ModelMessage("user", "hello")])
        event = threading.Event()
        event.set()
        with self.assertRaises(ModelCancelled):
            provider.complete(
                [ModelMessage("user", "hello")], cancel_event=event
            )
        self.assertEqual(calls, 0)

    def test_timeout_type_and_bounds_fail_before_transport(self) -> None:
        calls = 0

        def transport(*args: object) -> _HttpResponse:
            nonlocal calls
            calls += 1
            return _response()

        provider = OpenAICompatibleProvider(
            build_provider("ollama", model="fixture-model"), transport=transport
        )
        for invalid in (True, "1", float("nan"), 0.09, 301):
            with self.subTest(timeout=invalid):
                with self.assertRaisesRegex(ValueError, "timeout"):
                    provider.complete(
                        [ModelMessage("user", "hello")], timeout=invalid  # type: ignore[arg-type]
                    )
        self.assertEqual(calls, 0)

    def test_retry_classification_and_protocol_validation(self) -> None:
        responses = [
            _HttpResponse(429, b'{"error":{"message":"rate limited"}}', {}),
            _HttpResponse(200, b'{"choices":[]}', {}),
        ]
        provider = OpenAICompatibleProvider(
            build_provider("ollama", model="fixture-model"),
            transport=lambda *args: responses.pop(0),
        )
        with self.assertRaises(ModelProviderError) as caught:
            provider.complete([ModelMessage("user", "hello")])
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(caught.exception.status_code, 429)
        with self.assertRaises(ModelProtocolError):
            provider.complete([ModelMessage("user", "hello")])

    def test_invalid_tool_arguments_from_provider_are_rejected(self) -> None:
        response = _response(
            content=None,
            finish_reason="tool_calls",
            tool_calls=[
                {
                    "id": "call_1",
                    "function": {"name": "measure", "arguments": "[]"},
                }
            ],
        )
        provider = OpenAICompatibleProvider(
            build_provider("ollama", model="fixture-model"),
            transport=lambda *args: response,
        )
        with self.assertRaisesRegex(ModelProtocolError, "object"):
            provider.complete([ModelMessage("user", "hello")])


class _FakeProvider:
    def __init__(
        self,
        provider_id: str,
        results: list[object],
        calls: list[str],
    ) -> None:
        self.provider_id = provider_id
        self.model = "fixture-model"
        self._results = results
        self._calls = calls

    def complete(self, *args: object, **kwargs: object) -> object:
        self._calls.append(self.provider_id)
        result = self._results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class ModelProviderRegistryTest(unittest.TestCase):
    def test_config_registry_uses_active_provider(self) -> None:
        provider = build_provider("ollama", model="fixture-model")
        config = make_provider_config([provider], "ollama")
        registry = ModelProviderRegistry.from_config(
            config, transport_factory=lambda item: lambda *args: _response()
        )
        result = registry.complete([ModelMessage("user", "hello")])
        self.assertEqual(registry.provider_ids(), ("ollama",))
        self.assertEqual(result.provider_id, "ollama")

    def test_failover_requires_opt_in_and_only_retries_retryable_errors(self) -> None:
        calls: list[str] = []
        retryable = ModelProviderError(
            "temporary",
            provider_id="primary",
            status_code=503,
            retryable=True,
        )
        fallback_result = OpenAICompatibleProvider(
            build_provider("ollama", model="fixture-model"),
            transport=lambda *args: _response(),
        ).complete([ModelMessage("user", "fixture")])
        registry = ModelProviderRegistry(
            [
                _FakeProvider("primary", [retryable], calls),
                _FakeProvider("fallback", [fallback_result], calls),
            ],
            active_provider="primary",
        )
        with self.assertRaisesRegex(ValueError, "allow_failover"):
            registry.complete(
                [ModelMessage("user", "hello")],
                fallback_provider_ids=("fallback",),
            )
        result = registry.complete(
            [ModelMessage("user", "hello")],
            fallback_provider_ids=("fallback",),
            allow_failover=True,
        )
        self.assertEqual(result.message.content, "fixture reply")
        self.assertEqual(calls, ["primary", "fallback"])

    def test_nonretryable_failure_never_reaches_fallback(self) -> None:
        calls: list[str] = []
        unauthorized = ModelProviderError(
            "unauthorized",
            provider_id="primary",
            status_code=401,
            retryable=False,
        )
        registry = ModelProviderRegistry(
            [
                _FakeProvider("primary", [unauthorized], calls),
                _FakeProvider("fallback", [object()], calls),
            ],
            active_provider="primary",
        )
        with self.assertRaises(ModelProviderError):
            registry.complete(
                [ModelMessage("user", "hello")],
                fallback_provider_ids=("fallback",),
                allow_failover=True,
            )
        self.assertEqual(calls, ["primary"])

    def test_all_retryable_failures_return_sanitized_attempt_summary(self) -> None:
        calls: list[str] = []
        failures = [
            ModelProviderError(
                "one", provider_id="one", status_code=503, retryable=True
            ),
            ModelProviderError(
                "two", provider_id="two", status_code=429, retryable=True
            ),
        ]
        registry = ModelProviderRegistry(
            [
                _FakeProvider("one", [failures[0]], calls),
                _FakeProvider("two", [failures[1]], calls),
            ],
            active_provider="one",
        )
        with self.assertRaises(ProviderFailoverError) as caught:
            registry.complete(
                [ModelMessage("user", "hello")],
                fallback_provider_ids=("two",),
                allow_failover=True,
            )
        self.assertEqual(len(caught.exception.attempts), 2)
        self.assertIn("one:503", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
