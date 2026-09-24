"""Run the complete rectifier optimization over the real MCP stdio transport."""
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
    env=get_default_environment()
    env['PYTHONPATH']=str(Path(__file__).resolve().parents[1]/'mcp_server')
    env['MULTISIM_MCP_TOOL_PROFILE']='optimization'
    for key in ('MULTISIM_MCP_TEMPLATE_DIR','MULTISIM_MCP_WORKER_RPC_TIMEOUT'):
        if key in os.environ:
            env[key]=os.environ[key]
    params=StdioServerParameters(command=sys.executable,args=['-m','multisim_mcp.server'],env=env)
    async with Client(stdio_client(params),mode='2026-07-28') as client:
        response=await client.call_tool('optimize_natural_rectifier',{'text':text,'output_dir':str(output.resolve()),'execute':True},read_timeout_seconds=1800)
        if response.is_error:
            raise RuntimeError(str(response))
        result=response.structured_content
    persisted=json.loads((output/'acceptance.json').read_text(encoding='utf-8'))
    if persisted!=result:
        raise RuntimeError('MCP response differs from persisted acceptance')
    # Include nested run manifests, not just the aggregate directory.
    for path in output.rglob('manifest.json'):
        for entry in json.loads(path.read_text(encoding='utf-8'))['artifacts']:
            if hashlib.sha256((path.parent/entry['path']).read_bytes()).hexdigest()!=entry['sha256']:
                raise RuntimeError('artifact hash mismatch: '+str(path.parent/entry['path']))
    return {key:result.get(key) for key in ('success','verification_status','native_runs','nominal_best_uf','winner','optimization','native_project','report','error')}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--text',default='设计12V交流输入、100mA负载的桥式整流电路，纹波不超过0.3V，尽量减小滤波电容')
    args=parser.parse_args()
    result=asyncio.run(run(args.text,args.output))
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['success'] else 1)
