# 完整设计版本比较 / Complete Design Comparison

`compare_design_variants` 用相同实验条件比较多个完整 `CircuitDesign`，适合比较元件值
方案、不同电路拓扑或独立工程版本。它是只读评估器：会运行 Multisim 并写入审计产物，
但不会修改输入对象、覆盖设计文件或自动采纳第一名。

## 安全与确定性边界

- 每次比较必须提供 2–16 个有稳定标识的完整设计；按输入顺序执行，同分时先输入者优先。
- 所有设计在创建输出目录前完成严格解析和 SPICE 编译；电气网表完全相同的重复版本拒绝。
- 所有版本共用命令、验收要求、理论值和单一目标，避免为某个版本改变评分标准。
- 每项验收要求都是硬约束。只有 `overall_status: pass`，且每项要求和目标都有有限实测值
  的版本才进入排名；`fail`、`unverified`、证据不完整和实验错误不会被当作“接近通过”。
- 某一版本失败不会掩盖后续独立证据；成功结果可明确返回 `ranked_with_errors`。
- 第一名仅是建议，`requires_manual_adoption: true`；本工作流没有设计写入能力。

## ComparisonSpec v1

```json
{
  "schema_version": 1,
  "title": "比较分压器设计",
  "commands": "op",
  "requirements": [
    {
      "id": "vout",
      "metric": "mean",
      "signal": "V(out)",
      "operator": "between",
      "lower": 3.0,
      "upper": 8.0,
      "unit": "V"
    }
  ],
  "theoretical_values": {"vout": 6.6666666667},
  "objective": {
    "requirement_id": "vout",
    "goal": "target",
    "target": 6.6666666667
  }
}
```

`goal` 支持 `minimize`、`maximize`、`target`。目标必须引用一个硬约束；第一版不支持
加权总分或 Pareto 前沿。

## CLI

每个 `--variant` 使用 `ID=DESIGN_JSON`，重复 2–16 次：

```powershell
multisim-mcp compare-designs `
  --variant low=.\low.json `
  --variant balanced=.\balanced.json `
  --variant target=.\target.json `
  --spec .\comparison-spec.json `
  --output .\comparison-result `
  --timeout 120 `
  --max-points 2000 `
  --json
```

退出码：有可行排名为 `0`；完成但没有可行版本或正常取消为 `1`；输入、路径或运行时
契约错误为 `2`。

## MCP

`compare_design_variants` 的 `variants` 是以下对象的有序数组：

```json
[
  {"variant_id": "low", "design": {"schema_version": 1, "design_id": "..."}},
  {"variant_id": "target", "design": {"schema_version": 1, "design_id": "..."}}
]
```

还需传入完整 `spec`、新的 `output_dir`，以及可选的单次实验超时和最大导出点数。工具
位于 `optimization` 和 `full` profile。

## 状态与产物

最终状态包括：

- `ranked`：至少一个可行版本，所有已计划实验正常完成；
- `ranked_with_errors`：仍得到可信排名，但一个或多个独立版本实验出错；
- `no_feasible_variant`：没有满足全部硬约束且目标可测的版本；
- `cancelled`：协作取消，部分结果不能宣称为最终排名。

输出目录包含：

- `comparison.json`：状态、输入摘要、逐版本判定和排名；
- `comparison-spec.json`、`verification-plan.json`：规范化评分与验收契约；
- `variants/<id>.json`：每个输入设计的不可变快照；
- `experiments/<id>/`：每个版本的完整实验、验证、数据和报告；
- `variants.csv`：便于人工审查和数据分析的扁平结果；
- `directory.manifest.json`：递归绑定上述文件大小与 SHA-256。

读取完成结果时使用 `read_design_comparison(..., verify=True)`；任何嵌套设计、验证文件、
实验 manifest 或汇总被修改都会导致验证失败。

## 中断与限制

当前执行是同步的。正常取消会形成完整 `cancelled` manifest；进程强制终止可能留下
`state: running` 的部分目录，不支持原地续跑，需保留证据并在新空目录重跑。第一版只
比较提供的完整设计，不自动生成拓扑、不计算成本/BOM 供应风险，也不自动提交第一名。

## 真实 Multisim 门禁

可选测试 `tests.test_real_design_comparison` 在 Multisim 14.3 中依次运行 500 Ω、1 kΩ、
2 kΩ 三个 10 V 分压器完整设计。三者通过共同电压范围后，以 6.6667 V 为目标，2 kΩ
版本稳定排名第一；全部输入设计保持逐字段不变。

## English summary

`compare_design_variants` evaluates 2–16 complete `CircuitDesign` variants,
including different topologies, under one command, hard-constraint, and
single-objective contract. All designs are validated and compiled before output
creation. Only finite measured all-pass evidence is ranked; failed, unverified,
malformed, and errored variants are never promoted. Execution order and tie
breaking are deterministic, input designs are immutable, and the selected
variant always requires manual adoption. The output retains normalized contracts,
design snapshots, complete experiments, a flat CSV, and a recursive SHA-256
manifest. Forced termination is not resumable in place in v1.
