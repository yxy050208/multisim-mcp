# Changelog / 更新日志

本项目遵循语义化版本的预发布形式。中文为主要说明，英文摘要紧随其后。

## [Unreleased - toward 1.0.0]

### 中文

- 迁移到 MCP Python SDK 2.x；同一 stdio 服务兼容 `2026-07-28` 和旧协议客户端。
- 将所有工具调用串行到专用 COM 线程，适配 SDK 2 的同步 handler 线程模型。
- 增加 11 个 `multisim://experiments/...` 实验资源模板、2 个扫描资源模板和重启后
  重新注册工具。
- 增加创建实验、调试、比较、报告和指标验证五个中英双语 Prompt。
- 为完整实验及资源注册结果增加明确的 output schema 和运行时结构校验。
- 增加 32 位 Windows 可安装的加密依赖边界及现代/旧协议、资源安全测试。
- 增加持久实验任务状态机，以及提交、查询、列出、取消和安全重试任务的 MCP 工具与状态 Resource。
- 将异步实验隔离到独立 worker 进程，支持排队、检查点、进度、取消、总超时、
  心跳超时、崩溃检测和 MCP 重启后的安全重排队。
- 为输出目录增加同名任务占用检查与跨进程文件租约；完整产物仍以事务方式发布。
- 增加版本化 `ExperimentSpec`、13 类确定性测量、逐项
  PASS/FAIL/未验证结论，以及理论值/仿真值/误差的结构化比较。
- 增加参数、容差、温度和可复现 Monte Carlo 扫描，包含 100 次硬上限、事务式
  汇总产物、MCP Resources 和持久 worker 支持。

### English

- Migrated to MCP Python SDK 2.x with one stdio server serving both the
  `2026-07-28` and legacy protocol eras.
- Serialized every tool call onto a dedicated COM-initialized worker thread.
- Added eleven experiment resource templates, two sweep resource templates,
  re-registration, five bilingual prompts, and validated structured results
  for the high-level workflow.
- Added a 32-bit Windows-compatible cryptography constraint and dual-era,
  resource-security, and structured-output tests.
- Added a durable experiment-job state machine with queueing, progress,
  cancellation, total/heartbeat timeouts, restart recovery, and a status
  resource.
- Isolated asynchronous experiments in restartable subprocesses and added
  cross-process output leases plus structured crash/hang diagnostics.
- Added versioned design requirements, deterministic measurements, strict
  pass/fail/unverified verdicts, and theory-versus-simulation errors.
- Added parameter, tolerance, temperature, and seeded Monte Carlo sweeps with
  a 100-run cap, transactional summaries, resources, and durable jobs.

## [0.1.0-alpha.3] - 2026-08-09

### 中文

- 新增保持无参数 stdio 启动兼容的 `multisim-mcp doctor`、`serve` 和
  `config` 命令。
- `doctor` 默认无副作用检查 Python 位数、pywin32、Multisim COM 注册、模板包和
  `.ms14` 编解码器，并提供稳定 JSON、可选严格退出码和显式 `--connect` 激活验证。
- 配置生成器可输出 Claude Desktop JSON、Codex TOML 和通用 stdio JSON，默认
  仅预览且拒绝静默覆盖文件。
- 将 pywin32 首次导入的 COM 缓存输出隔离到 stderr，避免污染 MCP/JSON stdout。
- 当 pywin32 的 `gen_py` 生成包装缓存损坏时，自动回退到动态 COM Dispatch，
  无需删除用户缓存即可连接 Multisim。

### English

- Added backward-compatible `doctor`, `serve`, and `config` CLI commands.
- Added default-side-effect-free checks for Python architecture, pywin32, COM
  registration, the local template pack, and `.ms14` codecs, with stable JSON,
  an optional strict exit code, and an explicit `--connect` activation probe.
- Added Claude Desktop JSON, Codex TOML, and generic stdio configuration
  fragments with preview-first overwrite protection.
- Redirected pywin32 import chatter away from MCP and JSON stdout.
- Added a dynamic COM Dispatch fallback for stale or corrupt pywin32 `gen_py`
  wrapper caches without deleting user data.

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
