# Agent API contract / Agent API 契约

本页说明 Multisim MCP 现有工具返回值中面向 Agent、DeepSeek Harness 和未来
Workbench 的稳定机器接口。它不是第二套业务 API，也不会替代 MCP；客户端仍应
通过 MCP 工具和 `multisim://` Resources 调用功能。

## 能力发现 / Capabilities

调用 `runtime_status` 后，响应包含 `api_contract`。该对象具有固定的
`schema_version`、`api_name`（当前为 `multisim-mcp-agent-api`）和 `api_version`
（当前为 `1`），并列出当前 Tool Profile、功能开关、错误码和 durable job 状态。
它不含本机路径、进程号或时间戳，因此可以由 UI 缓存，并可在刷新时直接比较。
本机 Workbench loopback API 也提供只读 `GET /api/capabilities`，返回相同对象，
便于页面在尚未建立 MCP 会话时先完成能力握手。

The `runtime_status` response now includes a deterministic `api_contract` object.
Clients can cache it safely: host paths, process IDs, probe timestamps, and other
volatile diagnostics stay outside the contract. The existing MCP tool and resource
counts are unchanged. The loopback Workbench API exposes the same object through
the read-only `GET /api/capabilities` route for an early UI handshake.

## 统一错误 / Structured errors

CLI 的 `--json` 错误以及后续适配器应使用同一嵌套错误对象：

```json
{
  "schema_version": 1,
  "command": "benchmark-suite",
  "success": false,
  "error": {
    "schema_version": 1,
    "code": "invalid_input",
    "type": "ValueError",
    "message": "--output is required",
    "retryable": false,
    "command": "benchmark-suite"
  }
}
```

`type` 和 `message` 为 1.2 兼容字段；新客户端应优先依赖稳定的 `code` 和
`retryable`，不要解析异常文本。当前错误码为：

| code | 含义 | 可重试 |
| --- | --- | --- |
| `invalid_input` | 参数、JSON 或网表不符合契约 | 否 |
| `not_found` | 实验、作业或文件不存在 | 否 |
| `already_exists` | 目标已存在且未允许覆盖 | 否 |
| `permission_denied` | 文件或系统权限不足 | 否 |
| `timeout` | 后端或作业超过超时 | 是 |
| `backend_unavailable` | 后端连接不可用 | 是 |
| `io_error` | 文件/IO 错误 | 否（调用方可在确认暂态后重试） |
| `runtime_error` | 已知运行时状态冲突 | 否 |
| `internal_error` | 未分类异常 | 否 |

错误对象不会携带 traceback、密钥或完整本机环境。消息仍可能包含用户提供的
设计名称；展示前应按 UI 的日志脱敏策略处理。

## Durable job 状态事件 / Task status

`api_contract.tasks` 描述当前 `submit_*` 作业边界。作业通过
`multisim://jobs/{job_id}` 读取，状态机为：

`queued → running → succeeded | failed | cancelled | timed_out`

运行中的取消请求会短暂进入 `cancelling`。`mcp_task_status` 提供到 MCP Tasks
语义的映射（`working`、`completed`、`failed`、`cancelled`）。当前实现仍使用
已有的 `get_experiment_job` 工具和状态 Resource；每个 Workbench 作业快照还会
附带有界的 `task_event` 对象，便于 UI 直接渲染状态而不读取作业规格或结果路径。

可信本机 UI/Agent 可以通过 loopback Workbench API 订阅同一事件：

```text
GET /api/jobs/{job_id}/events
```

这是只读的 Server-Sent Events（SSE）接口，不提交任务、不取消任务、不启动仿真。
服务端先验证作业句柄，再立即发送当前 `task_event`，之后只在状态、阶段或进度发生
变化时发送 `event: task_event`；连接空闲时发送 `: heartbeat` 注释。默认连接最多
保持 15 秒，允许 `timeout=0.1..30` 和 `interval=0.05..2` 秒；`once=1`（也接受
`true`/`yes`）只读取一个快照后关闭。作业进入 `succeeded`、`failed`、`cancelled`
或 `timed_out` 时连接自动关闭，客户端可以用新的 GET 请求重连。所有选项都有硬上限，
不会创建无界长连接或后台订阅任务。

每个事件的 `data` 是一行 JSON，字段与 `task_event` 相同并增加短期 `event_id`：

```text
id: 9d1e3a6b0e1b4d6a9c2f8a10
event: task_event
data: {"schema_version":1,"job_id":"job-...","event_type":"state_changed","state":"running","status":"working","stage":"simulate","progress":42,"updated_at":"2026-09-04T10:00:00Z","event_id":"9d1e3a6b0e1b4d6a9c2f8a10"}
```

`event_id` 由作业标识、更新时间、状态、阶段、进度和状态映射确定性生成，客户端可
用于去重；当前版本不承诺按 `Last-Event-ID` 回放历史事件。

The durable queue remains polling-compatible. The loopback Workbench API now also
offers a bounded read-only SSE stream at `/api/jobs/{job_id}/events`; it emits the
same versioned `task_event` snapshot and closes on terminal states. The existing
MCP tools, Resources, and persisted job records remain unchanged. A future MCP
Tasks adapter can reuse the same state names and event payload without changing
the storage schema.

## 自然语言工程任务 / Natural engineering tasks

Agent 应先调用 `plan_*` 查看结构化合同，再调用对应的 RC、RLC 或 OPAMP `run_*` 工具。
需要后台执行时调用 `submit_natural_engineering_job`，提交后使用
`get_experiment_job` 或 `multisim://jobs/{job_id}` 查询状态。终态结果包含
`topology_acceptance`、`measurement_acceptance`、`artifacts` 和 `error`。

`unverified`、`target-not-met` 和 `failed` 都必须展示为未通过，不能仅凭生成了 `.ms14`
就宣称电路正确。

## 多板规划 / Multi-board planning

工程计划可以提供 `boards` 和 `components`，返回候选分板、跨板网络、连接器引脚和成本评分。
该结果是拓扑规划，尚未自动生成多个独立 `.ms14` 工程；每块板仍需分别通过原生网表和仿真验收。

## 模型工程后端入口 / Model engineering handoff

未来独立软件可调用以下 loopback 后端入口；两者复用 MCP/CLI 的同一审计服务：

```text
POST /api/model-engineering/plan
POST /api/model-engineering/run
```

请求至少包含 `text`；执行请求还必须提供新的 `output_dir`，并显式设置 `execute: true`
才会写入工程、启动原生仿真或导出报告。`execute: false` 只返回模型提案和一致性结果。
该入口当前继承自然语言 RC 合同范围，不能宣称支持任意电路生成。

## 兼容策略 / Compatibility

- `api_version` 只有在字段语义发生不兼容变化时才递增。
- 新字段可向后添加；客户端必须忽略未知字段。
- `schema_version` 针对单个对象的序列化结构；不能用异常 `type` 推断错误语义。
- Tool Profile 会影响可发现工具数量，但 `runtime_status` 与本页契约始终可用。

## 2N3904 共射原生入口

`plan_natural_common_emitter(text)` 返回候选方案和估算，不能代替实际验收。
`run_natural_common_emitter(text, output_dir, execute=false)` 默认仅预览；
`execute=true` 要求新的输出目录和含 VDC、VPULSE 的用户本地元件包。
工具属于 experiment/full profile。成功返回原生 OP/AC/TRAN 采样结果、模型和引脚
核验、实际源属性核查、工程和报告路径。`delivery_status=requires-visual-review`
表示仍应查看最终电路图；不等于任意电路已通过工程认证。

复现实例和限制见 [共射原生验收记录](COMMON_EMITTER_NATIVE_ACCEPTANCE.md)。
