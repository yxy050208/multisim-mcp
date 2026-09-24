# DesignPatch 审批、应用与撤销

`multisim-mcp` 把“模型提出修改”和“本机持久化修改”分成两个独立信任边界。
`model-diagnose --enable-patch-preview` 仍然只有内存预览能力；真正写入只能由用户在本机
显式运行 `patch-approve`，再用一次性令牌运行 `patch-apply` 或 `patch-revert`。

当前写入对象是版本化 `CircuitDesign` JSON，不是 Multisim 工程文件，也不会启动
Multisim、运行仿真或证明电路指标改善。

## 完整流程

假设人工审查后已把预览中的 `patch` 保存为 `patch.json`。如果补丁修改元件值或设计
参数，而设计携带权威 `source_netlist`，审批和应用两步都必须显式加入
`--regenerate-source-netlist`；安全编译器无法无损表达设计时会失败关闭。

```powershell
# 1. 为完全确定的输入、输出和回执签发 15 分钟一次性审批。
multisim-mcp patch-approve `
  --design .\design.json `
  --patch .\patch.json `
  --in-place `
  --receipt .\apply-receipt.json `
  --regenerate-source-netlist `
  --token-output .\apply.token `
  --json

# 2. 使用令牌应用；令牌不会出现在命令行参数或 JSON 输出中。
multisim-mcp patch-apply `
  --design .\design.json `
  --patch .\patch.json `
  --in-place `
  --receipt .\apply-receipt.json `
  --regenerate-source-netlist `
  --approval-token-file .\apply.token `
  --json
```

若不希望覆盖输入，把两步的 `--in-place` 同时替换为同一个
`--output .\candidate.json`。审批会绑定规范化后的设计、补丁、候选、输入路径、目标
路径、回执路径、源网表选项和修订号；任一项变化都必须重新审批。

撤销不是旧令牌的附带能力，需要第二次审批。撤销会恢复字段值，但修订号继续单调递增，
不会把文件字节和修订号倒退到旧版本。

```powershell
multisim-mcp patch-approve `
  --design .\design.json `
  --revert-transaction .\apply-receipt.json `
  --in-place `
  --receipt .\revert-receipt.json `
  --regenerate-source-netlist `
  --token-output .\revert.token `
  --json

multisim-mcp patch-revert `
  --design .\design.json `
  --transaction .\apply-receipt.json `
  --in-place `
  --receipt .\revert-receipt.json `
  --regenerate-source-netlist `
  --approval-token-file .\revert.token `
  --json
```

也可以用 `--approval-token-env NAME` 从指定环境变量读取令牌。没有内联 token 参数，
防止 bearer secret 进入 shell 历史或进程列表。令牌文件不会自动删除；成功交易后令牌
已经作废，但文件仍应按本机秘密处理并加入 `.gitignore`。

## 安全与一致性门禁

- 默认有效期 900 秒，可用 `--ttl-seconds` 在 60–86400 秒内显式调整；
- 审批存储只保存令牌 SHA-256，不保存 bearer token；令牌只可成功消费一次；
- 设计 ID、基线修订、每项 `before`、目标存在性和最多 64 项操作会在审批、应用时
  分别复核；
- 操作可覆盖元件值/模型/引脚、元件增删替换、网络增删以及设计参数/注释；拓扑变更必须
  是结构化可逆操作，且权威源网表必须显式安全再生；
- 参数/注释中的 `null` 同时承担“原键不存在”的补丁表示，因此已有值为 `null` 的目标会
  被拒绝，避免生成不能精确恢复键存在性的伪逆补丁；
- 目标设计与回执均为精确路径绑定，输出和回执采用 create-only，不覆盖意外存在的文件；
- 同一目标有跨进程互斥锁，两个独立审批不能同时发布；
- 设计与回执发布期间发生可捕获的 I/O 或审批消费错误时，会恢复原设计并移除半成品；
- 写目标前会在目标旁持久化版本化 journal、候选、回执 staging 和原设计备份；各状态及
  文件都用确定性摘要复核，目录同步在平台支持时一并执行；
- 回执保存正向补丁、严格逆补丁、输入/输出修订和摘要，但不是数字签名，拥有本机文件
  写权限的用户仍属于信任边界。

审批记录默认位于每用户状态目录；可用
`MULTISIM_MCP_PATCH_APPROVAL_STORE` 或 `--approval-store` 指定私有目录。不要把该目录、
令牌文件或包含专有电路参数的回执提交到公开仓库。

## 先验收再提交：闭环应用

`patch-verify-approve` / `patch-verify-apply` 把真实 Multisim 实验接到同一审批边界。
补丁先应用到内存候选，候选只有在每项要求都得到 `pass` 时才进入已有的持久化事务；
只要出现 `fail`、`unverified` 或实验错误，就丢弃未提交候选，输入设计和目标路径保持不变。
这相当于预提交自动回滚，避免为了恢复原值而再制造一个设计修订。

验收计划不包含可替换的网表；网表始终由已审批候选生成。例如：

```json
{
  "schema_version": 1,
  "title": "RC candidate verification",
  "commands": "ac dec 100 10 1Meg",
  "requirements": [
    {
      "id": "cutoff",
      "metric": "cutoff_frequency",
      "signal": "V(out)",
      "reference_signal": "V(in)",
      "operator": "between",
      "lower": 15000,
      "upper": 17000,
      "unit": "Hz"
    }
  ],
  "theoretical_values": {"cutoff": 15915.5}
}
```

审批和执行必须使用完全相同的参数：

```powershell
multisim-mcp patch-verify-approve `
  --design .\design.json --patch .\patch.json --in-place `
  --receipt .\verified-receipt.json --regenerate-source-netlist `
  --verification-plan .\verification-plan.json `
  --experiment-output .\candidate-experiment `
  --workflow-manifest .\verified-workflow.json `
  --timeout 120 --max-points 2000 `
  --token-output .\verified.token --json

multisim-mcp patch-verify-apply `
  --design .\design.json --patch .\patch.json --in-place `
  --receipt .\verified-receipt.json --regenerate-source-netlist `
  --verification-plan .\verification-plan.json `
  --experiment-output .\candidate-experiment `
  --workflow-manifest .\verified-workflow.json `
  --timeout 120 --max-points 2000 `
  --approval-token-file .\verified.token --json
```

审批额外绑定规范化验收计划摘要、计划路径、实验目录、工作流清单路径、超时、返回点数和
固定策略。验收计划、路径或运行参数改变后，旧令牌会在启动 Multisim 前被拒绝。审批
有效期必须至少覆盖实验超时再加 60 秒提交余量；长实验应显式增加 `--ttl-seconds`。

工作流清单交叉记录审批 ID、输入/候选/补丁摘要、验收计划、实验目录 manifest、
`verification.json` 和最终补丁回执。只有 `overall_status == "pass"` 可以提交；
`unverified` 不会降级成通过。该写入能力仍只存在于本机 CLI，模型的 MCP 补丁预览保持
只读。

## 故障恢复与边界

当前事务对 Python 进程能捕获的异常立即逆向恢复；进程被强制终止后，目标目录旁会保留
形如 `.design.json.patch-journal-….multisim-patch-journal.json` 的隐藏 journal。先确认
原进程已经退出，然后执行：

```powershell
# 自动策略：目标和回执均完整时提交，否则在审批尚未消费时回滚。
multisim-mcp patch-recover --target .\design.json --action auto --json

# 存在多个 journal 时必须精确选择；人工审查后也可明确继续提交或回滚。
multisim-mcp patch-recover `
  --journal .\.design.json.patch-journal-….multisim-patch-journal.json `
  --action commit `
  --json
```

恢复命令会拒绝仍存活的 journal/锁所有者、格式损坏的锁、摘要不匹配的设计/回执、错误
审批存储及被另一交易消费的审批。它不需要旧 bearer token：journal 只在令牌已通过完整
审批核对并取得锁之后创建，恢复时还会把 journal、回执和原审批记录重新交叉验证。

`auto` 的规则有意保守：

- 目标和回执都与 journal 完全一致：完成审批消费并清理 staging；
- 只有目标或更早状态落盘且审批未消费：恢复原设计、移除本交易回执，审批仍可在有效期
  内重试；
- 审批已消费但发布不完整，或已发布文件被替换/损坏：失败关闭，保留 journal 和备份供
  人工审查；
- `commit` 可从完整 staging 继续，`rollback` 不允许撤回已经消费的审批。

异常终止后应：

1. 确认没有 `patch-apply` / `patch-revert` 进程仍在运行；
2. 不要手工删除 journal、候选、回执 staging、`.backup`、`.tmp` 或锁；
3. 优先运行 `patch-recover --target ... --action auto --json`；
4. 如果恢复失败，保留所有文件和 JSON 错误结果，不要签发新的覆盖审批。

若中断发生在闭环工作流而不是底层补丁发布中，使用：

```powershell
multisim-mcp patch-verify-recover `
  --workflow-manifest .\verified-workflow.json --json
```

- 已有完整目标和补丁回执时，命令核对审批 ID、候选摘要和通过结论后补记 `committed`；
- 没有回执且输入仍是原摘要时，命令确认没有提交并标记 `aborted`；
- 目标已变化但没有完整回执时失败关闭，应先对相邻 patch journal 运行 `patch-recover`。

进程终止发生在“journal 尚未创建”之前时目标还未修改，但原子 JSON 更新可能留下无害的
随机 `.tmp`；目前不会扫描并自动删除无法归属某个 journal 的孤立临时文件。底层存储也
必须兑现文件写入、替换和同步语义，网络盘或异常文件系统不能被描述为掉电级保证。

闭环当前持久化的仍是 `CircuitDesign` JSON；实验会生成独立 `.ms14`、波形、数据和报告，
但不会把任意现有 `.ms14` 工程原地改写。优化候选生成和多轮预算管理仍属于下一阶段。

## English summary

Model-authored patch preview remains read-only and in memory. Persistence is a
separate local CLI boundary: `patch-approve` issues a short-lived one-time
bearer token bound to the exact design, patch, candidate, revisions, paths, and
source-regeneration choice; `patch-apply` consumes it. Revert requires a fresh
approval and advances the revision while restoring field values.

Tokens are accepted only from a bounded file or a named environment variable,
are never printed, and only their SHA-256 is stored. Writes are create-only or
replace one explicitly approved input, serialized per target, and rolled back
on caught publication failures. A versioned adjacent journal persists candidate,
receipt, backup, state, paths, and digests before target publication.
`patch-recover` refuses live owners and mismatched evidence; `auto` commits only
a fully published design plus receipt and otherwise rolls back an unconsumed
transaction. Explicit commit/rollback is also available. Receipts and journals
are strictly validated but are not cryptographically signed.

The separate `patch-verify-approve` / `patch-verify-apply` path binds a
netlist-free verification plan, experiment directory, workflow manifest,
timeout, and point limit into the same approval. It simulates the in-memory
candidate through the real Multisim experiment service and persists the design
only when every requirement passes. `fail`, `unverified`, and experiment errors
discard the uncommitted candidate. The workflow manifest cross-links experiment
and patch receipts, and `patch-verify-recover` finalizes or safely aborts an
interrupted workflow. Existing `.ms14` files are not modified in place.
