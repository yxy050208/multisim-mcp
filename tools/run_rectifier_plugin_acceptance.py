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


async def run(text: str, output: Path) -> dict:
    environment = get_default_environment()
    environment['PYTHONPATH'] = str(Path(__file__).resolve().parents[1]/'mcp_server')
    for key in ('MULTISIM_MCP_TEMPLATE_DIR', 'MULTISIM_MCP_WORKER_RPC_TIMEOUT'):
        if key in os.environ:
            environment[key] = os.environ[key]
    environment['MULTISIM_MCP_TOOL_PROFILE'] = 'experiment'
    params = StdioServerParameters(command=sys.executable, args=['-m','multisim_mcp.server'], env=environment)
    async with Client(stdio_client(params), mode='2026-07-28') as session:
        response = await session.call_tool('run_natural_rectifier', {'text':text,'output_dir':str(output.resolve()),'execute':True})
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
            'rectifier':result.get('rectifier_acceptance'),
            'model_ok':result.get('model_acceptance',{}).get('ok'),
            'topology_ok':result.get('topology_acceptance',{}).get('ok'),
            'presentation':result.get('presentation_acceptance'),
            'measurements':result.get('measurement_acceptance'),
            'manifest_entries':len(manifest['artifacts']), 'native_project':result.get('native_project'),
            'report':result['report'], 'delivery_status':result['delivery_status']}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--text', default='设计12Vrms 50Hz桥式整流，负载100mA，纹波不超过1V')
    args=parser.parse_args()
    result=asyncio.run(run(args.text,args.output))
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['success'] else 1)
