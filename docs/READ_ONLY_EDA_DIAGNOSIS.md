# 只读 EDA 模型诊断 / Read-only EDA Model Diagnosis

`multisim-mcp model-diagnose` 把一个固定、已验证的电路设计交给有界模型工具循环，
用于结构梳理、连接检查和报告建议。该入口与普通 `model` 命令分离：只有显式调用
`model-diagnose` 并指定设计输入时，四个只读设计工具才会出现；显式指定一个已完成
实验目录时，再附加四个只读实验证据工具。
显式授权补丁预览时，还会增加一个只在内存候选上工作的 `DesignPatch` 工具。

## 快速使用

Provider 配置完成后，可以直接分析安全 SPICE 网表：

```powershell
multisim-mcp model-diagnose `
  --input .\diagnosis-prompt.txt `
  --netlist .\circuit.cir `
  --experiment-dir .\completed-experiment `
  --enable-patch-preview `
  --provider deepseek `
  --max-rounds 8 `
  --max-tool-calls 16 `
  --audit-output .\agent-audit.json `
  --json
```

也可以输入版本化 `CircuitDesign` JSON：

```powershell
Get-Content -Raw -Encoding utf8 .\diagnosis-prompt.txt |
  multisim-mcp model-diagnose `
    --stdin `
    --design .\design.json `
    --json
```

`--design` 与 `--netlist` 必须且只能选择一个。`--experiment-dir` 可省略；指定后只读取
现有完整实验产物，不创建实验、不执行网表、不启动 Multisim。提示词仍只允许来自显式
stdin 或 UTF-8 文件，没有内联 `--prompt` 参数。

## 固定的四个设计工具

| 工具 | 作用 | 明确不做的事 |
| --- | --- | --- |
| `eda_get_design_summary` | 返回设计标识、版本、元件/网络/连接数量和有界类型统计 | 不返回网表正文、注释或路径 |
| `eda_list_components` | 按偏移量分页列出最多 20 个元件，可按类型过滤 | 不返回任意文件、完整 annotations/parameters |
| `eda_inspect_net` | 检查一个精确网络名的元件引脚连接，最多返回 100 项 | 不启动 Multisim、不测量电压 |
| `eda_run_structural_checks` | 检查未使用/单连接网络、参考地和模型溯源元数据 | 不执行仿真、ERC 或正确性证明 |

工具绑定捕获一个不可变 `CircuitDesign`，参数中没有文件路径、命令、后端名称或写入
目标。普通 `multisim-mcp model` 仍然不公开任何工具。

## 可选的四个实验证据工具

只有显式传入 `--experiment-dir` 时，CLI 才会先在本机注册并汇总该已完成实验，然后把
经过二次白名单过滤的内存快照绑定到以下工具：

| 工具 | 作用 | 明确不公开的内容 |
| --- | --- | --- |
| `eda_get_experiment_summary` | 返回点数、列数、验收总状态/计数及产物总量 | 报告正文、路径、原始样本 |
| `eda_list_measurement_columns` | 分页返回最多 20 列的 first/last/min/max/mean 统计 | 完整 CSV/raw 数据和任意采样窗口 |
| `eda_list_requirement_results` | 分页返回最多 20 项实测值、单位、阈值、PASS/FAIL/未验证及误差 | 完整 `verification.json` 和嵌套私有细节 |
| `eda_list_experiment_artifacts` | 分页返回逻辑名、MIME、大小和 SHA-256 | 产物内容、导出动作和本机文件名路径 |

快照最多保留 64 个测量列摘要、25 个验收摘要和 32 个产物元数据。handler 不持有实验
目录或资源注册表引用，因此模型无法用工具参数改变路径或读取其他文件。`experiment_id`
是路径派生的不透明句柄，不包含路径正文。

当前 CLI 只接受用户对“设计 + 实验”的显式配对，不会自动验证实验网表、manifest 与
输入 `CircuitDesign` 是否同源；所有工具和 CLI JSON 均返回
`design_association_verified=false`。诊断结论必须把这种来源限制写清楚。

## 可选的补丁预览工具

`--enable-patch-preview` 显式增加 `eda_preview_design_patch`。模型提交完整、版本化
`DesignPatch` 后，工具执行以下确定性门禁：

1. `design_id` 和 `base_revision` 必须与固定设计完全一致；
2. 最多 64 项操作；可修改值、模型和引脚，可增删/替换元件及网络，也可修改设计参数
   和注释；拓扑操作必须使用显式结构化 `before` / `after`，不能提交任意脚本；
3. 每个目标只能出现一次，元件必须存在，每项 `before` 必须等于当前值；
4. 只在内存中构造修订号 +1 的候选设计，并生成顺序反转的显式逆补丁；
5. 对基线与候选运行同一组确定性结构检查，返回新增/消除的诊断计数。

工具结果及 `--json` 的 `patch_preview.previews[]` 会明确返回：

- `original_design_unchanged=true`、`persisted=false`、`backend_called=false`；
- `simulation_performed=false`、`electrical_correctness_proven=false`；
- `approval_required_before_apply=true`；
- 当电气或拓扑变化会使已有权威源网表陈旧时，
  `source_netlist_update_required=true`。

所以“预览有效”只表示 schema、基线、目标、可逆性和内存对象验证通过，不代表参数能
改善指标，更不代表补丁已经写盘、应用、仿真或审批。普通 `model-diagnose` 默认仍不
公开此工具；该命令没有任何 `apply` 参数或隐藏写入路径。人工审查后如需持久化，必须
转到独立本机 CLI 的短期审批和事务命令，见
[`DesignPatch 审批、应用与撤销`](DESIGN_PATCH_TRANSACTIONS.md)。

## 输入门禁

- `CircuitDesign` JSON 最大 8 MiB，要求 UTF-8、唯一字段名、有限数字、严格
  `schema_version=1`，未知字段会被拒绝；
- SPICE 文件最大 8 MiB 且正文不超过 4,000,000 字符；它只经现有安全解析器转换，
  不会执行；
- `.include`、`.lib`、`.control`、shell、文件读写等危险记录在联系模型前失败；
- 原始 `source_netlist`、设计 annotations 和模型来源路径不会进入工具结果；
- 单页元件/证据数、单元件节点数、网络连接数、诊断数和总工具结果大小都有固定上限。

## 数据与隐私边界

该命令会向显式选择的 Provider 发送提示词、系统约束、四个设计工具 schema，以及
模型按需请求的有界工具结果。附加实验时还会发送四个证据工具 schema；列名、统计值、
验收判定、阈值、误差、产物名和哈希可能离开本机。虽然原始网表、报告、波形样本和路径
不会直接发送，但元件名称、数值、模型名、网络名和拓扑连接也可能通过工具结果离开
本机；模型可以在工具调用上限内请求多个分页。
因此不要把“`source_netlist_exposed=false`”理解为零数据外传。分析专有设计前，应确认
Provider 的数据政策，或选择本地 Ollama。

补丁预览参数会把目标、前后值、理由和 metadata 发送给 Provider；它们也会作为已验证
工具参数写入显式审计文件。与其他工程数据一样，不应把包含专有参数的 CLI JSON 或
审计文件默认提交到公开仓库。完整补丁工具结果不会进入审计，只记录大小和 SHA-256。

完整 transcript 不写入 CLI JSON，只返回最终响应、轮次、调用数、Provider 序列和
用量汇总。Provider 仍可能按其自身政策记录请求。

需要复盘模型行为时，可以显式增加 `--audit-output`。版本化审计 JSON 会记录：

- 模型轮次、实际 Provider/模型、finish reason 和 token 用量；
- 工具调用 ID、工具名和通过独立验证后的参数；
- 工具结果的字符数、UTF-8 字节数和 SHA-256，而不是结果正文；
- 运行成功/失败、总轮次、总工具数和时间信息。

审计不会保存提示词、模型回答、reasoning、API Key、完整工具结果、原始网表或输入
文件路径。目标已存在时默认失败；确认替换时才传 `--audit-overwrite`。工具参数仍可能
包含网络名等设计信息，因此审计文件应按本地工程数据管理，不应默认提交到 Git。

## 成本、失败回退与取消

一次诊断可能包含多个模型轮次。默认最多 8 轮和 16 次工具调用，硬上限分别是 16 和
64。每一轮都可能产生 token 用量。Provider fallback 仍需同时给出顺序与显式授权：

```powershell
multisim-mcp model-diagnose `
  --input .\prompt.txt `
  --netlist .\circuit.cir `
  --provider deepseek `
  --fallback local-ollama `
  --allow-failover
```

只有网络错误、HTTP 408/409/429 或 5xx 才会进入 fallback。同一 Provider 不自动重试；
网络失败发生在服务端已经处理请求之后时，fallback 仍可能造成重复计费。Ctrl+C 会在
模型与工具边界取消调用；所有只读 handler 也会检查取消令牌。

## 库级使用

应用层可以自行构建绑定，而不使用 CLI：

```python
from multisim_mcp.agent_runtime import BoundedToolLoop
from multisim_mcp.eda_agent_tools import create_readonly_eda_bindings
from multisim_mcp.experiment_agent_tools import ReadOnlyExperimentEvidence
from multisim_mcp.experiment_resources import summarize_experiment
from multisim_mcp.design_patch_tools import ReadOnlyDesignPatchPreview

bindings = create_readonly_eda_bindings(design)
evidence = ReadOnlyExperimentEvidence(summarize_experiment(experiment_id))
bindings += evidence.bindings()
patch_preview = ReadOnlyDesignPatchPreview(design)
bindings += patch_preview.bindings()
loop = BoundedToolLoop(registry, bindings, max_rounds=8, max_tool_calls=16)
```

工厂不接受 `EdaBackend`，因此不会因为注册了 Multisim 后端就隐式启动 COM。以后增加
后端诊断时，也应通过独立白名单和显式授权接入。

## 当前限制

没有附加实验时，结构性诊断仍只能指出值得检查的拓扑特征。附加实验后可以引用现有
列统计和确定性验收结果，但不能查询任意原始波形窗口，也不能证明实验与设计同源、
替代实物测量或把 `unverified` 当作通过。模型直接写设计仍不在该入口的权限内。
独立审批和事务 CLI 已可应用一个精确绑定、未过期的补丁并生成回执，也可通过新的独立
审批应用逆补丁；强制终止后可用持久化 journal 和 `patch-recover` 复核并恢复。
`model-diagnose` 不会自动运行实验。另一个明确的本机 CLI 闭环
`patch-verify-approve` → `patch-verify-apply` 已可把验收计划绑定到审批，对内存候选运行
真实 Multisim，并仅在全部要求通过时持久化；其工作流清单会交叉记录实验和补丁证据。
模型工具本身仍然没有获得审批、仿真或写入能力。

## English summary

`model-diagnose` explicitly enables four bounded, read-only tools over one
validated `CircuitDesign`. Optional `--experiment-dir` adds four tools over a
sanitized snapshot of an already completed experiment: column statistics,
deterministic requirement verdicts, and artifact hash metadata. The tools do
not expose report text, raw samples, artifact content, or local paths, cannot
run Multisim or mutate state, and explicitly mark design/experiment association
as unverified. Requested structured circuit data and evidence are still sent
to the selected provider, so proprietary work requires an appropriate provider
policy or a local model. Structural checks are not ERC, simulation, or proof of
electrical correctness.

Explicit `--enable-patch-preview` adds one more tool for up to 16 component
value, design-parameter, or annotation operations. It verifies the fixed design
ID, revision, targets, and current values, builds only an in-memory candidate,
and returns an inverse patch plus structural deltas. A valid preview is neither
an approval nor evidence of electrical improvement; no file, source design,
backend, or simulator is changed. Persistence is available only through the
separate local, one-time approval-gated transaction CLI documented in
`DESIGN_PATCH_TRANSACTIONS.md`. A separate explicitly invoked local verified
patch CLI can bind a plan, simulate the in-memory candidate, and persist only
an all-pass result. The model-facing diagnosis tools still receive no approval,
simulation, or write capability.
