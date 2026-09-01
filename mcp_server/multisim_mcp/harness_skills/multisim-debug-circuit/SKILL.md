---
name: multisim-debug-circuit
description: 诊断并以最小、可回滚修改修复 Multisim 电路或仿真故障；use for invalid netlists, convergence failures, wrong waveforms, bias errors, or missing outputs
---

# 调试 Multisim 电路

本 skill 引用 Multisim MCP 原始工具名；Harness 默认前缀为 `mcp__multisim__`。

1. 保存问题描述、原始网表、分析命令、错误日志和已有实验 ID，不覆盖原始设计。
2. 调用 `runtime_status`，把安装/COM/模板故障与电路故障分开。
3. 对结构化设计先调用 `diagnose_design`；若已有同一设计的完成实验目录，把它作为
   `experiment_dir` 附加。按顺序复核：网表语法、地节点、悬空节点、短路、极性、单位、
   偏置、模型、分析命令、采样范围、饱和以及收敛条件。
4. 若只有实验 ID，先用 `get_experiment_summary`，再按需读取 `log`、`netlist`、
   `commands` 或 `report`；不要把二进制文件送入模型上下文。
5. 提出能够解释故障的最小修改，明确修改前值、修改后值、理由和回滚方法。
6. 把该最小修改写成明确 `DesignPatch`，调用 `evaluate_design_patch` 在同一硬性验收规范
   下复测原设计和内存候选。若设计带权威 `source_netlist`，只有确认需要时才显式允许
   候选内存网表再生。不要把 `adoption_eligible` 当作提交授权。
7. 检查前后诊断、两次实验和递归 manifest；失败或证据不足时保留“失败/未验证”。
8. 只有用户另行批准时，才把通过候选交给本机审批绑定的持久化流程。

输出采用“症状—证据—根因—最小修改—复测结果—剩余风险”结构。不要在没有
复测数据时宣称问题已经解决。

## English summary

Separate environment failures from circuit defects, inspect bounded evidence,
apply the smallest reversible change, rerun into a new output directory, and
compare measured results before declaring the issue fixed.
