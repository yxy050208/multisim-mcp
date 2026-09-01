# Multisim MCP 2.0 综合开发路线

本文定义 `v1.0.0` 之后的开发顺序。核心目标不是继续增加彼此独立的
MCP 工具，而是把已经验证的 Multisim 实验能力发展为可诊断、可纠错、
可优化并可接入多个 EDA 后端的工程内核。

## 产品决策

- MCP 在 2.0 之前继续作为主要交付接口。
- 从 1.1 开始，领域模型、任务、优化和报告不得依赖 MCP 上下文。
- Multisim 作为第一个 `EdaBackend`，不再充当通用数据模型。
- 纠错与参数优化优先于大规模 EDA 扩展和完整 GUI。
- 可视化工作台先做只读工程审查，再增加修改审批和运行控制。
- DeepSeek、Codex、Claude 等模型都是入口，不进入仿真核心。

## 目标架构

```text
AI client / Visual Workbench
            |
   MCP adapter / Local API
            |
     Application service
            |
  EDA Core + Optimization Core
      |         |          |
 Multisim    ngspice     KiCad
```

建议的稳定对象：

- `CircuitDesign`：元件、网络、参数、模型引用和设计注释；
- `DesignRequirements`：指标、容差、优先级和硬约束；
- `SimulationPlan`：工作点、DC、AC、瞬态和批量分析；
- `DesignPatch`：带理由、前值、后值和回滚信息的最小修改；
- `SimulationResult`：波形、测量、诊断和后端元数据；
- `OptimizationRun`：候选、目标函数、约束、停止原因和最优解；
- `ArtifactSet`：原理图、网表、BOM、数据、日志和报告清单。

所有持久化对象都必须有 `schema_version`，且升级过程可测试、可回滚。

## 阶段 A：1.1 平台化基础

目标是在不破坏 1.0 MCP 接口的前提下建立可复用内核。

- [x] 定义 `EdaBackend` 能力接口和能力发现结果。
- [x] 定义第一版 `CircuitDesign`、`DesignPatch` 和 `ArtifactSet`。
- [x] 增加传输无关的后端注册/调度服务、可注入 Multisim 适配器和无 COM 假后端门禁。
- [x] 增加显式受限 SPICE 转换器，并将首个原理图 MCP 工具接入兼容桥。
- [x] 将独立 SPICE 仿真工具接入应用服务并保持安全命令策略和返回契约。
- [x] 把同步、验证和持久 worker 实验入口从 MCP 工具迁移到应用服务。
- [x] 将事务发布/报告执行器从 `server.py` 提取为可注入独立流水线组件。
- [x] 将 Multisim COM/编解码操作保留在独立 32 位 worker 中，支持 64 位前端。
- [x] 为项目目录、实验目录和优化目录增加版本化 manifest。
- [x] 保持原有 55 个工具兼容，以 `optimize_design`、
  `compare_design_variants`、`submit_design_optimization`、`diagnose_design`、
  `evaluate_design_patch`、`global_optimize_design` 和
  `autonomous_correct_design`、`submit_global_optimization` 和
  `submit_autonomous_correction` 与跨后端差分、SPICE 兼容性审计扩展为 66 个工具；
  20 个 Resource 模板和 5 个 Prompt 保持兼容。
- [x] 规划 DeepSeek 与 DeepSeek Harness 的分层适配。
- [x] 增加 DeepSeek Harness 客户端配置生成器。
- [x] 增加 `core`、`experiment`、`optimization` 和 `full` Tool Profile。
- [x] 增加审批凭证完整性重建、受限模型重哈希和首个 `signal-passive` 引脚级网表编译预览；
  其余逻辑方案继续失败关闭，网表批准、成图和仿真仍为独立后续门禁。
- [x] 增加 `approve_executable_netlist`：将人工确认绑定到编译预览、`CircuitDesign` 摘要
  和 SPICE SHA-256；批准后只开放成图准备，文件写入和仿真仍保持关闭。
- [x] 为不消费 MCP Resources 的客户端增加有界产物 Tool 等价入口。
- [x] 为不消费 MCP Prompts 的 Harness 客户端增加五个项目级 Skill。
- [x] 增加固定版本兼容清单、本地契约门禁和非阻塞上游漂移监控。
- [x] 增加可独立安装的 Harness bundle 源码和固定版真实启动烟雾测试。

阶段门禁：现有 1.0 回归全部通过，MCP 适配器与核心服务之间不共享
传输层对象，Multisim 后端可由无 COM 的假后端替换测试。

## 阶段 B：1.2 诊断、纠错与参数优化

基础入口保留有界参数修改；高级入口已经允许在严格补丁校验、SPICE 编译和真实实验
验收下执行元件增删/替换、网络与引脚连接修改。这里的“全局”指声明的有限混合设计域，
不是对任意非凸电路绝对最优的数学证明。

- [x] `diagnose_design`：确定性、只读地组合结构检查、实验 manifest/网表绑定、
  指标失败/未验证、求解器收敛特征以及有工作点证据时的 BJT/运放饱和诊断；
- [x] `propose_design_changes` 第一版：模型生成结构化 `DesignPatch`，核对基线并在内存
  候选上预览逆补丁和结构差异，不持久化、不仿真；
- [x] `apply_design_patch` / `revert_design_patch` 第一版：本机 CLI 使用短期一次性审批令牌，
  精确绑定摘要和路径，事务化写入设计 JSON 与回执，并用独立审批撤销；
- [x] 补丁事务持久化 journal 与 `patch-recover`：覆盖五个崩溃点，自动策略只提交完整
  目标+回执，否则回滚未消费交易，摘要异常时失败关闭；
- [x] 补丁预提交仿真、要求验收、失败候选自动丢弃与实验/补丁 manifest 闭环；
- [x] `evaluate_design_patch`：固定运行原设计和一个明确内存候选，在相同硬性验收计划下
  保存前后诊断、补丁/逆补丁与递归证据；全通过也只标记为需另行审批的可采用候选；
- [x] `optimize_design`：显式或 E12/E24/E48/E96 标量值、单目标、电气/在库/变量成本
  硬约束和最多 32 次实验预算（含基线），只输出需另行审批的最优补丁；
- [x] `compare_design_variants` 第一版：在统一验收计划下比较 2–16 个完整设计/拓扑，
  只排名具备有限实测证据的全通过版本，并保留错误与递归 manifest；
- [x] E12/E24/E48/E96 离散值、料号/供应商/库存/单价记录、在库与最高变量成本约束，
  以及目标同分时的低成本次级排序；
- [x] 确定性单目标最优解（含基线最优与无可行解）；
- [x] `global_optimize_design`：参数与拓扑混合设计域、小域穷举、确定性 Halton 覆盖、
  硬约束筛选、epsilon-aware Pareto 前沿和加权折中推荐；
- [x] `autonomous_correct_design`：有界诊断—提案—真实实验闭环，只接受严格改进并把
  多轮结果合并为相对原 revision 的可逆补丁；
- [x] 多目标 Pareto 候选；
- [x] 第一版每轮修改、仿真、失败、目标值和停止原因进入可复现 manifest；
- [x] `submit_design_optimization` 复用隔离持久任务，在 MCP 重启后重验并复用已完成
  候选；未提交候选使用新 attempt 目录原地续跑，CLI 也提供显式 `--resume`。
- [x] `submit_global_optimization` 与 `submit_autonomous_correction` 复用隔离持久任务，
  分别实现候选级和纠错轮次级的证据重验、断点恢复、取消和重启续跑。
- [x] `benchmark-suite` 固化 RC、RLC、运放、BJT 和稳压电源五类基准；本机真实
  Multisim 门禁 5/5 通过，并保存可校验的套件与每个用例证据。

首批基准电路：RC/RLC 滤波器、运放闭环电路、晶体管偏置和基础电源。
找不到满足约束的候选时必须返回明确失败，不允许把最接近的候选标为通过。

阶段门禁：五类基准电路已在固定预算内得到预期结论；每次修改都有原因、证据和回滚点；
异常终止后能够恢复或安全结束。发布前仍需在干净环境重复真实套件，避免把单机结果
误当作跨版本保证。

## 阶段 C：1.3 开放仿真后端

- [x] 接入 ngspice，支持 Linux 中的真实仿真和完整开放实验包；
- [x] 对已注册 Multisim/ngspice 实验执行信号对齐、容差化数值差分；
- [x] 记录 SPICE 方言、模型和求解器差异，而不是静默改写；
- [x] 将公共 CI 从 introspection-only 扩展到真实 ngspice 后端与完整实验回归；
- [ ] 按来源、许可证和 SHA-256 管理用户模型。

阶段门禁：第二个后端无需改动优化器即可完成生成、仿真、测量和验证；
跨后端误差有明确容差和诊断解释；源网表、实际执行网表、模型指纹和求解器
版本都在实验审计产物中保留，缺失证据会显式降级而不是被静默补全。

## 阶段 D：可视化工作台

第一版是本地只读工程审查器，不重造完整 EDA 编辑器。

- [x] 建立有界、只读的项目审查快照服务和 `inspect-project` CLI；只读取版本化
  directory manifest，报告子目录完整性错误，不修改工程；
- [x] 提供 React + Vite 只读工作台原型，可加载快照 JSON 并展示项目树、状态、
  产物预览和 SPICE 证据；
- [x] 提供仅绑定 loopback 的本机快照 API，固定工程根目录、限制扫描深度/条目数，
  工作台可主动连接刷新快照；API 不接受任意路径、不写入文件、不启动仿真；
- [x] 查看工程、原理图、波形、验收指标和有界报告预览；媒体只来自已验证 manifest，
  SVG 以隔离图片加载，浏览器不执行其内容；
- [x] 展示实验/优化运行状态、单目标收敛、候选排名、多目标 Pareto 前沿和推荐解；
- [x] 增加基于已测候选范围的局部/全局敏感度视图；结果明确标注为描述性观测，
  不冒充因果导数或全局最优证明；
- [x] 增加敏感度驱动的有界 `search_plan` 建议；仅供人工复核，预算分配不超过剩余
  探索预算，且不自动运行实验或修改优化规格；
- [x] 为 `search_plan` 提供不可执行的 `spec_draft` 复制/下载载荷；明确区分审阅、批准
  与后续任务提交；
- [x] 为仿真计划提供受控交接包及 `execute-handoff` CLI：默认只校验路径、网表和审批
  一致性，明确确认后按先成图、后仿真执行；长实验可将仿真排入 durable worker，并沿用
  manifest/审批归属回读；
- [x] 增加提交预检门禁，展示预算和值域上限及人工批准要求；预检不签发令牌、不启动任务；
- [x] 增加独立搜索草案审批边界：一次性短期令牌精确绑定条目、优化 ID、规范化源设计/来源规格、
  完整草案摘要和预算；`search-plan-verify` 仅校验不消费；
- [x] 增加 `search-plan-submit`：重新校验并消费一次性令牌，把派生正式规格写入 durable
  optimization job queue；提交命令不启动短命 worker，长驻 MCP worker 复用同一作业目录，
  并用 approval ID 保证入队后崩溃重试幂等；拓扑选择仍要求显式操作；
- [ ] 比较两个设计版本及其 `DesignPatch`；
- [x] 先提供只读 baseline/candidate 证据对比：对已验证实验/优化条目的验收指标或测量
  信号做并列查看与 SPICE 名称归一化；拓扑差异和 `DesignPatch` 仍需后续审批工作流；
- [x] 在 Workbench Inspector 中接入 manifest 校验的只读 Patch review：展示
  `patch-evaluation` / `autonomous-correction` 的有限差异、诊断增量与审批预检，
  不签发浏览器令牌；
- [x] 在 Patch review 中增加脱敏事务状态回读：候选等待显式审批，受信任工作流摘要的有效
  交易显示为已提交；回读不携带回执路径、审批令牌或浏览器执行能力；
- [x] 增加 `workbench` 单命令本地应用入口：构建后的 React 页面与 loopback API 共用一个
  进程/端口，并在方案页提供无工具、只读的 AI 设计助手；
- [ ] 在数据结构稳定后加入补丁审批、运行控制和回滚；
- [x] 主应用支持 64 位，Multisim worker 保持隔离的 32 位进程；
- [ ] GUI 与 MCP 的写入/执行调用仍需接入同一应用服务，不复制业务逻辑（当前 MVP 已共用
  只读 API，写入和仿真继续保留在受控 MCP/CLI 边界）。

阶段门禁：关闭并重新打开项目后能恢复完整工程状态；界面崩溃不会破坏
实验队列；所有会覆盖工程的操作都需要明确审批。

## 阶段 E：KiCad 与工程输出

- [ ] 生成结构清晰的 KiCad 原理图；
- [ ] 符号、封装和引脚映射具有来源记录；
- [ ] 运行 ERC，导出 BOM、网表和预览；
- [ ] 生成受约束的初始 PCB 工程；
- [ ] 将 DRC、热、成本和可制造性约束纳入后续优化。

自动 PCB 布局布线不属于初期承诺。没有 DRC、制造和人工审查证据时，
不得把生成的 PCB 标记为可生产。

## DeepSeek 与 Harness 主线

DeepSeek API 是模型提供方；DeepSeek Harness 是代理运行时。适配分为：

1. [x] MCP 客户端配置和 stdio 契约测试；
2. [x] `core` / `experiment` / `optimization` / `full` Tool Profile；
3. [x] 为不支持 MCP Resources 的客户端提供工具等价接口；
4. [x] 为不支持 MCP Prompts 的客户端提供版本化 Harness Skill Bundle；
5. [x] 建立机器可读版本门禁和非阻塞上游兼容监控；
6. [x] 提供独立、带版本门禁的 DeepSeek Harness bundle 源码；
7. [ ] 将 bundle 通过 Trusted Publishing 发布到 npm；
8. [x] 提供只保存环境变量引用的版本化 Provider 配置、自动发现和连接诊断；
9. [x] 实现通用 `ModelProvider` 调用、取消、显式回退与白名单有界工具循环；
10. [x] 绑定四个固定只读 EDA 工具，支持严格设计 JSON、安全网表和有界诊断 CLI；
11. [x] 接入只读实验/验收证据，并加入不持久化的 `DesignPatch` 提案预览；
12. [x] 加入独立短期审批令牌及 CircuitDesign JSON 事务应用/回滚；
13. [x] 加入 crash-recovery journal、存活 PID/锁门禁及显式恢复 CLI；
14. [x] 加入审批绑定的预提交仿真验证、失败候选自动丢弃与崩溃恢复清单；
15. [x] 加入多候选参数优化进度、持久检查点与任务查询；流式 UI 留在工作台阶段。

DeepSeek API Key 不得传给 Multisim MCP。Harness 当前处于 Developer Preview，
集成测试应固定已验证版本，并用非阻塞的上游兼容任务监控新版本。

## 不在近期范围内

- “覆盖所有元器件”或来源不明的厂商模型自动下载；
- 任意拓扑的无人审查自动重写；
- 自研 SPICE 求解器、完整原理图编辑器或 PCB 路由器；
- 把本地 stdio MCP 直接暴露到公网；
- 将任何特定模型的响应格式写入 EDA Core。

## English summary

The post-1.0 plan keeps MCP as the primary interface while extracting a
transport-neutral EDA and optimization core. Bounded diagnosis and parameter
optimization come first, followed by an ngspice backend, a read-only visual
workbench, and then KiCad engineering outputs. DeepSeek API support remains a
model-provider concern. Secret-free configuration, explicit connection probes,
bounded Chat Completions, cancellation, opt-in failover, and an allowlisted tool
loop are now available outside the EDA core. Four fixed read-only EDA bindings
now inspect strict CircuitDesign JSON or safely parsed SPICE without backend
execution. Read-only experiment evidence, transactional actions, streaming,
and UI remain workbench milestones. The workbench now exports a validated
schematic-first controlled handoff that can be executed through an explicit
local CLI confirmation.
DeepSeek Harness support starts with MCP configuration, profiles, bounded
artifact tools, a versioned skill bundle, and compatibility tests.
