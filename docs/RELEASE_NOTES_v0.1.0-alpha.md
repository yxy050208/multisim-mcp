# Multisim MCP v0.1.0-alpha

[PyPI](https://pypi.org/project/multisim-mcp/0.1.0a1/) ·
[官方 MCP Registry / Official MCP Registry](https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.yxy050208%2Fmultisim-mcp) ·
[源代码 / Source](https://github.com/yxy050208/multisim-mcp/tree/v0.1.0-alpha)

## 中文（主要说明）

这是 Multisim MCP 的首个 Alpha 版本。它允许本机 AI Agent 根据受限 SPICE 网表
生成可编辑 Multisim 电路，调用真实 Multisim 完成实验，并导出原理图、CSV、SVG
波形、raw 数据和 Markdown 实验报告。

主要能力：

- DC、AC、瞬态和工作点实验；
- R/L/C、受控源、半导体、开关、耦合/传输线和 2–16 端子电路；
- NOT/AND/OR/NAND/NOR/XOR/XNOR 和 JK 时序；
- 原生 XFG 函数发生器与 XSC 示波器状态；
- 默认安全命令白名单、外部文件指令阻断和覆盖保护；
- 从用户已授权 Multisim 安装生成本地模板包。

已验证：48 项无 COM 测试、19 项安装包资源测试，以及 8 组真实 Multisim 14.3
元件族回归。

重要限制：仅支持 Windows；需要本地授权 Multisim；COM worker 使用 32 位 Python；
自动布局、部分符号美术和数字器件仍属于 Alpha；超过 16 端的通用子电路尚未支持。

许可说明：MIT 仅覆盖项目自有代码。发布附件不包含 NI Multisim、NI 示例电路或
从本地样例提取的 XML 模板。用户必须从自己的授权安装生成模板包。

PyPI 安装：

```powershell
C:\path\to\python32\python.exe -m pip install "multisim-mcp==0.1.0a1"
```

## English summary

The first alpha release provides a local constrained-SPICE-to-Multisim workflow:
editable schematic generation, real Multisim analyses, and raw/CSV/SVG/Markdown
exports. It includes broad analog primitives, 2-16-pin generic subcircuits,
logic/JK timing, and native XFG/XSC instrument state.

Windows, a licensed local Multisim installation, and 32-bit Python for COM are
required. The MIT license covers project-owned code only; NI software, samples,
and locally extracted XML packs are not distributed.

The package is published as `multisim-mcp==0.1.0a1` and is discoverable in the
official MCP Registry as `io.github.yxy050208/multisim-mcp`.
