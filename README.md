# Multisim MCP + Skills

[![Glama MCP server score](https://glama.ai/mcp/servers/yxy050208/multisim-mcp/badges/score.svg)](https://glama.ai/mcp/servers/yxy050208/multisim-mcp)

[中文（当前）](README.md) | [English](README.en.md)

让 AI Agent 根据实验要求自动生成 Multisim 电路、运行仿真、提取实验数据，并导出
电路图、CSV、波形图和实验报告。

> 当前版本定位为 `v0.1.0-alpha.2`。项目非 NI 官方产品，需要本机安装并授权
> Multisim 14+；当前 COM 运行时使用 32 位 Python。

[PyPI 安装包](https://pypi.org/project/multisim-mcp/) ·
[官方 MCP Registry 条目](https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.yxy050208%2Fmultisim-mcp) ·
[GitHub Release](https://github.com/yxy050208/multisim-mcp/releases/tag/v0.1.0-alpha.2)

## 开源发布状态

`0.1.0a2` 已发布到 PyPI，并以 `io.github.yxy050208/multisim-mcp` 收录到官方
MCP Registry。由本地 NI 样例提取的 XML 模板不属于
MIT 代码授权范围，公开仓库默认不应包含这些文件。用户需要运行
`tools/bootstrap_local_component_pack.py`，从自己已授权的 Multisim 安装生成本地模板包。

请不要上传 `analysis/`、`.ms14`、解码 XML、类型库转储、实验输出或当前包含 142 个
本地模板的开发 wheel。完整发布步骤见 [`docs/PUBLISHING.md`](docs/PUBLISHING.md)。

## 已实现的完整闭环

`run_circuit_experiment` 可以从同一份受限 SPICE 网表完成：

1. 网表和实验命令安全校验。
2. 生成可编辑 `.ms14` 原理图。
3. 由真实 Multisim 打开并反向枚举验证。
4. 导出原理图 PNG。
5. 运行 DC、AC、瞬态或工作点实验。
6. 导出 raw、CSV、SVG 波形和命令日志。
7. 生成可复现 Markdown 实验报告。

已经在 Multisim 14.3 上完成分压器、耦合电感、数字门/JK 时序以及
函数发生器 + 示波器联合实验的真实验证。

## 能力成熟度

稳定：

- Multisim COM 连接、打开、保存、枚举和导出。
- DC/AC/瞬态分析、波形输入注入、RLC 读写。
- 安全子集 SPICE 实验和 raw/CSV 解析。
- MCP stdio、运行环境诊断和报告生成。

实验性：

- 自动原理图支持 R/L/C、标量及波形电压/电流源、B/E/F/G/H 受控源、
  K/T/O/U 耦合与传输线、二极管、NPN/PNP、NMOS/PMOS、JFET/MESFET、
  电压/电流控制开关、五端运放及 2–16 端通用 X 子电路；扩展族暂用通用载体符号。生成后会通过
  Multisim 反向网表确认器件没有被静默丢弃。
- 已加入原生 NOT/AND/OR/NAND/NOR/XOR/XNOR 和 JK 触发器预览；
  原理图打开/回导、组合逻辑真值表和 JK 翻转时序均已真实验证。
- 支持原生 XFG 函数发生器和四通道 XSC 示波器状态，实验波形同时导出为
  CSV、SVG 和 Markdown 报告。
- 自动生成的原理图探针暂不作为实验数据来源；实验数据来自同一网表经 Multisim
  命令引擎执行的结果。

## 快速开始

从 PyPI 安装到 32 位 Python 环境：

```powershell
C:\path\to\python32\python.exe -m pip install "multisim-mcp==0.1.0a2"
C:\path\to\python32\Scripts\multisim-mcp.exe
```

Linux/Docker 仅提供 MCP 工具发现和兼容性诊断，不能运行 Multisim 仿真。容器中的
`runtime_status` 会返回 `introspection-only`；所有 COM 自动化能力仍要求上述 Windows
环境。根目录 `Dockerfile` 用于 Glama 等目录验证 MCP 协议和工具定义。

从源码安装并生成本地元件模板包：

```powershell
cd mcp_server
.\setup.ps1 -Python C:\path\to\python32\python.exe
npm install --global electronics-workbench-decoder@0.2.0
cd ..
$env:PYTHONPATH = (Resolve-Path .\mcp_server).Path
C:\path\to\python32\python.exe .\tools\bootstrap_local_component_pack.py `
  --output C:\MultisimMcp\component-pack
$env:MULTISIM_MCP_TEMPLATE_DIR = 'C:\MultisimMcp\component-pack'
cd mcp_server
.\run_server.ps1
```

MCP 客户端配置：

```json
{
  "mcpServers": {
    "multisim": {
      "command": "C:\\path\\to\\python32\\python.exe",
      "args": ["-m", "multisim_mcp.server"]
    }
  }
}
```

详细安装、工具、安全开关和测试说明见 [`mcp_server/README.md`](mcp_server/README.md)。
元件覆盖和剩余边界见 [`docs/COMPONENT_COVERAGE.md`](docs/COMPONENT_COVERAGE.md)。

## 仓库结构

- `mcp_server/`：MCP server、COM adapter、原理图构建器和测试。
- `skills/multisim-workflow/`：面向 Agent 的实验工作流。
- `docs/`：架构、实测流程和能力边界。
- `analysis/`、`tools/`：互操作研究材料；发布前需要独立做来源和隐私审计。

## 安全

- 默认只允许 `op`、`dc`、`ac`、`tran` 实验命令。
- 任意 Multisim 命令文件默认关闭，需要显式不安全环境开关。
- MCP 启动过程不会自动安装 Python 或 npm 依赖。
- 默认拒绝覆盖已有实验文件。
- 仅适合可信本机 stdio 使用，不应直接暴露到网络。

详见 [`SECURITY.md`](SECURITY.md)。

## License

项目自有代码采用 MIT License。NI Multisim、商标、格式以及本地安装样本仍受其各自
权利和许可条款约束。本仓库不应提交 NI 二进制、许可证材料或未确认可再分发的样本。
