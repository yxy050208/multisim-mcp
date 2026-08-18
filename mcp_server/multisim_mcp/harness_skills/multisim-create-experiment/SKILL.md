---
name: multisim-create-experiment
description: 根据工程要求生成安全、可复现的 Multisim 电路实验并核对理论与仿真结果；use when creating a new circuit, schematic, simulation, or experiment report
---

# 创建 Multisim 电路实验

本 skill 使用 Multisim MCP 工具。下文写原始工具名；DeepSeek Harness 默认会将其
显示为 `mcp__multisim__<工具名>`，如果配置使用了不同 `serverName`，请采用实际前缀。

1. 收集电路功能、输入输出、供电、频率、容差、分析类型、输出目录和验收指标。
   对会改变设计结论的缺失条件先向用户确认。
2. 调用 `runtime_status`。完整工作流未就绪时，报告具体缺口，不要反复尝试 COM。
3. 以 SPICE 网表作为源数据。只使用受支持元件和 `op`、`dc`、`ac`、`tran` 分析；
   禁止任意命令、外部文件模型和来源不明的器件模型。
4. 有明确验收指标时优先调用 `run_verified_circuit_experiment`；普通实验调用
   `run_circuit_experiment`，长任务调用 `submit_circuit_experiment` 并查询任务状态。
5. 成功后调用 `get_experiment_summary` 和 `list_experiment_artifacts`。只有需要更多
   文本证据时才分页调用 `read_experiment_artifact`，不要读取二进制内容。
6. 核对理论值、仿真测量、容差和验证结论。任何缺少数据支持的指标标记为“未验证”。
7. 仅在用户要求导出时调用 `export_experiment_artifact`；导出目录必须已由服务端批准。

最终答复应列出电路假设、网表/分析、关键测量、PASS/FAIL/未验证结论和产物清单。
不要把“最接近要求”写成“通过”，也不要声称自动生成的设计已可直接生产。

## English summary

Create a reproducible experiment from explicit requirements, prefer verified or
durable high-level tools, inspect bounded artifact summaries, and report only
claims supported by simulation evidence.
