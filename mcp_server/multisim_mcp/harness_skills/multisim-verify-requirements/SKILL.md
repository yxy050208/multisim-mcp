---
name: multisim-verify-requirements
description: 将电路设计要求转成可计算判据并依据实验数据给出 PASS、FAIL 或未验证；use for acceptance testing, tolerance checks, and evidence-backed design review
---

# 验证 Multisim 设计要求

需要实验 ID 和设计要求。工具名为 Multisim MCP 原始名；Harness 通常添加
`mcp__multisim__` 前缀。

1. 调用 `get_experiment_summary`，确认测量列、分析类型和现有验证结果。
2. 把每一项要求转换为独立、稳定的 requirement ID、测量 metric、signal、operator、
   target 或上下界、容差和单位。不得把定性目标伪装成可测量数值。
   周期波形优先使用 `frequency`；失真要求使用 `thd`，并通过 `start_x` / `end_x`
   排除启动过程。噪声边沿应显式设置 `threshold`、`hysteresis` 和 `min_cycles`。
3. 调用 `verify_experiment_requirements`；有理论值时通过 `theoretical_values` 提供，
   不从模型猜测理论值。
4. 对每项列出原要求、判据、实测值、目标、容差、状态和证据。工具返回
   `unverified` 时保持该状态，不以最接近值代替通过。
5. 若失败源于测量列或分析设置不足，提出最小复测计划；除非用户授权，不自动覆盖
   原实验或修改电路。

结论必须同时给出整体状态和 pass/fail/unverified 计数。任何无法从数据证明的安全、
可靠性、热或可制造性声明都不属于仿真通过项。

## English summary

Translate each requirement into an explicit measurable criterion, call the
verification tool, preserve unverified outcomes, and propose a minimal rerun
when the existing analysis cannot supply the required evidence.
