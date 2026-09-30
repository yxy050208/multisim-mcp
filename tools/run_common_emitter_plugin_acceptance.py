"""Exercise the real MCP stdio tool against a locally licensed Multisim install."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys

from mcp import Client
from mcp.client.stdio import StdioServerParameters, stdio_client, get_default_environment


def _nested_ok(result: dict, key: str):
    """Return a nested acceptance flag while preserving an unreached stage."""
    value = result.get(key)
    return value.get('ok') if isinstance(value, dict) else None


def _report_path(result: dict, output: Path) -> str:
    """Resolve the report emitted by the task contract.

    Candidate-search results keep the detailed report on the selected native
    run, while the top-level contract only records it as a manifest artifact.
    The runner must handle both shapes so a successful MCP call is not turned
    into a false failure while formatting its summary.
    """
    report = result.get('report')
    if isinstance(report, str) and report:
        return report
    selected = result.get('selected')
    if isinstance(selected, dict):
        nested = selected.get('result')
        if isinstance(nested, dict) and isinstance(nested.get('report'), str):
            return nested['report']
    return str(output / 'report.html')


async def run(text: str, output: Path) -> dict:
    environment = get_default_environment()
    environment['PYTHONPATH'] = str(Path(__file__).resolve().parents[1]/'mcp_server')
    for key in ('MULTISIM_MCP_TEMPLATE_DIR', 'MULTISIM_MCP_WORKER_RPC_TIMEOUT'):
        if key in os.environ:
            environment[key] = os.environ[key]
    environment['MULTISIM_MCP_TOOL_PROFILE'] = 'experiment'
    params = StdioServerParameters(command=sys.executable, args=['-m','multisim_mcp.server'], env=environment)
    async with Client(stdio_client(params), mode='2026-07-28') as session:
        response = await session.call_tool('run_natural_common_emitter', {'text':text,'output_dir':str(output.resolve()),'execute':True})
        if response.is_error:
            raise RuntimeError(str(response))
        result = response.structured_content
    persisted = json.loads((output/'acceptance.json').read_text(encoding='utf-8'))
    if persisted != result:
        raise RuntimeError('MCP response and persisted acceptance disagree')
    manifest = json.loads((output/'manifest.json').read_text(encoding='utf-8'))
    for entry in manifest['artifacts']:
        if hashlib.sha256((output/entry['path']).read_bytes()).hexdigest() != entry['sha256']:
            raise RuntimeError('artifact hash mismatch: '+entry['path'])
    return {'success':result['success'], 'verification_status':result['verification_status'],
            'model_ok':_nested_ok(result, 'model_acceptance'),
            'topology_ok':_nested_ok(result, 'topology_acceptance'),
            'presentation':result.get('presentation_acceptance'),
            'measurements':result.get('measurement_acceptance'),
            'manifest_entries':len(manifest['artifacts']), 'native_project':result.get('native_project'),
            'report':_report_path(result, output),
            'delivery_status':result.get('delivery_status')}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--text', default='设计一个12V单电源、增益10倍的2N3904 NPN共射放大器')
    args=parser.parse_args()
    result=asyncio.run(run(args.text,args.output))
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['success'] else 1)
