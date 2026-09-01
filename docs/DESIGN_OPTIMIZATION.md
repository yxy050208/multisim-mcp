# 有预算的参数优化 / Budgeted design optimization

`optimize_design` 是阶段 B 的第一版参数优化闭环；较长搜索可改用
`submit_design_optimization` 持久任务。它们对一个固定的
`CircuitDesign` 依次运行基线和有限个元件值候选，使用真实实验结果检查硬约束，
再按一个确定性目标排序。优化过程不会修改输入设计、`.ms14` 或任何工程源文件；
如果最优解是候选，只输出一个仍需人工审批的 `DesignPatch`。

## 当前边界

- 只支持 `set_component_value`，不改网络、模型、引脚或拓扑；
- 最多 4 个变量，每个变量最多 32 个显式标量值，或由 E12/E24/E48/E96 范围生成的值；
- 最多 32 次实验，基线固定占用 1 次；
- 候选按变量和值在 JSON 中的顺序执行，笛卡尔积和同分排序均确定；
- `requirements` 中每一项都是硬约束，只有 `overall_status == "pass"` 且目标测量
  有限可用的结果才进入可行解排名；
- 目标支持 `minimize`、`maximize` 和到指定值的 `target`；
- 找不到可行解时返回 `no_feasible_candidate`，不会把“最接近”的失败候选伪装成通过；
- 如果基线满足约束且目标最好，返回 `baseline_best`，不生成无意义补丁；
- 携带权威 `source_netlist` 的设计会为每个内存候选安全再生网表，避免用陈旧输入仿真。
- 可为每个值绑定料号、供应商、库存和单价；缺货、缺少必要库存记录或超过变量合计
  单价预算时，候选即使仿真通过也会标为 `procurement_fail`；
- `prefer_lower_cost` 只在仿真目标分数相同的可行方案之间作为确定性次级排序，不会用
  便宜但不满足电气目标的料号替代通过方案。

这仍不是连续优化器或自动拓扑综合器。当前采购成本仅统计优化变量，不代表完整 BOM、
阶梯价格、运费或实时供应商报价；Pareto 多目标仍在后续范围内。

## OptimizationSpec v1

下面的例子从 E24 的 1.8–2.2 kΩ 范围生成候选，并把库存和变量单价作为额外工程约束。

```json
{
  "schema_version": 1,
  "title": "优化分压器输出",
  "variables": [
    {
      "refdes": "R2",
      "series": {"name": "E24", "minimum": "1.8k", "maximum": "2.2k"},
      "inventory": [
        {"value": "1k", "part_number": "R-1K", "supplier": "demo", "unit_cost": 0.01, "stock": 0},
        {"value": "1.8k", "part_number": "R-1K8", "supplier": "demo", "unit_cost": 0.03, "stock": 20},
        {"value": "2k", "part_number": "R-2K", "supplier": "demo", "unit_cost": 0.04, "stock": 20},
        {"value": "2.2k", "part_number": "R-2K2", "supplier": "demo", "unit_cost": 0.08, "stock": 20}
      ]
    }
  ],
  "commands": "op",
  "requirements": [
    {
      "id": "divider-output",
      "metric": "mean",
      "signal": "V(out)",
      "operator": "between",
      "lower": 4.9,
      "upper": 8.1,
      "unit": "V"
    }
  ],
  "theoretical_values": {"divider-output": 6.6666666667},
  "objective": {
    "requirement_id": "divider-output",
    "goal": "target",
    "target": 6.6666666667
  },
  "max_experiments": 4,
  "procurement": {
    "currency": "CNY",
    "require_in_stock": true,
    "max_total_unit_cost": 0.05,
    "prefer_lower_cost": true
  }
}
```

`objective.requirement_id` 必须引用一个硬约束，这保证排名指标也来自已经请求和记录的
验收测量。候选值必须是单个标量 token，例如 `1000`、`1k`、`2.2u`；表达式、指令、
换行和任意模型文本会在实验前被拒绝。`series` 与 `values` 必须二选一；E 系列只用于
R/C/L，并使用标准 SPICE 工程后缀（`k`、`Meg`、`m`、`u`、`n`、`p`、`f`）。生成范围
包含上下界，但如果产生超过 32 个值，必须缩小范围。`1k` 与 `1000` 会视为同一值。

每条 `inventory` 记录必须包含 `value`、`part_number`、非负 `unit_cost` 和非负整数
`stock`，`supplier` 可选。当启用在库、最高成本或低成本次级排序时，候选所选的每个变量
都必须能匹配库存记录。`max_total_unit_cost` 是优化变量的单件成本之和，币种仅作明确
记录，不执行汇率换算或联网询价。

## 本机 CLI

```powershell
multisim-mcp optimize-design `
  --design .\design.json `
  --spec .\optimization-spec.json `
  --output .\optimization-run-001 `
  --timeout 120 `
  --max-points 2000 `
  --json
```

首次运行的输出目录必须是新的或为空，优化器不会覆盖既有证据。如果同一目录留下了
匹配的中断检查点，可在完全相同的设计、规范、超时和点数下增加 `--resume`。恢复过程会
重新验证已完成候选的实验 manifest、验收文件、目标值和采购证据；不一致时失败关闭。

```powershell
multisim-mcp optimize-design `
  --design .\design.json `
  --spec .\optimization-spec.json `
  --output .\optimization-run-001 `
  --timeout 120 `
  --max-points 2000 `
  --resume `
  --json
```

退出码含义：

- `0`：得到 `optimized` 或 `baseline_best`；
- `1`：流程正常完成，但没有可行解或被取消；
- `2`：输入、运行环境或系统契约错误。

MCP 客户端可调用同步 `optimize_design`，或为较长搜索调用
`submit_design_optimization`。持久提交立即返回 `job_id`，随后复用
`get_experiment_job`、`list_experiment_jobs`、`cancel_experiment_job`、
`retry_experiment_job` 和 `multisim://jobs/{job_id}` 查询、取消与重试。MCP 前端在任务
运行时退出后，下一进程会安全重新排队；worker 崩溃、任务超时或显式取消会形成终态，
需要调用 `retry_experiment_job`。若任务记录已经丢失但保留了可信检查点，可显式设置
`resume_existing=true` 采用匹配目录。两种优化工具都属于 `optimization` 与 `full`
Profile，都会运行 Multisim 并写证据，但不会持久化设计修改。

## 产物与可审计性

每个完成的目录至少包含：

- `baseline-design.json`：输入设计快照；
- `optimization-spec.json`：规范化后的搜索、E 系列、库存、采购约束、目标和预算；
- `verification-plan.json`：可直接用于后续验收补丁流程的无网表计划；
- `patches/candidate-NNN.json`：每个实际测试候选的可逆补丁；
- `experiments/baseline/` 与 `experiments/candidate-NNN/`：完整实验及各自 manifest；
- `experiments/*-attempt-NNN/`：仅在某候选未提交即中断时使用的新恢复尝试目录；旧现场
  不删除、不覆盖；
- `optimization.json`：进度、每次成功/失败、目标值、采购判定、停止原因和排名；
- `candidates.csv`：便于表格和可视化使用的扁平比较；
- `best-patch.json`：仅在候选优于基线时生成；
- `directory.manifest.json`：覆盖上述文件和所有嵌套实验产物的大小与 SHA-256。

单个候选的仿真或证据错误会记录为 `error` 并继续消耗预算；它不会进入可行集。
`stop_reason` 明确区分 `candidate_space_exhausted`、`budget_exhausted` 和
`cancellation_requested`。`read_design_optimization(..., verify=True)` 会验证整个目录，
任一已登记文件被替换、截断或篡改都会失败关闭。

`optimization.json` 额外记录每个候选的 `attempt`、中断尝试、`resume_count`、唯一候选
数量 `experiments_attempted` 和包含重试的 `experiment_attempt_count`。恢复只复用已经
原子提交且能重新通过证据校验的候选；处于 `running` / `interrupted` 的候选会在新 attempt
目录中重跑。设计摘要、规范摘要或运行限制不同均拒绝恢复，避免把不同搜索混在一起。

## 将最优候选提交到设计

优化器永远不会自动提交补丁。审查 `best-patch.json` 后，使用输出的
`verification-plan.json` 进入现有审批绑定闭环：

```powershell
multisim-mcp patch-verify-approve `
  --design .\design.json `
  --patch .\optimization-run-001\best-patch.json `
  --in-place `
  --receipt .\apply-receipt.json `
  --regenerate-source-netlist `
  --verification-plan .\optimization-run-001\verification-plan.json `
  --experiment-output .\selected-candidate-verification `
  --workflow-manifest .\selected-candidate-workflow.json `
  --token-output .\approval.token
```

然后用同一组路径运行 `patch-verify-apply --approval-token-file .\approval.token`。
这会再次验证所选候选，并且只有全部硬约束通过才事务化写入设计。完整审批参数与恢复
方式见 [DesignPatch 事务指南](DESIGN_PATCH_TRANSACTIONS.md)。

真实门禁已在 Multisim 14.3 上从 E24 的 1.8 kΩ、2 kΩ、2.2 kΩ 运行候选和基线；
缺货基线与超预算 2.2 kΩ 被采购硬约束排除，在不修改源设计的前提下稳定选出有库存的
2 kΩ 料号及约 6.667 V 的电气结果。

## English summary

`optimize_design` evaluates a fixed `CircuitDesign`, its baseline, and a
deterministic Cartesian product of explicit or E12/E24/E48/E96 component values under a hard
experiment budget. Every verification requirement is a hard constraint; failed
or unverified evidence is never ranked as feasible. A single measured
requirement supplies a minimize, maximize, or target objective. The baseline is
ranked too, so the correct result may be `baseline_best` with no patch.

The bounded surface allows at most four variables, 32 values per variable, and 32
experiments including the baseline. It never edits topology or the source
design. Optional inventory records bind values to part numbers, suppliers,
non-negative stock, and unit cost. In-stock and maximum variable-cost constraints
are hard constraints; lower cost is only a deterministic tie-break after the
measured electrical objective. It writes normalized inputs, every tested patch, complete experiment
evidence, a flat CSV, stopping reasons, and a recursive SHA-256 directory
manifest. A selected `best-patch.json` remains untrusted for persistence until
the user separately approves and runs the existing verified-patch transaction.

Long searches can use `submit_design_optimization`. It reuses the durable job
query/cancel/retry tools and persists a candidate-level checkpoint. After an MCP
restart, completed candidates are reused only after their experiment manifests,
verification data, objective, and procurement evidence are revalidated. An
uncommitted candidate is rerun in a new attempt directory without overwriting
the interrupted evidence. The local CLI exposes the same guarded behavior via
`optimize-design --resume`.

The real Multisim 14.3 gate evaluates a 10 V divider baseline plus three E24
candidates. It excludes an out-of-stock baseline and over-budget 2.2 kOhm part,
then selects the stocked 2 kOhm part at approximately 6.667 V while leaving the
input design unchanged.
