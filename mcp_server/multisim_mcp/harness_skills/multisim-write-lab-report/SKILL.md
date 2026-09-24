---
name: multisim-write-lab-report
description: 基于已注册实验的真实产物撰写中英实验报告；use when producing a lab report, design record, reproducibility package, or formal HTML/PDF output
---

# 撰写 Multisim 实验报告

只使用已注册实验的证据。工具名为 Multisim MCP 原始名；Harness 通常添加
`mcp__multisim__` 前缀。

1. 调用 `get_experiment_summary` 和 `list_experiment_artifacts`。
2. 分页读取 `report`、`netlist`、`commands`、`data` 和 `verification` 中实际需要的
   文本。不得编造缺失测量，也不要在上下文内请求 PDF、PNG、raw 或 `.ms14`。
3. 报告包含：目的、设计要求、原理、器件与参数、分析设置、步骤、关键数据、
   理论对比、误差、验证结果、异常、局限、结论和复现产物 SHA-256。
4. 将事实、计算结果和工程判断分开。所有 PASS/FAIL 引用验证证据；不支持的指标
   标为“未验证”。
5. 用户需要正式文件时调用 `export_formal_experiment_report`；用户需要把文件复制到
   其他位置时再调用 `export_experiment_artifact`，且不得绕过批准的导出根目录。

默认中文为主并附英文摘要；用户指定英文时反向处理。报告中的本地文件引用应来自
工具返回值，不猜测路径。

## English summary

Write a traceable report from registered artifacts, measured summaries, and
verification evidence. Keep facts, calculations, and engineering judgment
separate, and use formal export tools only when requested.
