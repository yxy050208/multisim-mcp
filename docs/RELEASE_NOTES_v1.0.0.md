# Multisim MCP v1.0.0

这是第一个稳定版：AI Agent 现在可以从受限 SPICE/实验规范生成可编辑 Multisim 电路，
运行真实仿真，验证设计指标，执行批量扫描，并导出电路图、数据与中英双语正式报告。

## 主要变化

- MCP Python SDK 2.x，兼容现代与旧版 stdio 协议。
- 持久、可取消的隔离实验任务，支持超时、崩溃/无响应恢复和跨进程输出锁。
- `ExperimentSpec`、13 类测量、严格 PASS/FAIL/未验证结论，以及理论/仿真误差。
- 参数、容差、温度和可复现 Monte Carlo 扫描，每次最多 100 个运行点。
- 13 个不携带 NI 数据库资产的便携元件适配器，以及严格声明式社区 JSON 接口。
- 数据万用表、Bode Plotter、Logic Analyzer 和复数 AC 相位解析。
- Markdown、CSV、SVG、原理图 PNG、中英双语 HTML/PDF 与 SHA-256 复现清单。
- 51 个 MCP 工具、19 个 Resource 模板和 5 个中英双语 Prompt。
- schema 2 本地模板包使用当前安装的 Multisim 创建空白工程骨架；`doctor` 会拒绝旧版
  schema 1 包，防止跨版本骨架导致元件静默丢失。

## 安装

真实 Multisim 自动化要求 Windows、已授权的 Multisim 14+ 和 32 位 Python 3.10+：

```powershell
C:\path\to\python32\python.exe -m pip install "multisim-mcp==1.0.0"
C:\path\to\python32\Scripts\multisim-mcp.exe doctor --lang zh --connect
```

Linux/Docker 仅用于 MCP introspection 和目录健康检查，不能运行 Multisim。公开包不包含
NI 软件、样例、解码电路或从本地安装提取的 XML 模板。

## 验证证据

- 116 项无 COM 测试在 32/64 位 Python 环境通过。
- 现代/旧 MCP 握手均通过，服务公开 51 tools / 19 resources / 5 prompts。
- wheel/sdist 内容审计确认只有项目代码、许可证和来源 manifest，没有 NI 派生资产。
- Multisim 14.3 真实回归覆盖全部 13 个适配器的打开/回导，以及电位器 DC、变压器瞬态、
  继电器工作点、晶振复数 AC、功率器件工作点、DFF 瞬态和双语报告事务。

## 升级和限制

从 alpha 升级请阅读 [迁移指南](MIGRATION_TO_1.0.md)，故障处理见
[恢复指南](RECOVERY.md)，具体元件证据等级见 [兼容性矩阵](COMPATIBILITY.md)。
自动生成的探针仍不是权威数据源；实验数据来自同一安全网表经 Multisim 命令引擎执行的结果。

## English summary

Version 1.0 is the first stable release of the complete constrained-netlist to
editable-schematic, real-simulation, design-verification, sweep, data-export,
and bilingual-report workflow. It adds resilient durable jobs, portable
component adapters, virtual instruments, reproducibility manifests, MCP 2
compatibility, and verified code-only packaging. Real automation remains local
to licensed Multisim on Windows with 32-bit Python.
