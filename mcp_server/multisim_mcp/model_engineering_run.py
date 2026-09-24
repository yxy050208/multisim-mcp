"""Persist model planning, requirement differences and native experiment evidence."""
from __future__ import annotations

import hashlib
import html
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .model_engineering import ModelRequirementError, model_plan_engineering_request
from .model_provider import ModelProviderRegistry
from .natural_engineering_run import run_natural_engineering


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + '\n', encoding='utf-8')


def _report(root: Path, result: dict[str, Any]) -> None:
    audit = result.get('model_plan', {})
    difference_rows = ''.join(
        '<tr>' + ''.join('<td>' + html.escape(str(item.get(key, ''))) + '</td>'
                        for key in ('field', 'kind', 'original', 'proposed')) + '</tr>'
        for item in audit.get('consistency', {}).get('differences', []))
    native_link = ('<p><a href="native/report.html">原生仿真、选值与电路图报告</a></p>'
                   if (root / 'native/report.html').is_file() else '')
    content = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>模型工程实验记录</title>'
        '<style>body{font:16px/1.7 system-ui;max-width:1000px;margin:auto;padding:30px}'
        'pre{white-space:pre-wrap;overflow-wrap:anywhere}td,th{padding:8px;border:1px solid #ddd}table{border-collapse:collapse}</style>'
        '<h1>模型工程实验记录</h1><p>结果：' + html.escape(result['verification_status']) + '</p>'
        '<h2>原始需求</h2><pre>' + html.escape(result['input_text']) + '</pre>'
        '<h2>模型提案</h2><pre>' + html.escape(audit.get('proposed_text', '未获得有效提案')) + '</pre>'
        '<p>提供器：' + html.escape(str(audit.get('provider_id', '未调用或未返回'))) + '；模型：'
        + html.escape(str(audit.get('model', '未知'))) + '</p><h2>需求一致性检查</h2><p>'
        + html.escape(str(audit.get('consistency', {}).get('status', '未完成'))) + '</p>'
        '<table><tr><th>字段</th><th>差异</th><th>原始值</th><th>提案值</th></tr>' + difference_rows + '</table>'
        + ('<p>' + html.escape(str(result['error'])) + '</p>' if result.get('error') else '')
        + '<p>一致性检查覆盖当前 RC 入口识别的参数、选值权限及本地推导的实验计划。'
        '它不证明任意自然语言完全等价。只有检查通过才开始原生实验，执行始终以原始需求为准。</p>'
        + native_link + '<p><a href="model-plan.json">模型记录</a> · <a href="acceptance.json">总验收记录</a> · '
        '<a href="manifest.json">文件校验清单</a></p></html>')
    (root / 'report.html').write_text(content, encoding='utf-8')


def run_model_engineering(
    text: str, output_dir: str, *, execute: bool = False,
    provider_config_path: str | None = None, provider: str | None = None,
    fallback_providers: Sequence[str] = (), allow_failover: bool = False,
    timeout: float = 60.0, registry: ModelProviderRegistry | None = None,
) -> dict[str, Any]:
    """Journal planning before executing the ORIGINAL request in a child folder.

    Preview calls the model but never creates files or invokes native execution.
    Explicit execution preserves rejections and provider failures as evidence.
    """
    if not isinstance(execute, bool):
        raise ValueError('execute must be a boolean')
    if not isinstance(text, str) or not text.strip() or len(text) > 4000:
        raise ValueError('text must contain 1-4000 characters')
    if not isinstance(output_dir, str) or not output_dir.strip():
        raise ValueError('output_dir must be a non-empty path')
    root = Path(output_dir).expanduser().resolve()
    if root.exists() or root == Path(root.anchor):
        raise FileExistsError('output must be a new directory')
    result: dict[str, Any] = {'schema_version': 1, 'success': False, 'input_text': text,
        'mode': 'execute' if execute else 'preview', 'output_dir': str(root),
        'verification_status': 'failed', 'native_execution_attempted': False}
    if execute:
        root.mkdir(parents=True, exist_ok=False)
        (root / 'input.txt').write_text(text, encoding='utf-8')
    try:
        model_plan = model_plan_engineering_request(text, provider_config_path=provider_config_path,
            provider=provider, fallback_providers=fallback_providers, allow_failover=allow_failover,
            timeout=timeout, registry=registry)
        result['model_plan'] = model_plan
        if execute:
            _write(root / 'model-plan.json', model_plan)
            result['native_execution_attempted'] = True
            native = run_natural_engineering(text, str(root / 'native'), execute=True)
            result.update(native=native, success=native['success'], verification_status=native['verification_status'])
            if native.get('error'):
                result['error'] = native['error']
        else:
            result.update(success=True, verification_status='unverified')
    except ModelRequirementError as exc:
        result.update(model_plan=exc.audit, verification_status='requirements-rejected',
                      error={'type': type(exc).__name__, 'message': str(exc)})
        if exc.audit.get('failure', {}).get('stage') == 'provider':
            result['verification_status'] = 'model-failed'
    except (OSError, ValueError, RuntimeError) as exc:
        result['error'] = {'type': type(exc).__name__, 'message': str(exc)}
    if execute:
        _write(root / 'model-plan.json', result.get('model_plan', {'input_text': text, 'failure': result.get('error')}))
        result['report'] = str(root / 'report.html')
        result['acceptance'] = str(root / 'acceptance.json')
        _write(root / 'acceptance.json', result)
        _report(root, result)
        _write(root / 'manifest.json', {'artifacts': [
            {'path': p.relative_to(root).as_posix(), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in sorted(root.rglob('*')) if p.is_file() and p != root / 'manifest.json'
        ]})
    return result
