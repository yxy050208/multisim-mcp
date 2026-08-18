---
name: multisim-debug-circuit
description: 诊断并以最小、可回滚修改修复 Multisim 电路或仿真故障；use for invalid netlists, convergence failures, wrong waveforms, bias errors, or missing outputs
---

# 调试 Multisim 电路

本 skill 引用 Multisim MCP 原始工具名；Harness 默认前缀为 `mcp__multisim__`。

1. 保存问题描述、原始网表、分析命令、错误日志和已有实验 ID，不覆盖原始设计。
2. 调用 `runtime_status`，把安装/COM/模板故障与电路故障分开。
3. 按顺序检查：网表语法、地节点、悬空节点、短路、极性、单位、偏置、模型、
   分析命令、采样范围、饱和以及收敛条件。
4. 若有实验 ID，先用 `get_experiment_summary`，再按需读取 `log`、`netlist`、
   `commands` 或 `report`；不要把二进制文件送入模型上下文。
5. 提出能够解释故障的最小修改，明确修改前值、修改后值、理由和回滚方法。
6. 在新的输出目录重新运行，禁止启用 `do_command_line` 或不安全命令模式。
7. 使用 `get_experiment_summary` 比较修复前后测量；若证据仍不足，保留“未验证”。

输出采用“症状—证据—根因—最小修改—复测结果—剩余风险”结构。不要在没有
复测数据时宣称问题已经解决。

## English summary

Separate environment failures from circuit defects, inspect bounded evidence,
apply the smallest reversible change, rerun into a new output directory, and
compare measured results before declaring the issue fixed.
