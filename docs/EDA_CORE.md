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

## 当前兼容桥

现有 1.0 MCP 工具尚未改签名，也尚未迁移执行路径。当前 Multisim 适配器要求
`CircuitDesign.source_netlist`；仅包含结构化元件、没有源网表的设计会得到
`source-netlist-required` 诊断。这是刻意保留的编译器边界，不能通过猜测重新生成
可能改变语义的网表。

下一步按以下顺序迁移：

1. 增加 `CircuitDesign` 与受限 SPICE 网表之间的显式转换器；
2. 将完整实验事务移入应用服务，MCP 工具只做参数/结果适配；
3. 将 Multisim COM 调用固定在独立 32 位 worker；
4. 为工程目录、实验目录和优化目录写入版本化 manifest；
5. 在同一服务接口后接入 ngspice，而不修改验证器和后续优化器。

## English summary

The first platformization slice introduces strict, versioned `CircuitDesign`,
`DesignPatch`, and `ArtifactSet` domain objects, a runtime-checkable
`EdaBackend` protocol, a transport-neutral `EdaApplicationService`, and an
injectable `MultisimBackend`. The core imports neither MCP nor COM and is tested
with deterministic no-COM executors on Python 3.10 and 32-bit Python 3.12.

The existing MCP surface remains unchanged. Until a structure-to-SPICE compiler
is added, the Multisim compatibility adapter intentionally requires the original
`source_netlist` and reports a structured error instead of guessing one.
