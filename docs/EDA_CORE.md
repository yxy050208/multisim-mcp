# EDA 核心与后端边界 / EDA Core and Backend Boundary

这是 2.0 路线中阶段 A 的第一批稳定接口。目标是让 MCP、DeepSeek Harness、
未来的本地 API 和可视化工作台共享同一套工程对象与后端服务，而不是复制
`server.py` 中的业务逻辑。

## 已实现对象

`multisim_mcp.eda_core` 当前提供：

- `CircuitDesign`：设计编号、修订、元件、网络、参数、模型来源、注释和可选源网表；
- `DesignPatch` / `PatchOperation`：仅允许元件值、设计参数和注释三类有界修改，
  每项都包含前值、后值和理由，并可生成显式逆补丁；
- `ArtifactSet` / `Artifact`：记录产物种类、位置、媒体类型、大小和 SHA-256；
- `CircuitComponent` / `ModelReference`：保存结构化元件和可追踪模型引用。

持久化顶层对象使用 `schema_version: 1`，拒绝未知字段、非有限数值、重复参考编号、
缺失网络和不一致的产物归属。对象内部的参数与元数据会被冻结，`to_dict()` 输出
普通 JSON 数据，`from_dict()` 执行严格往返验证。

## 后端接口

`multisim_mcp.eda_backend.EdaBackend` 定义四个能力：

1. `discover_capabilities()`：返回版本化 `BackendCapabilities`；
2. `validate_design()`：返回结构化诊断；
3. `create_schematic()`：生成原理图 `ArtifactSet`；
4. `simulate()`：执行分析并返回 `BackendExecution`。

`EdaApplicationService` 负责注册、发现和调度后端，并校验后端编号、操作类型和
产物所属设计。它不导入 MCP 或 COM，因此可在普通 64 位 Python、Linux 和 CI 中
使用假后端测试。

`MultisimBackend` 是第一个适配器。它通过构造函数接收现有低层原理图和仿真执行器，
不会从 EDA Core 初始化 COM。适配器为生成文件建立带 SHA-256 的 `ArtifactSet`，并
报告可编辑模型覆盖和 Multisim 反向网表完整性。

`NgspiceBackend` 是第二个适配器。它在 Linux、macOS 和 Windows 上探测本机运行时，
编译同一 `CircuitDesign`，并执行安全 `op/dc/ac/tran`。执行器不使用 shell，以 `-n`
禁用用户初始化文件，由服务器生成 ASCII raw 控制段，并支持超时、心跳和取消。它只声明
`validate/simulate`，不会冒充可编辑原理图后端。

## 当前兼容桥

`create_schematic_from_netlist` 和 `run_spice_netlist` 已迁移到应用服务。后者新增可选
`backend=multisim|ngspice`，默认仍为 Multisim。内部先将网表导入 `CircuitDesign`，再通过
`EdaApplicationService` 和选定后端执行。原理图兼容桥通过了真实
Multisim 14.3 与双 LM324 宏模型回归；仿真兼容桥通过了 10 V 分压器工作点回归，
得到预期的 5 V 输出并发布五项实验产物。

`SimulationRequest` 支持可选发布目录、超时、最大返回点数和显式
`unsafe_commands` 标志。省略发布目录时，产物仍由后端工作目录追踪；指定目录时，
`ArtifactSet` 优先记录发布副本，避免同名临时文件重复。危险命令标志不会绕过现有
服务器策略，底层执行器仍要求显式环境变量授权。

完整实验新增独立的 `ExperimentRequest` 和 `ExperimentApplicationService`。同步
`run_circuit_experiment`、验证实验和持久任务 worker 共用这一入口；服务统一处理安全
分析命令、绝对输出目录、超时、返回点数、所有者、需求和理论值，再调用可注入事务
执行器。服务模块不导入 MCP 或 COM，也能将信息完整的纯结构化 `CircuitDesign`
显式编译为 SPICE。现有 job 存储格式和 MCP 返回结果没有变化。

文件事务已经从 `server.py` 提取到 `MultisimExperimentPipeline`。流水线接收可注入的
原理图、仿真、正式报告和资源注册执行器，独立负责跨进程输出租约、预检、staging、
绘图、验证、中英报告、完整性门禁、原子发布和逆序回滚。发布中途注入故障的测试确认
旧产物逐项恢复，临时文件与 staging 目录全部清理；模块本身不导入 MCP 或 COM。

完整流水线现支持后端档案。设置 `MULTISIM_MCP_EXPERIMENT_BACKEND=ngspice` 后，同一
`ExperimentApplicationService` 及其参数优化、全局优化、自主纠错消费者无需修改算法。
开放档案发布 `schematic.svg` 标签连接图、PNG 预览和 `backend.json`，明确标记不可编辑，
并省略 `.ms14`。正式报告、资源注册和目录 manifest 按档案验证。公共 Ubuntu CI 安装真实
ngspice，验证低层仿真及完整双语实验事务。

`compare_experiment_backends` 读取两个已注册实验，在公共信号的第一列坐标域上线性对齐，
报告 MAE、RMSE、最大绝对误差、归一化 RMSE 和容差违例；无公共信号或无重叠域时返回
`unverified`，不宣称求解器、方言或厂商模型等价。完整说明见
[`OPEN_EDA_BACKENDS.md`](OPEN_EDA_BACKENDS.md)。

Multisim Automation 与 `.ms14` 编解码现在全部通过版本化 JSON-RPC 进入独立 32 位
worker。该进程保留当前连接/电路状态，主进程通过锁串行调用，并转发长仿真的心跳和
取消；协议错误、RPC 超时或崩溃只终止 worker，下一次调用可重新启动。64 位 Python
前端拉起 32 位 worker 的真实 COM 门禁已经通过。

真实 Multisim 14.3 完整事务门禁生成了可编辑电路、453 点瞬态数据、PNG/SVG、
Markdown、中英 HTML/PDF、日志和 SHA-256 manifest，共 15 个文件并注册 15 个安全
Resource 句柄。

`multisim_mcp.spice_adapter` 提供两个方向清晰、失败关闭的转换边界：

- `circuit_design_from_spice()` 先执行安全策略和语法解析，将解析后的元件、顶层简单
  `.param` 和内联 `.model` / `.subckt` 摘要写入结构化设计，同时原样保留
  `source_netlist` 作为权威输入；
- `circuit_design_to_spice()` 默认返回已经验证的权威源网表。显式关闭
  `prefer_source` 时，只编译当前受支持且信息完整的结构化元件；遇到未知类型、非法
  token、缺失模型或不可表达参数会报错，不做静默猜测。

模型引用记录名称、来源、可选 SHA-256 和许可证声明，但不保存或下载模型正文。
完整实验和 `audit_spice_compatibility` 会进一步记录源网表/实际执行网表哈希、方言
特性、模型内容是否内嵌和求解器版本证据；缺失项保持未验证。因此，从纯结构化对象
生成的网表只覆盖明确支持的元件子集；需要 `.model` / `.subckt` 正文的设计仍应携带
原始 `source_netlist`。复杂表达式、续行 `.param` 和方言改写仍需要运行时验证。

`model-diagnose --enable-patch-preview` 现已把 `DesignPatch` 接入第一个只读消费者。
预览器只允许元件值、设计参数和注释操作，逐项核对 `before` 与固定设计，构造修订号
+1 的内存候选，返回逆补丁和前后结构诊断差异。它不持久化候选，也不调用
`EdaBackend`。当结构修改会使携带的权威 `source_netlist` 过期时，结果显式要求后续
更新源网表，不能把结构化值修改误当成已经改变后端输入。

独立 CLI 事务边界现已支持短期一次性审批、精确摘要/路径绑定、源网表显式再生门禁、
设计 JSON 与回执联合发布，以及需要第二次审批的逆补丁撤销。模型预览工具没有获得这些
写入能力。捕获到发布错误时会恢复原设计；强制终止会保留版本化 journal、候选、回执
staging 和备份，`patch-recover` 在复核 PID、锁、审批及各文件摘要后保守提交或回滚。
五个持久化崩溃点已有无 COM 回归。完整契约见
[`DESIGN_PATCH_TRANSACTIONS.md`](DESIGN_PATCH_TRANSACTIONS.md)。

`design_patch_workflow` 进一步组合 `PreparedDesignPatch`、
`ExperimentApplicationService` 和上述事务边界。验收计划不携带网表，候选设计在内存
应用后由安全转换器生成实际仿真输入；只有逐项要求全部 `pass` 才消费审批并持久化。
`fail`、`unverified` 或实验错误都丢弃未提交候选。审批摘要同时绑定验收计划、实验目录、
工作流清单、超时和点数；实验目录 manifest、验收文件和补丁回执在工作流清单中交叉
记录。提交前/后的崩溃可分别安全终止或根据完整回执补记提交状态。

`DesignOptimizationService` 已在同一服务接口上增加第一版有预算参数优化。它只生成
显式 `set_component_value` 候选，基线固定占用一次实验，全部验收要求作为硬约束，
单目标在可行集中确定性排序。输入设计始终不变；最优候选输出 `DesignPatch` 和可复用
验收计划，仍需独立审批才能持久化。每个补丁、实验目录、错误、目标值与停止原因进入
递归 SHA-256 优化 manifest。真实 Multisim 14.3 分压器门禁在三次实验内稳定选择 2 kΩ
候选。

`DesignVariantComparisonService` 复用同一实验和排名证据层，在统一命令、硬约束与目标下
比较 2–16 个完整 `CircuitDesign`。拓扑可以不同，但电气上完全相同的重复版本会在任何
输出或仿真前拒绝。只有全部要求具有有限实测值并通过的版本进入确定性排名；输入设计
不变，第一名也不会自动采纳。真实 Multisim 14.3 门禁已比较 500 Ω、1 kΩ、2 kΩ 三个
完整分压器设计并选中约 6.667 V 的 2 kΩ 版本。

优化输入现可从 E12/E24/E48/E96 的有界范围确定性生成，并为每个值绑定料号、供应商、
库存和单价。在库与最高变量成本作为额外硬约束；低成本只在电气目标同分时次级排序。
真实 Multisim 门禁已经排除缺货基线和超预算候选，再选择有库存的 2 kΩ 料号。成本证据
只覆盖优化变量，不宣称为完整 BOM。ngspice 现已接入同一公共边界；下一步是跨方言/模型
差异档案和更多双运行时校准基准。

## English summary

The first platformization slice introduces strict, versioned `CircuitDesign`,
`DesignPatch`, and `ArtifactSet` domain objects, a runtime-checkable
`EdaBackend` protocol, a transport-neutral `EdaApplicationService`, and an
injectable `MultisimBackend`. The core imports neither MCP nor COM and is tested
with deterministic no-COM executors on Python 3.10 and 32-bit Python 3.12.

The public compatibility surface keeps Multisim as its default.
`create_schematic_from_netlist` and `run_spice_netlist` are routed through the
application service; the latter can select Multisim or ngspice. The explicit
SPICE adapter preserves a validated source netlist as authoritative and only
compiles its documented structured subset when requested; unsupported or
incomplete constructs fail closed instead of being guessed. Unsafe command
execution still requires the existing explicit server-side opt-in.

Complete synchronous, verified, and durable-worker experiments now share a
transport-neutral `ExperimentRequest` and `ExperimentApplicationService` with
an injectable transaction runner. A real Multisim transient gate produced 453
points and the complete 15-file bilingual artifact transaction without changing
the MCP result or persisted-job format.

The ngspice profile runs the same experiment/verification/optimization services
without algorithm changes, emits an honest non-editable SVG/PNG connectivity
graph instead of `.ms14`, and is exercised by real Ubuntu CI. The differential
tool aligns common signals and reports tolerance-based numerical error without
claiming dialect, model, or solver equivalence.

`MultisimExperimentPipeline` now owns staging, plotting, verification, bilingual
reports, completeness gates, atomic publication, and reverse-order rollback
outside `server.py`. Injected mid-publication failures prove that prior artifacts
are restored and temporary state is removed.

`design_patch_workflow` now composes the in-memory patch service, experiment
service, and approval-gated transaction layer. It derives the simulated netlist
from the approved candidate and persists only an all-pass result. Failed,
unverified, or errored candidates are discarded before publication. A durable
workflow manifest cross-links the verification plan, experiment manifest,
verification evidence, approval, and patch receipt, including safe recovery on
both sides of the commit boundary.

`DesignOptimizationService` now provides the first budgeted optimization slice
on the same boundary. It evaluates the baseline and deterministic explicit
component-value patches, treats every requirement as a hard constraint, ranks
one measured objective only within the feasible set, and never mutates the
source design. Patch, experiment, failure, objective, stop-reason, and recursive
SHA-256 evidence is persisted; a selected patch still requires separate verified
approval. A real three-experiment Multisim 14.3 divider gate selects 2 kOhm.

`DesignVariantComparisonService` applies the same hard-constraint evidence
rules to 2–16 complete designs, including different topologies. It rejects
electrically duplicate inputs before execution, deterministically ranks only
finite measured all-pass variants, and never mutates or adopts a design. A real
Multisim 14.3 gate ranked three complete dividers and selected the 2 kOhm,
approximately 6.667 V variant.

Optimization inputs can now be generated from bounded E12/E24/E48/E96 ranges
and linked to part number, supplier, stock, and unit-cost evidence. In-stock and
maximum variable-cost rules are hard constraints; lower cost only breaks equal
electrical-objective ties. The real gate excludes an out-of-stock baseline and
an over-budget candidate before selecting a stocked 2 kOhm part. Reported cost
covers optimized variables, not a complete BOM.

All Multisim Automation and codec calls now cross a versioned JSON-RPC boundary
into a stateful 32-bit worker. A 64-bit frontend successfully started the worker
and retained a real Multisim 14.3 circuit across calls; heartbeat forwarding,
cancellation, timeouts, crash restart, and serialized concurrency are covered.
