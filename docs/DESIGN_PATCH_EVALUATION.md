# 修复候选复测 / Design Patch Evaluation

`evaluate_design_patch` 把“发现问题—提出最小补丁—复测”连接成一个只读、可审计的
工程步骤。它接收一个明确的 `DesignPatch`，在内存中生成候选，然后让原设计和候选在
完全相同的分析命令与硬性验收要求下各运行一次。服务会保存前后诊断、实验、补丁和
逆补丁，但不会覆盖源设计，也不会把通过验收解释成自动提交授权。

## 适用范围

- 验证一个已经明确写出 `before`、`after` 和原因的元件值、模型、引脚、元件/网络拓扑
  或设计参数补丁；
- 判断候选是否把原设计的失败要求变为有限实测的 `pass`；
- 生成可供人工审批或后续 `patch-verify-*` 流程使用的证据包；
- 对失败、未验证、实验错误和仅修改注释的候选失败关闭。

本工具不会生成补丁，不执行脚本式任意重写，也不会持久化采用候选；它可以复测经过
严格校验的结构化拓扑补丁。参数搜索使用 `optimize_design`，混合参数/拓扑搜索使用
`global_optimize_design`，完整设计比较使用 `compare_design_variants`。

## PatchEvaluationSpec v1

```json
{
  "schema_version": 1,
  "title": "验证分压器修复候选",
  "commands": "op",
  "requirements": [
    {
      "id": "vout",
      "metric": "mean",
      "signal": "V(out)",
      "operator": "between",
      "lower": 6.5,
      "upper": 6.8,
      "unit": "V"
    }
  ],
  "theoretical_values": {"vout": 6.6666666667}
}
```

规范必须至少包含一个硬性要求。两次实验复用同一份规范；候选只有在
`overall_status=pass`、每项要求都有有限实测值时才具备采用资格。没有“最接近通过”。

## MCP

调用 `evaluate_design_patch` 时提供：

- `design`：严格版本化的 `CircuitDesign`；
- `patch`：基线 ID、修订和 `before` 值均匹配的 `DesignPatch`；
- `spec`：上述无网表的 `PatchEvaluationSpec`；
- `output_dir`：必须为空的新证据目录；
- `regenerate_source_netlist`：当设计带权威 `source_netlist` 且电气值改变时，必须显式
  设为 `true`。这只重建内存候选，不修改源文件；
- 可选 `timeout_per_experiment` 和 `max_points`。

## CLI

```powershell
multisim-mcp evaluate-design-patch `
  --design .\divider.json `
  --patch .\set-r2.json `
  --spec .\patch-evaluation-spec.json `
  --output .\patch-evaluation `
  --regenerate-source-netlist `
  --timeout 120 `
  --max-points 2000 `
  --json
```

退出码：候选具备采用资格为 `0`；候选失败、未验证或结果不确定为 `1`；输入、路径、
完整性或运行时契约错误为 `2`。

## 结果状态

- `candidate-improved-and-passed`：基线未通过，候选全部通过；
- `candidate-passed`：基线和候选都通过，只能证明候选仍满足已给要求；
- `candidate-failed-requirements`：候选存在明确失败；
- `candidate-unverified`：候选证据不足；
- `inconclusive`：候选实验错误或没有可用的完成证据。

`adoption_eligible=true` 只会出现在前两种状态。结果始终包含
`source_design_modified=false`、`candidate_persisted_as_source=false` 和
`approval_required_before_apply=true`。

## 证据目录

输出目录包含：

- `source-design.json`、`candidate-design.json`；
- `patch.json`、`inverse-patch.json`；
- `verification-plan.json`；
- `comparison/`：两次完整实验及其比较清单；
- `diagnosis-before.json`、`diagnosis-after.json`；
- `evaluation.json`：状态、采用资格和新增/已解决 finding；
- `directory.manifest.json`：递归绑定全部文件的 SHA-256 清单。

读取结果时会递归验证清单；候选、验收或嵌套实验被改写后，不再接受该证据包。

## 安全边界

1. 在创建输出目录前验证设计、补丁、候选网表和两次实验规范；
2. 只允许一个明确补丁和固定两次实验，不形成无界自主修改循环；
3. 电气网表没有变化的补丁拒绝复测；
4. 权威源网表可能陈旧时必须显式允许只在内存中重建；
5. 诊断差异是确定性规则的变化，不是对全部电气行为的证明；
6. 采用候选必须另走本机审批与验收绑定事务。

## English summary

`evaluate_design_patch` runs exactly two verified experiments—the unchanged
baseline and one explicit in-memory `DesignPatch` candidate—under the same hard
requirements. It retains both designs, the patch and inverse, full experiment
evidence, before/after deterministic diagnoses, and a recursive integrity
manifest. A candidate is adoption-eligible only with finite measured all-pass
evidence, and it is never persisted or treated as approved automatically.
