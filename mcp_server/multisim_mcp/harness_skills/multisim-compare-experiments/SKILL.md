---
name: multisim-compare-experiments
description: 比较两个已注册 Multisim 实验的电路、设置、测量、验证和产物；use when evaluating a baseline against a candidate or explaining changed simulation results
---

# 比较 Multisim 实验

需要两个已注册实验 ID。工具名为 Multisim MCP 原始名；Harness 通常添加
`mcp__multisim__` 前缀。

1. 分别调用 `get_experiment_summary`，确认两个 ID 可用并记录产物哈希。
2. 按需调用 `read_experiment_artifact` 分页读取 `netlist`、`commands`、`report`、
   `verification` 或 `data`。只读取解释差异所需的最小内容。
3. 比较电路拓扑与参数、分析命令、采样点、关键列的首值/末值/最小值/最大值/均值、
   验证状态和产物 SHA-256。
4. 区分设计变化、求解设置变化、采样差异和证据缺失。不要把相关性写成因果关系。
5. 如果两次实验的分析条件不可比，明确标记“不可直接比较”，并说明需要怎样的复测。

最终给出紧凑表格，至少包含指标、实验 A、实验 B、差值/变化率、证据和结论，随后
列出最可能原因与仍未验证的假设。除非用户明确要求，不导出二进制产物。

## English summary

Compare two registered experiments using bounded summaries and only the text
pages needed to explain topology, settings, measurements, verdicts, and hashes.
Flag incompatible test conditions instead of forcing a conclusion.
