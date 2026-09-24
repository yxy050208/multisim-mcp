"""Model-assisted planning checked against the user's original RC contract."""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

from .model_provider import ModelMessage, ModelProviderRegistry, ToolDefinition
from .natural_engineering import parse_natural_request
from .provider_config import read_provider_config

_TOOL = ToolDefinition(
    'propose_natural_requirement',
    'Restate the original supported RC requirement without changing values, permissions or constraints.',
    {'type': 'object', 'properties': {'text': {'type': 'string', 'minLength': 1, 'maxLength': 4000}},
     'required': ['text'], 'additionalProperties': False},
)


class ModelRequirementError(ValueError):
    """Rejected proposal with bounded evidence for the execution journal."""
    def __init__(self, message: str, audit: dict[str, Any]):
        super().__init__(message)
        self.audit = audit


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    allow_nan=False, separators=(',', ':')).encode('utf-8')).hexdigest()


def _equal_number(left: Any, right: Any) -> bool:
    return (not isinstance(left, bool) and not isinstance(right, bool)
            and isinstance(left, (int, float)) and isinstance(right, (int, float))
            and math.isclose(left, right, rel_tol=1e-10, abs_tol=0))


def compare_requirement_proposals(original: Mapping[str, Any], proposed: Mapping[str, Any]) -> dict[str, Any]:
    """Compare recognized requirements and the full locally derived execution plan.

    This is a bounded RC check, not proof of equivalence of arbitrary prose.
    Native execution always uses the original request, even after a match.
    """
    differences: list[dict[str, Any]] = []
    before, after = original['requirement_contract'], proposed['requirement_contract']
    for key in ('topology', 'automatic_selection'):
        if before[key] != after[key]:
            differences.append({'field': key, 'kind': 'changed', 'original': before[key], 'proposed': after[key]})
    for key, value in before['explicit_parameters'].items():
        other = after['explicit_parameters'].get(key)
        if other is None:
            differences.append({'field': key, 'kind': 'omitted', 'original': value, 'proposed': None})
        elif not _equal_number(value, other):
            differences.append({'field': key, 'kind': 'changed', 'original': value, 'proposed': other})
    for key, value in original['derived'].items():
        if not _equal_number(value, proposed['derived'].get(key)):
            differences.append({'field': f'derived.{key}', 'kind': 'changed', 'original': value,
                                'proposed': proposed['derived'].get(key)})
    for key in ('candidate_resistances_ohm', 'netlist'):
        if original[key] != proposed[key]:
            differences.append({'field': key, 'kind': 'changed'})
    for key in ('experiments', 'objectives'):
        if original['request'][key] != proposed['request'][key]:
            differences.append({'field': key, 'kind': 'changed'})
    return {'status': 'pass' if not differences else 'rejected', 'differences': differences,
            'scope': 'recognized RC requirements and derived execution plan',
            'original_contract_sha256': _digest(before), 'proposed_contract_sha256': _digest(after)}


def _model_messages(user_text: str) -> list[ModelMessage]:
    return [ModelMessage('system',
        'Restate the supplied RC low-pass requirement using propose_natural_requirement exactly once. '
        'Preserve every explicit numeric value, unit, constraint and permission to change parameters. '
        'Never replace the request with a closest supported circuit. Never add automatic selection unless requested. '
        'Return the original text unchanged if uncertain. Do not emit explanations or extra tool fields.'),
        ModelMessage('user', user_text)]


def model_plan_engineering_request(
    text: str, *, provider_config_path: str | None = None, provider: str | None = None,
    fallback_providers: Sequence[str] = (), allow_failover: bool = False, timeout: float = 60.0,
    registry: ModelProviderRegistry | None = None,
) -> dict[str, Any]:
    """Validate the original before any model call; reject semantic changes."""
    if not isinstance(text, str) or not text.strip() or len(text) > 4000:
        raise ValueError('text must contain 1-4000 characters')
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 120:
        raise ValueError('model timeout must be greater than 0 and at most 120 seconds')
    audit: dict[str, Any] = {'schema_version': 2, 'kind': 'model-assisted-engineering-plan',
        'input_text': text, 'validation': 'original-and-proposed-rc-contract', 'model_called': False}

    def reject(message: str, stage: str) -> None:
        audit['consistency'] = audit.get('consistency', {'status': 'rejected', 'differences': []})
        audit['failure'] = {'stage': stage, 'message': message}
        raise ModelRequirementError(message, audit)

    try:
        original = parse_natural_request(text)
    except ValueError as exc:
        reject(str(exc), 'original-requirements')
    audit['original_proposal'] = original
    try:
        if registry is None:
            registry = ModelProviderRegistry.from_config(read_provider_config(provider_config_path))
        audit['model_called'] = True
        response = registry.complete(_model_messages(text), [_TOOL], provider_id=provider,
            fallback_provider_ids=tuple(fallback_providers), allow_failover=allow_failover,
            max_tokens=1200, temperature=0, timeout=timeout)
    except (OSError, ValueError, RuntimeError) as exc:
        reject(str(exc), 'provider')
    audit.update(provider_id=response.provider_id, requested_model=response.requested_model,
                 model=response.model, response_id=response.response_id,
                 usage=response.usage.to_dict() if response.usage else None,
                 finish_reason=response.finish_reason)
    calls = response.message.tool_calls
    audit['response_contract'] = {'tool_call_count': len(calls), 'tool_names': [c.name for c in calls]}
    if response.finish_reason != 'tool_calls':
        reject('model response was incomplete or did not finish with tool_calls', 'response-contract')
    if len(calls) != 1 or calls[0].name != _TOOL.name:
        reject('model must call propose_natural_requirement exactly once', 'response-contract')
    arguments = dict(calls[0].arguments)
    audit['response_contract']['argument_fields'] = sorted(arguments)
    proposed = arguments.get('text')
    if isinstance(proposed, str) and len(proposed) <= 4000:
        audit['proposed_text'] = proposed
    if set(arguments) != {'text'}:
        reject('model arguments must contain only text', 'response-contract')
    if not isinstance(proposed, str) or not proposed.strip() or len(proposed) > 4000:
        reject('model proposed an empty or oversized requirement', 'response-contract')
    try:
        parsed = parse_natural_request(proposed)
    except ValueError as exc:
        reject(str(exc), 'proposed-requirements')
    audit['proposal'] = parsed
    audit['consistency'] = compare_requirement_proposals(original, parsed)
    if audit['consistency']['status'] != 'pass':
        reject('model changed or omitted original requirements; native execution is blocked', 'requirement-consistency')
    return audit


__all__ = ['model_plan_engineering_request', 'compare_requirement_proposals', 'ModelRequirementError']
