"""Normalize MCP input schemas to the DeepSeek Harness supported subset."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def normalize_dsh_tool_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a DSH-compatible copy of one MCP input schema.

    Local references are expanded, ``anyOf`` is expressed as ``oneOf`` and
    schema-valued ``additionalProperties`` is reduced to the boolean form
    accepted by DSH. The source schema is never mutated.
    """
    if not isinstance(schema, dict):
        return {}
    root = deepcopy(schema)
    definitions: dict[str, Any] = {}
    for key in ("$defs", "definitions"):
        value = root.get(key)
        if isinstance(value, dict):
            definitions.update(value)

    def visit(value: Any, stack: frozenset[str]) -> Any:
        if isinstance(value, list):
            return [visit(item, stack) for item in value]
        if not isinstance(value, dict):
            return value
        ref = value.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            name = ref.removeprefix("#/$defs/")
            target = definitions.get(name)
            if target is not None and name not in stack:
                expanded = visit(target, stack | {name})
                siblings = {key: item for key, item in value.items() if key != "$ref"}
                if isinstance(expanded, dict):
                    expanded.update(visit(siblings, stack))
                    value = expanded
                else:
                    value = siblings
            else:
                value = {key: item for key, item in value.items() if key != "$ref"}
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"$defs", "definitions"}:
                continue
            result[key] = visit(item, stack)
        any_of = result.pop("anyOf", None)
        if any_of is not None and "oneOf" not in result:
            result["oneOf"] = any_of
        if isinstance(result.get("additionalProperties"), dict):
            result["additionalProperties"] = True
        return result

    normalized = visit(root, frozenset())
    return normalized if isinstance(normalized, dict) else {}


__all__ = ["normalize_dsh_tool_schema"]
