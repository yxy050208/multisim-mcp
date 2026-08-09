# 升级到 Multisim MCP 1.0 / Migrating to 1.0

本文面向从 `0.1.0a1`–`0.1.0a3` 升级的用户。1.0 保留无参数 stdio 启动方式和
原有核心工具；升级不需要转换 `.ms14` 文件或既有实验输出。

## 升级步骤

1. 确认 Multisim 14+ 已安装并授权，且 MCP 使用 32 位 Python 3.10+。
2. 在运行 MCP 的同一个解释器中升级：

   ```powershell
   C:\path\to\python32\python.exe -m pip install --upgrade "multisim-mcp==1.0.0"
   C:\path\to\python32\Scripts\multisim-mcp.exe doctor --lang zh --connect
   ```

3. 完全退出并重新启动 MCP 客户端，避免旧 server 进程继续驻留。
4. 使用 1.0 仓库中的 `tools/bootstrap_local_component_pack.py` 从本机授权样例重新生成
   schema 2 模板包，并更新 `MULTISIM_MCP_TEMPLATE_DIR`。建议写入新目录；不要提交或
   分发从 NI 安装中提取的 XML。生成器会显式连接 Multisim，并用当前安装版本新建一个
   临时空白电路作为结构骨架；运行前请保存正在编辑的工作。
5. 调用 `runtime_status`，再运行一个新的只读诊断或分压器实验确认环境。

## 1.0 的兼容性变化

- MCP Python SDK 升至 2.x，但同一服务兼容现代 `2026-07-28` 和旧协议客户端。
- 服务新增持久任务、扫描、验证、便携元件适配器、虚拟仪器和正式报告；原工具名仍保留。
- 长实验推荐使用 `submit_circuit_experiment`，并通过 `job_id` 查询或取消；同步
  `run_circuit_experiment` 仍可用。
- 完整实验现在额外生成中英双语 HTML/PDF 和 `manifest.json`。固定 Resource 集合扩大，
  不应假定模板数量仍为旧版数值。
- 自定义适配器仅接受 `MULTISIM_MCP_ADAPTER_DIR` 中严格声明式 JSON；代码、外部文件
  指令、符号链接和覆盖内置适配器均被拒绝。
- 旧生成器的 schema 1 模板包必须重建。1.0 的 `doctor` 会拒绝其
  `local-pack-manifest.json`，因为旧版电阻提取源可能在真实 Multisim 打开后被静默丢弃。
- 已有实验目录可用 `register_experiment_artifacts` 重新注册；旧目录缺少新的正式报告时，
  可先注册，再调用 `export_formal_experiment_report`。

## 回退

回退前先取消正在运行的任务并备份 `MULTISIM_MCP_JOB_DIR`（默认位于
`%LOCALAPPDATA%\multisim-mcp\jobs`）。旧 alpha 版本不了解 1.0 新增的持久任务和报告，
因此不要让两个版本同时操作同一个任务目录。回退不会修改已有 `.ms14` 或实验产物。

## English summary

Upgrade the same 32-bit Python environment, rebuild the user-local template
pack with the 1.0 generator, restart the MCP client, and validate with `doctor`
plus `runtime_status`. Existing core tools remain available. New durable jobs,
sweeps, adapters, instruments, and report Resources are additive. Back up and
isolate the job-state directory before any downgrade.
