# 任务与实验恢复 / Job and Artifact Recovery

Multisim MCP 1.0 将长实验隔离到 worker 进程，并把任务状态以原子 JSON 保存。默认任务
目录为 `%LOCALAPPDATA%\multisim-mcp\jobs`，也可通过 `MULTISIM_MCP_JOB_DIR` 显式指定。

## MCP 或 Multisim 意外退出

1. 不要删除任务目录、输出目录旁的锁文件或半成品目录。
2. 重新启动 MCP server；若前端为 64 位，确认 `MULTISIM_MCP_WORKER_PYTHON`
   仍指向安装了本项目和 pywin32 的 32 位 Python。
3. 调用 `list_experiment_jobs`，再用 `get_experiment_job(job_id)` 查看状态。
4. 服务会把中断的 `running` / `cancelling` 任务安全恢复为队列任务，并从原始规范重建，
   不会把不完整产物当成成功结果。
5. 对 `failed`、`cancelled` 或超时任务，排除根因后调用 `retry_experiment_job`；它会生成
   新的 `job_id`，旧记录继续保留用于审计。

## 找回已完成实验

如果输出目录完整但当前进程没有 Resource 句柄，调用：

```text
register_experiment_artifacts(output_dir="C:\\msre_exp\\my-experiment")
```

服务只注册固定白名单中的产物，并返回不含本机路径的 `experiment_id`。旧实验缺少
双语 HTML/PDF 时，可随后调用 `export_formal_experiment_report(experiment_id)`。
扫描结果使用 `register_sweep_artifacts` 恢复。

## 恢复 DesignPatch 事务

`patch-apply` 或 `patch-revert` 被强制终止时，不要删除目标旁的隐藏 journal、staging、
backup 或锁。确认原进程已经退出后运行：

```powershell
multisim-mcp patch-recover --target .\design.json --action auto --json
```

自动模式只在目标设计和回执均与 journal 完全一致时完成提交；其余审批尚未消费的部分
交易会恢复原设计。存活 PID、格式损坏的锁、已被替换的目标/回执或消费状态冲突都会
失败关闭并保留证据。多个 journal 必须用 `--journal` 精确指定；完整状态机和人工
`commit` / `rollback` 流程见
[`DESIGN_PATCH_TRANSACTIONS.md`](DESIGN_PATCH_TRANSACTIONS.md)。

## 恢复验收闭环

`patch-verify-apply` 中断后先查看 `verified-workflow.json`。如果目标旁还存在底层补丁
journal，先按上一节运行 `patch-recover`；随后执行：

```powershell
multisim-mcp patch-verify-recover `
  --workflow-manifest .\verified-workflow.json --json
```

完整目标与回执会在复核通过结论、审批 ID 和候选摘要后补记为 `committed`；没有回执且
输入仍保持原摘要时会标记为 `aborted`。没有回执但目标已改变属于歧义状态，命令会失败
关闭而不是猜测提交结果。实验目录和工作流清单都应保留用于审计。

## 中断的优化与自主纠错

较长优化应通过 `submit_design_optimization` 提交，并用现有 job 工具查询。MCP 前端在
任务运行时退出后，新的前端会把任务安全重新排队；已完成候选只有在子 manifest、验收、
目标和采购证据重新校验通过后才复用，正在运行但尚未提交的候选会写入新的 attempt 目录。
worker 崩溃、`JOB_TIMEOUT` 或取消形成终态时，使用 `retry_experiment_job` 创建恢复尝试。

混合参数/拓扑搜索使用 `submit_global_optimization`；它会重验已完成候选的实验 manifest、
验收和 Pareto 目标向量，再从首个中断候选继续。模型规划闭环使用
`submit_autonomous_correction`；它会从最后一个完整、严格改进且证据匹配的选中轮次继续，
中断轮次重新规划。两者都要求原设计、原始规范和运行限制完全一致。自治任务还绑定无
密钥 Provider 身份；Provider、模型、回退顺序或超时变化时必须在新的空目录重跑。
`heartbeat_timeout` 必须大于 `model_timeout`，因为一次非流式模型请求期间没有候选检查点。

同步 CLI 被强制终止后，可使用原始设计文件、原始 OptimizationSpec、完全相同的
`--timeout` / `--max-points` 和原输出目录执行 `optimize-design --resume`。不要把输出目录中
规范化后的 `optimization-spec.json` 当成原始 CLI 输入，也不要手工删除候选或修改状态。
如果设计、规范、运行限制或完成证据不一致，恢复会失败关闭；这时保留目录作为部分证据，
在新的空目录重新运行。只有具有最终 `directory.manifest.json` 的结果才能宣称完成。

`compare-designs` 当前也采用同步、有界执行。正常取消会写入 `cancelled` 状态和完整目录
manifest；强制终止可能留下 `state: running` 的部分比较目录。该目录不得宣称为完整排名，
也不支持原地续跑；保留已完成实验作为部分证据，并用相同版本和规范在新的空目录重跑。

## 常见故障

- `WORKER_CRASH`：先运行 `multisim-mcp doctor --connect`，确认 COM、许可证和模板包。
- `WORKER_UNRESPONSIVE`：确认没有 Multisim 模态对话框；关闭对话框后用重试工具创建新任务。
- `JOB_TIMEOUT`：保留原记录，适当提高 `job_timeout`，但其值必须大于单次仿真的 `timeout`。
- 输出目录已占用：使用任务查询确认所有者，不要手工删除有效锁；取消或等待原任务结束。
- 资源文件过大：检查文件来源后调整正整数 `MULTISIM_MCP_RESOURCE_MAX_BYTES`，不要把服务
  暴露为网络文件服务器。

## 证据与备份

每个成功实验的 `manifest.json` 记录生成器版本、输入和 SHA-256。备份时保留整个输出
目录，不要只复制报告。任务状态目录可能包含本机路径和实验输入，不应提交到公开仓库。

## English summary

Restarting the same server environment safely requeues interrupted durable
jobs. Inspect jobs before retrying, never treat partial output as complete, and
restore completed artifact handles with the registration tools. Preserve the
whole experiment directory and its reproducibility manifest; never publish the
local job-state directory. For an interrupted verified patch, recover any
adjacent patch journal first and then run `patch-verify-recover` against the
workflow manifest; it finalizes only a receipt-backed passing commit and safely
aborts an unchanged pre-commit state. Durable parameter, global, and autonomous-
correction jobs revalidate completed candidate or round evidence before reuse and
rerun interrupted work in a new attempt directory. The synchronous
`optimize-design --resume` surface supports the same candidate-level recovery when
the original design, raw spec, runtime limits, and output directory are unchanged.
Provider identity is also part of the autonomous correction resume contract; no
API key value is persisted.
The synchronous `compare-designs` workflow has the same forced-termination
boundary: a `running` directory without a final manifest is partial evidence,
not a completed ranking, and must be rerun into a new empty directory.
