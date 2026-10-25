# 工程架构复盘与独立前端决策（2026-10-19）

## 结论

当前项目的核心技术路线是成立的，不建议推倒重写。应保留 Python 领域模型、应用服务、
独立 32 位 COM worker、Multisim/ngspice 后端、版本化审批和实验凭据体系，先做渐进式
架构收敛，再建设独立前端。

独立前端有必要，但它应是一个独立的产品层和独立发布单元，不能把业务逻辑复制到 React、
Electron 或桌面壳中。前端只负责需求输入、方案审阅、审批交接、任务状态和证据展示；
成图、仿真、优化、回滚和原生验收仍由当前后端服务完成。

## 当前工程的实测结构

本次审计基于 `main` 工作树，保留了既有未提交修改，没有重置或覆盖文件。主要模块规模为：

| 模块 | 行数 | 评价 |
| --- | ---: | --- |
| `server.py` | 4671 | MCP 注册、参数校验、编排和兼容入口过于集中 |
| `cli.py` | 3956 | 诊断、配置、审批、优化、Workbench 命令集中在一个文件 |
| `workbench_api.py` | 1823 | API 路由、安全边界和业务调用仍有明显耦合 |
| `schematic_builder.py` | 4754 | 解析、模板、布局、布线、XML 渲染和兼容逻辑集中 |
| `eda_core.py` | 约 1400 | 领域对象边界清晰，可继续作为稳定内核 |
| `workbench_app.py` | 225 | 静态资源宿主，默认依赖外部前端构建产物 |

当前 `main` 包含 Workbench API 和静态资源宿主，但没有被 Git 跟踪的 `workbench/` 前端
源码或 `dist/`。因此 `workbench` 命令的默认路径可能不存在；这不是后端能力失败，而是
发行资产和前端工程尚未在同一发布单元中闭合。另一个工作区已有可构建的 React/Vite 原型，
其 API 调用方向与当前 loopback 合同基本一致，适合作为独立前端候选。

后端边界已经具备较好的基础：

- `CircuitDesign`、`DesignPatch`、`ArtifactSet` 和 `BackendCapabilities` 是传输无关的版本化对象；
- `EdaApplicationService`、`ExperimentApplicationService` 和 `MultisimExperimentPipeline`
  已把大部分业务从 MCP/COM 入口中抽出；
- Multisim COM 与编解码调用集中在独立 worker，64 位主进程与 32 位运行时可以隔离；
- 原生保存重开、拓扑/引脚/参数回读、仿真、报告和 manifest 证据已经形成闭环；
- Workbench API 默认回环、只读，写入需要显式启用并复用审批服务。

## 架构评分与主要问题

这是工程成熟度判断，不是电路覆盖率或商业成功率：

| 方面 | 判断 | 原因 |
| --- | --- | --- |
| 原生运行时隔离 | 强 | COM、32 位、超时、取消、崩溃恢复边界清楚 |
| 领域模型与证据 | 强 | 对象、审批、manifest 和报告可复现 |
| 后端可替换性 | 中上 | Multisim/ngspice 已有适配器，但模板和原生语义仍集中在构建器 |
| MCP/CLI/Workbench 一致性 | 中 | 共享服务已存在，入口文件仍过重，易出现参数和错误处理漂移 |
| 原理图生成与布线 | 中 | 有确定性布局和几何门禁，但构建器过大，版本/模板耦合高 |
| 自然语言到复杂电路 | 中下 | 规则入口和受限编译器可靠，尚未形成任意复杂需求的通用编译链 |
| 前端产品化 | 当前 main 为弱 | API 有，前端源码不在当前发布单元，默认命令存在资产断点 |
| 安装与商业交付 | 中 | 双 Python、授权模板包和版本边界需要产品化安装器处理 |

最需要处理的不是增加更多 MCP 工具，而是减少四种重复：

1. `server.py` 与 `cli.py` 各自处理入口参数、错误包装和流程分支；
2. Workbench API 直接调用多个服务，路由层容易继续变成第二个编排器；
3. `schematic_builder.py` 同时承担解析、模板查找、布局、布线和 XML 输出；
4. 能力发现、文档、工具 profile 和前端页面存在手工同步风险。

## 是否整合架构

建议整合，但只做“门面收敛”，不做大规模重写。目标结构如下：

```text
React/Tauri Workbench     MCP / CLI / Harness
          \                  /
           Transport adapters
                    |
          Application services
                    |
      EDA Core + Evidence Core
          |       |        |
     Layout   Optimization  Approval
          |
   Backend ports / registry
      |                 |
 Multisim worker      ngspice
```

整合原则：

- 领域对象和应用服务是唯一业务来源，入口只做 schema 转换、鉴权/审批检查和结果映射；
- AI 只生成需求、方案和候选 patch，不能直接生成任意 XML、路径或仿真命令；
- 原生验收结果、失败原因和 `pending/unverified/failed` 状态必须原样穿过 MCP、CLI 和 UI；
- `build_schematic()`、`run_circuit_experiment()` 等现有公共门面先保留，内部逐步委托到拆分后的服务；
- 每次拆分都保留旧工具名、旧 JSON 字段和旧 manifest schema，先增加契约测试，再移动实现。

## 推荐的渐进式重构顺序

### 1. 先冻结合同和能力发现

建立一个生成式能力清单，单一来源覆盖工具 profile、后端能力、元件族、目标 Multisim 版本、
证据等级和成熟度。MCP、CLI、Workbench 都读取同一份结果。加入导入图门禁，禁止 `eda_core`
反向依赖 MCP、COM 或 UI。

完成判据：工具数量、schema、错误码和能力快照在三种入口中一致；旧回归不减少；版本未知时
返回 `unverified` 或 `manifest-unavailable`。

### 2. 拆分入口，不动业务语义

把 `server.py` 按领域拆成注册模块，例如 `mcp_tools/planning.py`、`experiment.py`、
`optimization.py`、`layout.py`、`multiboard.py` 和 `native.py`；`server.py` 只保留装配、
资源注册和生命周期。`cli.py` 按命令族拆分，保留现有子命令和 JSON envelope。

完成判据：单个入口模块降到约 800--1200 行以内；工具签名、profile 和错误码不变；每个
领域模块能在无 COM 测试中独立加载。

### 3. 拆分原理图构建器

保留 `schematic_builder` 作为兼容门面，内部依次抽出：

- SPICE/结构化网表解析；
- 元件模板和版本映射；
- 组件放置与信号链排序；
- 正交布线与几何验证；
- XML 组装与 ewd/ewe 编码。

布局和布线优化仍遵守“先电气/拓扑通过，再追求线长和交叉最少”的原则。商业版的跨板全局
搜索、制造约束和高级自动布线不进入本次开源重构。

### 4. 让 Workbench API 变成薄适配器

路由只做请求大小、路径、来源、审批和响应 envelope；规划、编译、实验、优化和证据读取
都调用应用服务。SSE 事件和 durable job 复用已有任务状态，不在前端另建状态机。

## 是否需要独立前端

需要，原因有三点：

1. MCP 客户端适合 Agent 调用，不适合用户持续审阅网表、引脚、候选布局、波形和证据；
2. 商业价值主要来自安装、配置、项目管理、审批和结果回看，这些需要稳定的产品入口；
3. 前端独立发布可以避免把 UI 依赖、商业安装器和本地授权模板带入开源 Python 包。

但不建议现在把前端做成完整 EDA 编辑器，也不建议让它直接控制 Multisim。第一版应是
“工程工作台”，而不是聊天机器人：

- 左侧：项目、需求、任务和证据导航；
- 中间：自然语言需求、方案卡片、参数/器件/网表审阅；
- 右侧：审批状态、原生验收、波形、报告和失败诊断；
- 所有执行按钮显示明确的审批边界，生成交接包后由 MCP/CLI 执行；
- 先支持本机 loopback，后续再包 Tauri/Electron 桌面壳。

这可以借鉴 ARTEX（网安工具）的“模型配置 + Agent 绑定 + 任务链 + 工作区产物”交互和
编排模式，但不集成 ARTEX 的网安模块，也不把 ARTEX 当作 Multisim 后端。我们要做的是
Multisim 专用的同类工程工作台：模型选择、Agent 角色、任务链、重试/故障转移属于我们
自己的前端编排层；Multisim MCP 负责需求合同、网表、元件映射、布局门禁、原生 Multisim
执行、证据和报告。对复杂电路，用户必须能看到方案、元件映射、引脚连接、布局门禁和
Multisim 原生结果，才能判断是否接受。

## 开源版与商业版边界

开源版继续保留：

- MCP、CLI、loopback API 和稳定的应用服务；
- 基本单板参数优化、几何验证和基本多板接口/连接器验收；
- Multisim 14.3 等明确版本和元件族的原生证据；
- 最小只读 Workbench 客户端或公开的前端协议。

商业版重点提供：

- 一键安装、32/64 位环境配置、模板包发现、升级和诊断；
- 完整桌面壳、项目管理、团队权限和模型配置向导；
- 跨板联合参数/拓扑搜索、全局高级布线、制造约束和批量项目管理；
- 商业支持与经过额外版本矩阵验证的交付包。

商业源码、客户数据、私有模型、授权模板和商品 ZIP 继续放在独立私有仓库/目录，不用公开
仓库分支作为隔离手段。

## 最终决策

本次已先落地第一步：新增版本化 `AgentTask`、`TaskChain`、`ArtifactRef`、`ApprovalGate`
和 `TaskEvent` 合同，并增加只读 `POST /api/agent-workflow/plan`。该入口可以生成需求、
方案、元件、网表、布局、原生实验、优化和报告的任务图，但不会调用模型、写文件或启动
Multisim。随后新增 `AgentWorkflowExecutor`，接入需求审查与技术方案规划两个无副作用
处理器，审批、重试和失败关闭由同一状态机负责。新增合同和全量回归均已通过
（`1122 passed, 46 skipped, 382 subtests passed`）。

现在的最优顺序是：先完成后端入口和构建器的渐进式收敛，同时把现有 React/Vite 原型整理成
独立的 Multisim Agent Workbench，借鉴 ARTEX 的工作台和任务链模式，但不建立 ARTEX 依赖。
等单板参数-布局-原生验收闭环再把前端接入真实执行状态。不要复制 ARTEX 的网安能力，也
不要把前端逻辑复制进 MCP 服务。

下一阶段的可审查交付应是：

1. 能力快照和入口一致性门禁；
2. `server.py`/`cli.py` 的首批领域模块拆分；
3. Workbench 前端与 loopback API 的版本化兼容测试；
4. 单板参数与布局候选的真实 Multisim 14.3 闭环证据；
5. 在以上交付稳定后，再决定 Tauri/Electron 和一键安装器。
