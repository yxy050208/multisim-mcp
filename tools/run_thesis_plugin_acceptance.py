"""Exercise the real MCP stdio plugin and native Multisim on a thesis scenario.

Requires a licensed local LM324AJ pack. Outputs include local models; do not
publish the evidence directory as part of the open-source distribution.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

from mcp import Client
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parents[1]


def proposal(trim: float | None = None) -> dict:
    q = json.loads((ROOT / "examples/generated-analog/two-stage-lowpass.json").read_text())
    q["title"] = "毕业设计插件测试：LM324AJ 传感器调理与两级低通"
    q["application"] = "100mV传感器输入，±15V供电，100kΩ负载，目标输出1V±1%、通带增益9.9..10.01、10kHz以上增益≤0.26；1ms施加阶跃，2..5ms输出须稳定在目标范围。使用本地LM324AJ宏模型，非实物验证。"
    q["netlist"] = q["netlist"].replace("OPAMP5", "LM324AJ").replace("DC 0.1 AC 1", "DC 0.1 AC 1 PULSE(0 0.1 1m 1u 1u 5m 10m)")
    if trim is not None:
        q["netlist"] = q["netlist"].replace("R1 in lp1", f"VTRIM adjusted in DC {trim * 1000:.6g}m\nR1 adjusted lp1")
        q["application"] += " 增加模拟零点校准电压源；其硬件实现、温漂和容差仍需后续设计。"
    q["checks"][0].update(min=0.99, max=1.01)
    q["experiments"].append({"type":"tran", "commands":"tran 10u 5m"})
    q["checks"].extend([
        {"analysis":"tran", "net":"out", "quantity":"value", "min":-0.02, "max":0.02, "time_min_s":0, "time_max_s":0.0009},
        {"analysis":"tran", "net":"out", "quantity":"value", "min":0.99, "max":1.01, "time_min_s":0.002, "time_max_s":0.005},
    ])
    return q


async def run(output: Path, optimize: bool) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "mcp_server")
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("MULTISIM_MCP_WORKER_RPC_TIMEOUT", "90")
    params = StdioServerParameters(command=sys.executable, args=["-m", "multisim_mcp.server"], env=env)
    runs = []
    async with Client(stdio_client(params), mode="2026-07-28") as session:
        async def candidate(name: str, q: dict) -> dict:
            preview_path = output / (name + "-preview")
            response = await session.call_tool("run_generated_analog_project", {"proposal":q, "output_dir":str(preview_path), "execute":False})
            if response.is_error or not response.structured_content["success"] or preview_path.exists():
                raise RuntimeError("MCP preview failed or wrote execution output")
            response = await session.call_tool("run_generated_analog_project", {"proposal":q, "output_dir":str(output/name), "execute":True})
            if response.is_error:
                raise RuntimeError(str(response))
            result = response.structured_content
            summary = {"candidate":name, **{key:result.get(key) for key in ("success", "verification_status", "verification_method", "error", "report", "measurement_acceptance", "model_acceptance")}}
            runs.append(summary)
            print(json.dumps(summary, ensure_ascii=False), flush=True)
            return result
        first = await candidate("uncalibrated", proposal())
        if optimize and first.get("error") is None and first.get("measurement_acceptance"):
            checks = first["measurement_acceptance"]["checks"]
            dc = next(c["measured_max"] for c in checks if c["requirement"]["analysis"] == "op")
            gain = next(c["measured_max"] for c in checks if c["requirement"]["analysis"] == "ac")
            if gain <= 0:
                raise ValueError("invalid measured gain for zero calibration")
            trim = (1.0 - dc) / gain
            await candidate("calibrated", proposal(trim))
    result = {"transport":"MCP stdio", "tool":"run_generated_analog_project", "runs":runs,
              "selected":next((r["candidate"] for r in reversed(runs) if r["success"]),None),
              "note":"All candidates use identical acceptance ranges; calibration voltage is an ideal prototype source, not a completed hardware calibration design."}
    (output/"plugin-acceptance.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--optimize", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(run(args.output.resolve(), args.optimize))
    raise SystemExit(0 if result["selected"] else 1)
