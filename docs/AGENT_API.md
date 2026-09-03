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
已有的 `get_experiment_job` 工具和状态 Resource；`event_types` 是给后续 UI
订阅/轮询适配器的版本化约定，不改变 1.2 的调用方式。

The durable queue remains polling-compatible today. A future SSE or MCP Tasks
adapter can emit `created`, `progress`, `state_changed`, and `completed` events
using the same state names without changing persisted job records.

## 兼容策略 / Compatibility

- `api_version` 只有在字段语义发生不兼容变化时才递增。
- 新字段可向后添加；客户端必须忽略未知字段。
- `schema_version` 针对单个对象的序列化结构；不能用异常 `type` 推断错误语义。
- Tool Profile 会影响可发现工具数量，但 `runtime_status` 与本页契约始终可用。
