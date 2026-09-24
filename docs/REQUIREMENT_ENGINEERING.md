# 需求契约审查 / Requirement contract review

`review_design_requirements` 是进入基线实验前的只读门。它把已有电路的目标拆成：

- `hard_constraints`：必须通过的实测要求；
- `soft_objectives`：在可行解之间排序的目标；
- `preferences`：库存、成本、复杂度等偏好；
- `assumptions`：仍需使用者确认的工程假设。

工具会复用实验验收契约校验指标、信号、单位和比较运算，并检测同一测量信号上
明显互相矛盾的硬约束。它不会读取或修改电路，不会启动仿真，也不会把“契约内部一致”
解释成“电路物理可行”。

## 示例

```json
{
  "hard_constraints": [
    {
      "id": "vout-range",
      "metric": "mean",
      "signal": "V(out)",
      "operator": "between",
      "lower": 4.8,
      "upper": 5.2,
      "unit": "V"
    },
    {
      "id": "ripple-limit",
      "metric": "ripple",
      "signal": "V(out)",
      "operator": "at_most",
      "target": 20,
      "unit": "mV"
    }
  ],
  "soft_objectives": [
    {
      "id": "power",
      "metric": "power",
      "signal": "V(out)",
      "goal": "minimize",
      "weight": 2,
      "unit": "W"
    },
    {
      "id": "cost",
      "metric": "power",
      "signal": "V(out)",
      "goal": "target",
      "target": 0.5,
      "weight": 1,
      "unit": "W"
    }
  ],
  "preferences": [
    {"id": "prefer-stock", "kind": "in_stock", "weight": 1}
  ],
  "assumptions": ["输入源为理想直流源"],
  "summary": "5 V 低纹波输出，优先降低功耗"
}
```

返回 `state=ready-for-baseline` 时，只表示可以进入基线实验。若返回
`state=conflict`，应先处理 `conflicts` 中的要求；系统不会用一个“最接近”的失败候选
替代硬约束。每个冲突也会给出 `relaxation_suggestions`，表示使区间刚好相交的最小边界调整；
它们只是需求评审候选，不会自动修改电路或自动放宽约束，采纳后必须重新运行基线实验。

返回值还包含 `optimization_handoff`。当软目标与唯一一个硬测量的
`metric`、`signal`、`unit` 完全匹配时，会生成 `single_objective_candidates` 和
`multi_objective_candidates`，可作为 `optimize_design` / `global_optimize_design`
规范的起点；没有匹配或匹配多个时会进入 `unmapped_objectives`，需要人工补充测量或
绑定关系。该交接包不会启动优化，也不会改变原需求。
后续流程接收 JSON 前可调用 `validate_requirement_review` 校验 `contract_digest`；摘要不匹配
时必须退回重新审查，不能继续使用被修改的交接包。
`optimize_design` 和 `global_optimize_design` 接收该交接包后，可分别自动填充单目标或多目标
规范；`submit_design_optimization`、`submit_global_optimization` 和自主纠错入口也使用同一
规则，并把 `requirement_review_digest` 写入队列请求。冲突、未匹配或多重匹配会在仿真开始前
失败关闭。

## 与优化流程的衔接

```text
review_design_requirements
  → 运行基线实验
  → optimize_design（参数）
  → global_optimize_design（有限参数+拓扑）
  → compare_design_variants / autonomous_correct_design
  → 人工审批并应用补丁
```

当前版本仍不会自动推导完整的热、EMI、安全或器件额定值，也不会自动应用放宽建议。
后续将增加更细的不可行原因、容差/温度角落和需求到测量证据的追踪关系。

## English summary

`review_design_requirements` is a read-only pre-flight gate. It separates hard measured
constraints, soft objectives, preferences, and assumptions, validates the existing
measurement contract, and detects obvious contradictory bounds for one signal. Conflicts
include conservative minimum-relaxation candidates for human review. A clean contract is
not a proof of physical feasibility. Matching soft objectives are projected into an explicit
optimizer handoff, while ambiguous objectives remain for human review; simulation and
approval remain separate steps. The `contract_digest` can be checked before a downstream
handoff so edits made between planning and optimization fail closed.
