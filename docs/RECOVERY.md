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
local job-state directory.
