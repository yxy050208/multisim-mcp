"""Shared result contract for natural-language engineering tasks."""
from __future__ import annotations

from typing import Any, Mapping
import hashlib
import json
from pathlib import Path

REQUIRED_FIELDS = frozenset({"mode", "verification_status", "output_dir", "success"})
STAGES = frozenset({"preview", "build", "native-open", "native-op", "native-ac", "native-tran",
                    "native-netlist", "acceptance", "complete", "failed"})


def normalize_task_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """Return a stable envelope while preserving component-specific evidence."""
    missing = sorted(REQUIRED_FIELDS.difference(result))
    if missing:
        raise ValueError(f"engineering task result missing fields: {', '.join(missing)}")
    payload = dict(result)
    if payload['mode'] not in {'preview', 'execute'} or not isinstance(payload['success'], bool):
        raise ValueError('mode must be preview/execute and success must be boolean')
    payload.setdefault("task_id", None)
    payload.setdefault("stage", "preview" if payload["mode"] == "preview" else ("complete" if payload["success"] else "failed"))
    payload.setdefault("proposal", None)
    payload.setdefault("native_project", None)
    payload.setdefault("topology_acceptance", None)
    # RC/RLC use acceptance for a file path; OPAMP uses it for a result object.
    acceptance = payload.get('acceptance')
    payload.setdefault("measurement_acceptance", dict(acceptance) if isinstance(acceptance, Mapping) else None)
    payload.setdefault("optimization", None)
    payload.setdefault("artifacts", [])
    payload.setdefault("error", None)
    if payload["stage"] not in STAGES:
        raise ValueError(f"unsupported engineering task stage: {payload['stage']}")
    return payload


def finalize_task_result(root: Path, result: Mapping[str, Any]) -> dict[str, Any]:
    """Persist the same terminal envelope returned to callers, then hash evidence.

    Called only after execution and report generation. It never starts COM.
    Manifest paths are relative to output_dir; the manifest does not hash itself.
    """
    payload = dict(result)
    terminal = 'complete' if payload['success'] else 'failed'
    if payload.get('stage') not in {None, terminal}:
        payload['last_stage'] = payload['stage']
    payload['stage'] = terminal
    selected = payload.get('selected')
    if isinstance(selected, Mapping):
        payload['measurement_acceptance'] = dict(selected)
        payload['topology_acceptance'] = selected.get('topology_acceptance')
        project = selected.get('project')
        if project and (root / project).is_file():
            payload['native_project'] = str(root / project)
    if 'candidates' in payload:
        payload['optimization'] = {
            'tested_candidates': len(payload['candidates']),
            'selected': selected,
        }
    payload = normalize_task_result(payload)
    if (root / 'checkpoint.json').is_file():
        (root / 'checkpoint.json').write_text(json.dumps({
            'stage': terminal, 'last_stage': payload.get('last_stage'),
            'verification_status': payload['verification_status'],
        }, ensure_ascii=False, indent=2), encoding='utf-8')
    names = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    names.update({'acceptance.json', 'manifest.json'})
    payload['artifacts'] = sorted(names)
    (root / 'acceptance.json').write_text(
        json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2) + '\n', encoding='utf-8')
    manifest = {'artifacts': [
        {'path': name, 'sha256': hashlib.sha256((root / name).read_bytes()).hexdigest()}
        for name in payload['artifacts'] if name != 'manifest.json'
    ]}
    (root / 'manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, allow_nan=False, indent=2) + '\n', encoding='utf-8')
    return payload


__all__ = ["REQUIRED_FIELDS", "STAGES", "normalize_task_result", "finalize_task_result"]
