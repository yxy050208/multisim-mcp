# AI 工程电路工作台 / AI Engineering Circuit Workbench

状态：`1.3 → 2.0` 产品方向草案。中文为主要说明，英文摘要位于文末。

## 一句话定位

Multisim MCP 不把自己定位成“会生成电路图的聊天机器人”，而是一个面向 AI
Agent、工程师和工业团队的 **AI 工程电路控制台**：用统一的标准流程连接模型、
EDA、仿真、硬件和工程知识，并把每一次设计决策变成可验证、可比较、可回滚的证据。

核心原则是：

> 模型负责提出与解释；工作台负责约束、执行、测量、判定、留证和回滚。

## 产品边界

### 我们要提供

- 从自然语言需求到结构化设计要求、技术方案和实现路径的标准流程；
- Multisim、ngspice 以及后续 EDA 后端的统一适配边界；
- 电路生成、仿真、测量、验收、纠错、优化和报告导出的闭环；
- 成本、容差、功耗、温升、可靠性、库存和制造规则等工程约束；
- 面向 Agent 的 MCP、CLI、loopback API、SSE 和 Harness 接口；
- 可审计的元件/模型/规则/实验“全面手册”，而不是不可追溯的长文本回答。

### 我们暂不承诺

- 任意拓扑的数学全局最优；
- 自动覆盖所有厂商元件和所有商业 EDA；
- 无人审查的 PCB 生产级布局布线；
- 没有真实后端或硬件证据的“工业级正确性”；
- 把某个模型品牌或某种提示词写死在 EDA Core 中。

## 标准工程流程

所有入口（极简 UI、MCP、DeepSeek Harness、CLI 或未来 SDK）都进入同一条流程：

```text
描述目标
   ↓
规范化需求与硬约束
   ↓
生成 2–3 个技术方案
   ↓
比较优缺点并由用户选择
   ↓
生成设计规格、网表和元件解析
   ↓
显式审批
   ↓
成图、仿真、测量和验收
   ↓
诊断、纠错、优化和方案对比
   ↓
导出电路图、数据、BOM、证据包和报告
```

在用户确认方案之前，只生成有界的方案和规格，避免提前消耗仿真资源或模型 token。
任何写入工程、启动仿真、采用候选或覆盖文件的动作都必须经过明确审批。

## 分层架构

```text
极简 UI / MCP / DeepSeek Harness / CLI / SDK
                         │
              Agent API + Workflow Orchestrator
                         │
       EDA Core + Constraint Engine + Evidence Store
             │              │                 │
        Multisim        ngspice/KiCad       Models/Rules/Handbook
                         │
                 Hardware-in-the-loop
```

### 1. 入口层

模型可替换。OpenAI、DeepSeek、Ollama、其他 OpenAI-compatible 服务只实现
`ModelProvider`，不能决定电路状态机、审批策略或实验结果。

### 2. 编排层

编排层负责方案阶段、审批令牌、任务队列、取消/恢复、事件流和权限边界。它只调用
领域服务，不在 UI 或模型适配器中复制业务逻辑。

### 3. 工程核心

稳定对象包括 `CircuitDesign`、`DesignRequirements`、`SimulationPlan`、
`DesignPatch`、`SimulationResult`、`OptimizationRun` 和 `ArtifactSet`。每个对象
都有版本、来源和完整性摘要。

### 4. 后端适配器

后端只负责格式转换、执行和能力报告。优化器、验收器和证据系统不应因更换
Multisim/ngspice/KiCad 而重写。

### 5. 知识与手册

手册采用结构化条目，而不是一份不可维护的百科文本。每条知识按以下维度索引：

- 元件、引脚、模型和许可证来源；
- 拓扑、应用场景和推荐工作区间；
- 常见故障、可观测症状和诊断规则；
- 后端差异、仿真设置和已验证示例；
- 版本、SHA-256、适用范围和证据等级。

模型可以解释手册内容，但不能把未验证的手册条目直接标记为实验结论。

## 极简界面原则

第一屏只保留四件事：

1. **我要实现什么**：自然语言目标和约束；
2. **有哪些方案**：默认推荐方案、备选方案、成本/风险/性能差异；
3. **现在进行哪一步**：等待选择、等待审批、仿真中或需要处理失败；
4. **证据结果**：电路图、波形、指标、差异和报告下载。

高级设置（模型、后端、容差、预算、原始网表、日志和高级诊断）按需展开，不在首次
使用时同时出现。页面只读预览与实际执行必须有清晰的视觉区分。

## 工程质量指标

产品升级不能只看模型回答是否“像专家”，而应持续记录：

- 方案可执行率和网表编译成功率；
- 仿真收敛率、指标满足率和测量误差；
- 自动纠错后的严格改进率、修复轮数和失败原因；
- 容差/功耗/温升/成本约束满足率；
- 证据包完整率、重现成功率和回滚成功率；
- 每个模型的 token、延迟和单位成功成本。

公开基准优先覆盖 555 波形发生器、RC/RLC、运放、晶体管、电源和机器人控制/电源
子系统。模型只作为变量，验收指标保持不变。

## 里程碑

- **1.3：可信闭环**：真实 Multisim 回归、稳定 Agent API、SSE 任务事件、极简方案页、
  可重现证据包；
- **1.4：工程优化**：容差/鲁棒优化、功耗/温升/BOM 约束、硬件在环接口和模型路由；
- **1.5：互操作**：KiCad/SPICE 工程导入导出、ERC/DRC 证据、统一 BOM 和制造检查；
- **2.0：独立平台**：桌面/本地服务发行版、插件式 EDA 后端、团队权限、项目历史和
  可扩展手册市场。

每个里程碑都必须保留 MCP/CLI 兼容入口；独立 UI 是入口升级，不是另起一套业务核心。

## English summary

Multisim MCP is evolving into an **AI engineering circuit control plane**, not a
generic circuit chatbot. Models propose and explain; the product constrains,
executes, measures, verifies, records, and rolls back. A single approval-gated
workflow serves the minimal UI, MCP, DeepSeek Harness, CLI, and future SDKs.

The durable moat is deterministic EDA execution, simulation-backed correction and
optimization, component/model provenance, reproducible evidence, and a structured
engineering handbook. Model providers remain replaceable adapters. The near-term
order is trustworthy Multisim execution, robust optimization, minimal UI, then
broader EDA interoperability and the 2.0 independent platform.
