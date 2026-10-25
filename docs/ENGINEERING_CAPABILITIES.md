# 工程能力发现

能力发现帮助宿主 AI 选择已有入口，并区分实现范围、目标版本、历史实验和本机依赖。
它不会生成电路、调用模型或替代新工程的原生验收。基本多板联调保持开源；此改动
没有增加专有跨板优化或高级排线功能。

## 使用

三个入口共用 `build_capabilities`，在同一版本与 Tool Profile 下返回相同契约：

```powershell
$env:PYTHONPATH = (Resolve-Path .\mcp_server).Path
python -m multisim_mcp.cli capabilities --target-version 14.3 --profile experiment --json
```

已安装用户可以直接运行 `multisim-mcp capabilities`。不指定 `--profile` 时读取
`MULTISIM_MCP_TOOL_PROFILE`，默认 `full`；不指定目标版本时不会自动选择 14.3。

- MCP：`runtime_status({"target_version": "14.3"})`，读取 `api_contract.engineering`。
- 本机 API：`GET /api/capabilities?target_version=14.3`。
- 元件工具：`schematic_component_catalog({"target_version": "14.3"})` 中的
  `versioned_components` 与上述 `component_mappings` 共用实现。
  每个载体的 `versioned_mapping_status` 另行标明版本证据；`ready` 仍只表示模板文件存在。

MCP 当前 `full` profile 提供 123 个工具，已有无参数调用及契约版本 1 兼容。`engineering` 是新增的
独立版本对象；客户端不应以旧目录的 `status` 或载体 `maturity` 推断实验已经通过。
API 的非法、空白或重复目标版本返回 HTTP 400；CLI 非法版本返回结构化错误和退出码 2。
新 CLI 的 JSON 使用 ASCII Unicode 转义，避免 Windows ANSI/UTF-8 管道解码不一致
破坏中文家族名称；解析后的内容与 MCP/API 保持一致。

## 返回字段与判读

| 字段 | 能证明什么 | 不能据此推断什么 |
| --- | --- | --- |
| `families[].catalog_status` | 保留的旧路线图标签 | 整类电路已验收或没有实现 |
| `workflows[].implementation_status` / `scope` | 已存在的受限入口及输入范围 | 任意自然语言或任意拓扑可生成 |
| `workflows[].entrypoints_in_profile` | 当前 MCP profile 能发现哪些工具 | 本机运行依赖可用 |
| `generic_compiler.supported_options` | 实际审批编译器支持矩阵 | 专用运放/整流入口已经接入同一编译链 |
| `component_mappings` | 精确版本清单、SHA-256、逐元件引脚/模型来源及记录状态 | 本机有模板、获得模型再分发权或当前工程正确 |
| `native_records` | 公开文档记录的版本、分析、案例和验收边界 | 本次核验了原始凭据或已在当前机器重跑 |
| `workflows[].native_record_scope` | 记录来自该工作流还是相关底层路径 | 相关路径成功等于自然语言入口端到端成功 |
| `engineering_runtime`（仅 `runtime_status`） | 本机 worker 依赖与模板完整性 | 已激活 Multisim、已检测版本或新电路已验收 |

每个家族的 `whole_family_verified` 保持 `false`。例如数字目录保留 `planned` 标签，
但 `digital-netlist` 可以发现，且关联五个 14.3 案例；基本多板关联两个双板案例和
DC/TRAN/AC 记录。它们不代表所有数字芯片、任意多板双向耦合或物理 PCB 已支持。

`target_evidence_status=documented-cases-only` 只表示有对应版本的公开案例记录。
`native_record_scope=related-backend-cases` 明确区分底层实验与完整入口实验。
统一编译器支持 `signal-passive` 与显式理想模型的 `signal-active`；后者列出模型合同
摘要、SI 数值边界与 `native_validation_status=unverified-for-this-template`。
真实芯片候选不会自动替换成理想运放。其他已实现的专用工作流单独列出。
内部模型入口仍只整理并核对 RC 需求；宿主 Agent 调用 MCP 与内部只读 EDA 工具也分别说明。

## 版本与当前运行状态

仓库当前只有 `components-14.3.json`。其中真实 `lm324aj-section-a` 映射仅覆盖
Multisim 14.3，具体原生结果和校准边界见
[`VENDOR_COMPONENT_ACCEPTANCE_20261011.md`](VENDOR_COMPONENT_ACCEPTANCE_20261011.md)。
查询 14.2、14.3.1 或 15.0 时，元件映射返回
`manifest-unavailable`、组件列表为空，所有工作流的目标版本证据为 `unverified`。
历史 14.3 记录保留供审查，但 `target_version_match=false`，不能借用。
查询 `Multisim 14.3` 或 `14.3.0` 会规范为同一个精确版本。

不指定目标版本时，映射和目标证据均为 `not-selected`，只列出可供选择的版本。
静态契约中的 `runtime.status=not-probed` 永不触发 COM 连接或模型调用。

`runtime_status.engineering_runtime` 也不会激活 Multisim：worker 依赖不满足时为
`blocked-runtime`；仅模板包不完整时为 `incomplete-template-pack`，不据此禁用所有
已具备模板的工作流；依赖均通过时仍为 `unverified`。实际执行须检测真实版本、
核验所需模板与模型，并对新工程做保存重开、引脚/网络/参数/模型回读、仿真与报告验收。

## 开发衔接

无源与理想运放方案已进入统一 `CircuitDesign` 预览与审批路径，见
[有源编译合同](ACTIVE_SIGNAL_COMPILER.md)。显式激励/夹具的摘要绑定与新模板原生验收
已经落地，具体范围见 [本轮原生记录](ACTIVE_SIGNAL_NATIVE_ACCEPTANCE_20261011.md)。
`generic_compiler.fixture_support` 与 `approved-ideal-signal-fixture` 工作流单独标记该路径，
不将基础未加源预览或任意有源电路提升为已验证。固定双板传感器示范和复杂数字
单板/双板回归已有独立记录；下一步转入混合数字/模拟负载与更多版本独立验收。
增加编译选项须同时更新实际支持矩阵和有界回归；增加元件或版本须补独立清单与
原生实验。不能仅修改发现元数据来开放未经实现或验收的能力。

Discovery is additive and shared by MCP, CLI and the loopback API. Recorded
native cases remain version-specific documentation evidence, separate from
compiler coverage, profile visibility and current-machine execution readiness.
