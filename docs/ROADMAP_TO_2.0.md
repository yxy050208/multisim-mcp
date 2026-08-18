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

- [ ] 定义 `EdaBackend` 能力接口和能力发现结果。
- [ ] 定义第一版 `CircuitDesign`、`DesignPatch` 和 `ArtifactSet`。
- [ ] 把实验执行从 MCP 工具函数迁移到应用服务。
- [ ] 将 Multisim COM 操作保留在独立 32 位 worker 中。
- [ ] 为项目目录、实验目录和优化目录增加版本化 manifest。
- [ ] 保持现有 51 个工具、19 个 Resource 模板和 5 个 Prompt 兼容。
- [x] 规划 DeepSeek 与 DeepSeek Harness 的分层适配。
- [x] 增加 DeepSeek Harness 客户端配置生成器。
- [x] 增加 `core`、`experiment`、`optimization` 和 `full` Tool Profile。
- [x] 为不消费 MCP Resources 的客户端增加有界产物 Tool 等价入口。
- [x] 为不消费 MCP Prompts 的 Harness 客户端增加五个项目级 Skill。
- [x] 增加固定版本兼容清单、本地契约门禁和非阻塞上游漂移监控。
- [x] 增加可独立安装的 Harness bundle 源码和固定版真实启动烟雾测试。

阶段门禁：现有 1.0 回归全部通过，MCP 适配器与核心服务之间不共享
传输层对象，Multisim 后端可由无 COM 的假后端替换测试。

## 阶段 B：1.2 诊断、纠错与参数优化

第一版只允许有界参数修改，不进行任意拓扑重写。

- [ ] `diagnose_design`：结构、偏置、饱和、收敛和指标失败诊断；
- [ ] `propose_design_changes`：生成结构化、可审查的 `DesignPatch`；
- [ ] `apply_design_patch` / `revert_design_patch`：事务化修改和回滚；
- [ ] `optimize_design`：目标、硬约束、范围和最大实验预算；
- [ ] `compare_design_variants`：基线、候选和误差证据比较；
- [ ] E12/E24/E48/E96 离散元件值和可用料号约束；
- [ ] 单目标最优解和多目标 Pareto 候选；
- [ ] 每轮修改、仿真、失败和停止原因进入可复现 manifest。

首批基准电路：RC/RLC 滤波器、运放闭环电路、晶体管偏置和基础电源。
找不到满足约束的候选时必须返回明确失败，不允许把最接近的候选标为通过。

阶段门禁：至少三类基准电路能在固定预算内重复得到相同结论；每次修改
都有原因、证据和回滚点；异常终止后能够恢复或安全结束。

## 阶段 C：1.3 开放仿真后端

- [ ] 接入 ngspice，支持 Linux/Docker 中的真实仿真；
- [ ] 对相同 Circuit IR 执行 Multisim/ngspice 差分验证；
- [ ] 记录 SPICE 方言、模型和求解器差异，而不是静默改写；
- [ ] 将公共 CI 从 introspection-only 扩展到开放后端实验回归；
- [ ] 按来源、许可证和 SHA-256 管理用户模型。

阶段门禁：第二个后端无需改动优化器即可完成生成、仿真、测量和验证；
跨后端误差有明确容差和诊断解释。

## 阶段 D：可视化工作台

第一版是本地只读工程审查器，不重造完整 EDA 编辑器。

- [ ] 查看工程、原理图、波形、指标和报告；
- [ ] 展示实验状态、优化收敛、敏感度和候选排名；
- [ ] 比较两个设计版本及其 `DesignPatch`；
- [ ] 在数据结构稳定后加入补丁审批、运行控制和回滚；
- [ ] 主应用保持 64 位，Multisim worker 保持隔离的 32 位进程；
- [ ] GUI 与 MCP 调用同一应用服务，不复制业务逻辑。

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
8. [ ] 独立平台需要内置模型时，再实现通用 `ModelProvider` 和 DeepSeek provider。

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
model-provider concern; DeepSeek Harness support starts with MCP configuration,
profiles, bounded artifact tools, a versioned skill bundle, and compatibility
tests.
