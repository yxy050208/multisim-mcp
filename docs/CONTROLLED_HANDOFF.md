# 受控执行交接包 / Controlled Execution Handoff

工作台的仿真审批页可以下载
`multisim-approved-experiment-handoff.json`。它是一个**本地调用载荷**，不是
实验结果，也不会因为下载或复制而启动 Multisim。若不希望手动把 JSON 拆成两个
MCP 调用，可以使用 `execute-handoff` CLI。

## 先校验，不执行

在包含工程目录的可信终端中运行：

```powershell
$env:PYTHONPATH = (Resolve-Path .\mcp_server).Path
python -m multisim_mcp.cli execute-handoff `
  --handoff .\multisim-approved-experiment-handoff.json `
  --root C:\path\to\project `
  --json
```

默认模式只做以下检查：

- 顶层 schema、步骤顺序和工具名称；
- 网表、`CircuitDesign` 编译预览、网表审批和仿真计划审批在两步之间保持一致；
- `ExperimentSpec` 的验收项、理论值、网表和安全分析命令在任何文件写入前通过同一套
  服务级校验；超时和最大采样点数也必须落在执行服务的边界内；
- 输出目录、`.ms14` 和 PNG 路径均为工程根目录下的相对路径，拒绝绝对路径、`..` 和
  符号链接穿越；
- `result_contract` 仍绑定 `path_manifest_and_approval_provenance`。

## 明确执行

只有确认校验输出和目标目录后才加 `--confirm`：

```powershell
python -m multisim_mcp.cli execute-handoff `
  --handoff .\multisim-approved-experiment-handoff.json `
  --root C:\path\to\project `
  --confirm `
  --json
```

命令严格按两步执行：

1. 用已批准的 SPICE 预览调用 `create_schematic_from_netlist`，生成 `.ms14` 和预览图；
2. 第一步成功后，使用同一份 `ExperimentSpec`、可执行网表和审批凭证调用
   `run_verified_circuit_experiment`。

第一步失败会立即停止，`simulation_started` 为 `false`。默认不覆盖已有产物；只有在
交接包本身请求覆盖且操作者再次加 `--allow-overwrite` 时才允许覆盖。该开关不会绕过
审批摘要、网表一致性或 manifest 校验。

对于时间较长的实验，可在明确确认的同时交给 durable worker：

```powershell
python -m multisim_mcp.cli execute-handoff `
  --handoff .\multisim-approved-experiment-handoff.json `
  --root C:\path\to\project `
  --confirm --submit --json
```

该模式仍会先同步生成原理图，然后只把已校验的仿真计划写入现有作业队列；返回的
`job_id`/`status_uri` 可交给工作台 `Jobs / 队列` 页面观察。队列提交本身的
`simulation_started` 为 `false`，实际启动由长驻 worker 完成。

## 与工作台的关系

浏览器仍保持 loopback 只读：它负责规划、审批、复制/下载交接包和回读证据；CLI 是
明确的本机执行边界。完成后点击工作台的“执行后刷新结果”，页面会按目标路径、
manifest 完整性和审批摘要三重匹配，只有匹配成功才把目录显示为当前实验。

旧版或直接执行的实验可以继续查看，但由于没有这份脱敏审批归属摘要，工作台会标记为
“未记录审批归属”，不会冒充本次受控执行。

## English summary

The workbench download is a local invocation payload, not an experiment result. Run
`execute-handoff` without `--confirm` to validate its schema, two-step identity,
ExperimentSpec requirements and safe commands, runtime limits, and root-confined paths.
Add `--confirm` only after reviewing the output. The CLI creates
the approved schematic first and runs the verified experiment second; a schematic
failure stops before simulation. Existing artifacts are never overwritten by default.
For long runs, add `--submit --confirm` to create the schematic first and enqueue the
verified experiment in the durable worker; the returned job handle can be monitored in
the workbench queue.
After completion, the workbench refreshes only when the path, manifest integrity, and
sanitized approval provenance all match.
