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

当显式连接器合同与目标版本 manifest 匹配时，原生多板阶段会把每块板的物理实例加入
结构化设计，并输出与 pin 顺序一致的 `XJ<n> ... <native_name>` 记录；14.3 的
`HDR1X4` 已完成这一闭环。目标版本缺少 `verified` 映射时，物化结果保持
`native_connector_status=mapping-pending`，`run_native_multiboard_acceptance(execute=true)`
会拒绝继续，避免把只有逻辑接口注释的工程误报为物理连接器工程。
目标版本没有 manifest 时，预览会返回 `connector_resolutions[].status=unavailable` 并保持
`native_connector_ready=false`，不会因为缺少版本清单而合成连接器。
重开后的 `ReportNetlist` 还会对已验证 `HDR1X4` 的 `P1`–`P4` 与源网逐针比对；连接器
存在但针脚顺序错误时，板级拓扑验收会失败。

## 多板规划 / Multi-board planning

MCP 工具 `plan_multiboard_engineering_request` 接受版本化结构化请求，执行只读校验和候选
分板规划，不启动 COM、不写入工程文件。
选择候选时调用 `select_multiboard_engineering_candidate`，它只接受
`structurally_ready=true` 的候选并返回绑定 `plan_digest` 的选择凭证。
工程计划可以提供 `boards` 和 `components`，返回候选分板、跨板网络、连接器引脚和成本评分。
板可以声明 `max_components` 和 `max_connector_pins` 约束；结果会返回每块板的
`connector_pin_count`、`board_interfaces`、`violations` 和 `feasible`。候选排序会优先保留
满足约束的方案，违反约束的方案仍保留并明确标记为 `infeasible`，不会被当作可交付设计。
`connector_count` 表示跨板 pin 记录数；多 pin 连接器的物理数量另由
`physical_connector_count` 给出。
工程请求还可以提供显式的 `connectors` 合同。每个连接器必须声明 `part`、参与的 `boards`、
每块板的 `instances` 以及从 1 开始连续编号的 `pins`；每个 pin 声明 `net`、
`signal_type` 和 `direction`，可选声明板端方向及电压/电流/阻抗约束。规划器会检查跨板
网络覆盖、pin 编号、物理实例归属、地线类型、端点方向冲突和容量限制，并在
`connector_contract` 中返回规范化合同与违反项。未提供该字段时仍生成兼容的逻辑连接器，
但 `connector_contract_status=inferred`、物理映射保持 `unverified`，不能据此声称真实连接器
已经确定。显式合同通过结构门禁后，接口记录会附带 `part` 和对应板端 `instance`；候选同时
返回 `native_connector_status` 和 `native_connector_ready`。版本感知的原生执行阶段会先
调用 `resolve_connector_mapping`；14.3 的 `HDR1X4` 已有完整 pin signature 和实机闭环
证据，其他 part 没有对应条目时仍保持 `mapping-pending`。旧简写的状态为
`unverified-inferred`，不能把它当作真实封装；通用规划预览在未绑定目标版本时仍保持
`native_connector_ready=false`。
兼容层提供 `resolve_connector_mapping`，要求 manifest 同时匹配连接器标识、完整 pin
signature 和目标版本；没有独立 manifest 条目时返回 `unavailable`，不会退回到通用 `XSUB2`
载体。
可行候选会附带 `logical_artifacts`：每块板的组件、板内网络、跨板连接器和接口清单。
这些工件的 `status` 为 `logical-only`、`verification_status` 为 `unverified`，用于下一阶段
逐板生成和验收；EDA 核心还可以据此生成每块板的结构化 `CircuitDesign` 和 SPICE 预览。
`interface_validation.status=valid` 只表示接口工件结构对称且完整；空板或接口不对称时为
`invalid`，不能送入逐板生成。上述预览不是已经生成的 `.ms14` 工程。每块板仍需分别通过
原生网表和仿真验收。
计划还返回 `structurally_ready` 和 `recommended_multiboard_candidate`；`score.feasible`
只代表容量/连接器约束满足，不能替代逐板结构门禁。

完成规划后可调用 `run_native_multiboard_acceptance` 进入首版原生验收闭环。该工具接收
同一份 `request`、新的 `output_directory` 和可选 `candidate_index`；它会重新计算计划、
只接受 `structurally_ready=true` 的候选，并把选择摘要、计划摘要和验收结果绑定在一起。
`execute=false`（默认）只返回夹具工件预览，不启动 COM；显式 `execute=true` 才会按目标
Multisim 版本逐板生成、打开、保存、重开、ReportNetlist 回读和原生 DC OP，并比较显式
观测点与完整电路参考。只有返回 `status=accepted` 且 `verification_status=native-verified`
时才表示这组 14.3 原生证据通过；其他状态必须继续标为未验证或失败。当前首版覆盖
DC operating point，并支持显式 `analysis=tran` 或 `analysis=ac`。验收会保留并逐点比较
原生返回的完整采样轴；TRAN 不再只看末时刻，AC 同时比较复数响应的实部和虚部，且
不同采样轴不会插值。AC 还要求响应不是全零，以防没有声明 AC 激励时误判为通过。
执行目录还会为每块板保留与分析类型对应的 `native-op.*`、`native-tran.*` 或
`native-ac.*`，以及对应的 `*-series.json`、`*-series.csv` 和重开记录；根目录提供 `acceptance.json` 与
`acceptance-report.md`，便于人工审阅、归档和后续实验报告组装。

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
工具属于 experiment/full profile。执行版会在估算值附近测试最多 5 个 E24 发射极电阻，
每个候选独立保存到 `candidate-NNN/`，按原生 1kHz 增益误差选择。成功返回原生
OP/AC/TRAN 采样结果、模型和引脚核验、实际源属性核查、工程和报告路径；
`optimization` 记录候选数量和选择结果。`delivery_status=requires-visual-review`
表示仍应查看最终电路图；不等于任意电路已通过工程认证。

复现实例和限制见 [共射原生验收记录](COMMON_EMITTER_NATIVE_ACCEPTANCE.md)。
