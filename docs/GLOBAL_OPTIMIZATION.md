# 全局与多目标优化 / Global and Multi-objective Optimization

`global_optimize_design` 在一个明确、有限且可审计的设计域中联合搜索元件参数和电路
拓扑。小设计域逐项穷举；超过实验预算时采用固定种子的 Halton 低差异序列进行全域覆盖。
这是一种有限预算全局搜索，不是对非凸混合整数电路问题“绝对全局最优”的数学证明。

## 搜索维度

- `component_value`：显式值、E12/E24/E48/E96 或线性/对数连续采样；
- `topology_choice`：互斥的结构化 `PatchOperation` 方案，可增删/替换元件、增删网络、
  修改引脚连接或模型；
- 最多 16 个维度、每维 256 个选项、512 次真实实验；
- `auto`、`exhaustive` 和 `halton` 三种确定性策略。

所有 `requirements` 都是硬约束。失败、未验证、非有限测量或证据不完整的候选不会进入
可行集。最多 8 个目标支持 `minimize`、`maximize` 和 `target`，并可设置工程容差
`epsilon` 与折中权重。输出使用 epsilon-aware Pareto 支配关系，并可在 Pareto 前沿中给出
归一化加权折中建议。

下面的最小规范同时搜索 `R2` 和一个可选输出负载：

```json
{
  "schema_version": 1,
  "title": "分压器混合优化",
  "dimensions": [
    {
      "id": "divider-resistance",
      "kind": "component_value",
      "refdes": "R2",
      "values": ["1k", "2k", "4k"]
    },
    {
      "id": "output-load",
      "kind": "topology_choice",
      "include_baseline": true,
      "choices": [
        {
          "choice_id": "add-load",
          "operations": [
            {
              "operation": "add_component",
              "target": "R3",
              "before": null,
              "after": {
                "refdes": "R3", "kind": "R", "nodes": ["out", "0"],
                "value": "10k", "model": null,
                "parameters": {}, "annotations": {}
              },
              "reason": "评估带载输出"
            }
          ]
        }
      ]
    }
  ],
  "commands": "op",
  "requirements": [
    {
      "id": "vout", "metric": "mean", "signal": "V(out)",
      "operator": "between", "lower": 4.5, "upper": 8.0, "unit": "V"
    }
  ],
  "objectives": [
    {"requirement_id": "vout", "goal": "target", "target": 6.667, "weight": 1}
  ],
  "max_experiments": 8,
  "search_strategy": "auto",
  "selection_policy": "weighted_compromise"
}
```

```powershell
multisim-mcp global-optimize-design `
  --design .\design.json `
  --spec .\global-optimization.json `
  --output .\global-run `
  --json
```

长时间搜索应通过 MCP 工具 `submit_global_optimization` 提交为持久作业。恢复时只复用
manifest、验证结果和目标向量均匹配的完成候选；中断候选会写入新的 attempt 目录重新
实验，任何设计、规范、运行参数或证据不匹配都会拒绝恢复。

目录包含基线设计、规范、验证计划、所有候选补丁、每次完整实验、
`global-optimization.json`、`pareto-front.json`、CSV 和递归 SHA-256 manifest。
建议方案仍然只是候选，不会自动覆盖源设计。

## English summary

`global_optimize_design` jointly explores declared component-value and topology
dimensions. It exhausts small domains and uses deterministic Halton space filling
when the domain exceeds the experiment budget. Hard requirements gate feasibility;
finite measured objectives produce epsilon-aware Pareto fronts. The weighted
compromise is only a recommendation and every candidate remains non-persistent.
Use `submit_global_optimization` for durable execution with integrity-checked
candidate-level recovery.
