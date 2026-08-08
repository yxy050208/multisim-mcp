# Changelog / 更新日志

本项目遵循语义化版本的预发布形式。中文为主要说明，英文摘要紧随其后。

## [0.1.0-alpha.2] - 2026-08-09

### 中文

- 增加 Linux/Docker `introspection-only` 模式，可完成 MCP 初始化和工具发现。
- 将 `pywin32` 限定为 Windows 依赖；非 Windows 自动化调用返回明确兼容性提示。
- 增加最小权限 Glama 验证镜像、严格 Docker 构建上下文和 Ubuntu 容器握手 CI。
- 实际 Multisim 电路生成、仿真和导出仍仅支持本地 Windows + 32 位 Python。

### English

- Added a Linux/Docker `introspection-only` mode for MCP initialization and
  tool discovery.
- Made `pywin32` Windows-specific and added explicit diagnostics for unsupported
  automation runtimes.
- Added a least-privilege Glama validation image, a strict Docker build context,
  and an Ubuntu container handshake check.
- Real Multisim generation, simulation, and export remain Windows-only.

## [0.1.0-alpha] - 2026-08-09

### 中文

- 建立从受限 SPICE 网表到可编辑 Multisim 原理图、真实仿真、CSV/SVG 和
  Markdown 报告的完整工作流。
- 支持主要模拟 SPICE 原语、2–16 端通用子电路、组合逻辑和 JK 触发器。
- 接入原生 XFG 函数发生器和 XSC 示波器状态。
- 增加命令白名单、外部文件指令拦截、覆盖保护和本机 stdio 安全边界。
- 增加本地模板包生成器，避免在公开仓库分发许可不明确的 NI 派生 XML。
- 发布 `multisim-mcp==0.1.0a1` 到 PyPI，并登记到官方 MCP Registry。
- 48 项无 COM 测试、19 项安装包资源测试和 8 组真实 Multisim 元件族回归通过。

### English

- Added the complete constrained-netlist → editable schematic → real simulation
  → CSV/SVG/Markdown workflow.
- Added major analog SPICE primitives, generic 2-16-pin subcircuits, logic gates,
  JK timing, and native XFG/XSC instrument state.
- Added safe command validation, overwrite protection, local-pack generation,
  and release-time asset separation.
- Published `multisim-mcp==0.1.0a1` to PyPI and the official MCP Registry.
- Verified 48 COM-free tests, 19 installed-package resource tests, and eight
  real Multisim component-family groups.
