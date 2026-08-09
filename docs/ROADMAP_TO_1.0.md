# Multisim MCP 1.0 路线图

当前公开版本仍为 `v0.1.0-alpha.3`。主分支将分阶段完成 1.0 能力，期间不发布新的
PyPI、MCP Registry 或 GitHub Release；所有阶段及真实 Multisim 回归通过后，下一次
公开发布直接定为 `v1.0.0`。

## 第一阶段：MCP 2 平台层（已完成）

- [x] 迁移到 MCP Python SDK 2.x，并由同一服务自动兼容 2025-era 和
      `2026-07-28` 客户端。
- [x] 为每个请求提供 `server/discover`、现代无状态协议和旧初始化握手回退。
- [x] 将全部 MCP tool 调用串行到一个专用、已初始化的 Multisim COM 线程。
- [x] 为完整实验签发不包含本机路径的 `experiment_id`。
- [x] 通过 `multisim://experiments/{experiment_id}/...` 暴露报告、原理图、
      CSV、SVG、网表、`.ms14`、raw、命令和日志。
- [x] 增加创建实验、调试、比较、撰写报告和指标验证五个中英双语 Prompt。
- [x] 为 `run_circuit_experiment` 和 `register_experiment_artifacts` 提供明确的
      output schema 与结构化结果校验。
- [x] 验证 32 位 Python、Multisim 14.3、现代/旧 stdio 协议及真实分压器完整实验。

## 第二阶段：实验任务与容错引擎（已完成）

- [x] 引入稳定的实验状态机和原子 JSON 持久任务清单。
- [x] 支持排队、进度、取消、总超时、心跳超时和阶段性检查点。
- [x] 隔离同名输出，并通过进程内占用检查和跨进程文件租约保护发布事务。
- [x] 在独立 worker 进程中运行实验；Multisim 崩溃或无响应时终止该 worker，
      保持 MCP 前端可用并继续处理下一任务。
- [x] 保持任务存储/状态机与 MCP transport 解耦，为官方 Tasks 建立稳定映射边界。
      MCP Python SDK 2.0 当前仅提供 Tasks 类型、尚无高层 server/client handler；待其
      提供正式接口后，只增加协议适配层，不迁移持久数据。

## 第三阶段：测量与设计指标验证

- [ ] 定义 `ExperimentSpec` 和可计算的设计要求。
- [ ] 支持增益、带宽、截止频率、上升时间、过冲、纹波、功耗等自动测量。
- [ ] 增加参数扫描、容差、温度和 Monte Carlo 分析。
- [ ] 输出逐项 PASS/FAIL/未验证结果，不根据缺失数据猜测。
- [ ] 支持理论值、仿真值与误差的结构化比较。

## 第四阶段：高价值元件、仪器与正式报告

- [ ] 增加变压器、电位器、继电器、晶振及常用功率半导体适配器。
- [ ] 增加 D/T 触发器、计数器、移位寄存器和混合信号器件。
- [ ] 增加万用表、Bode Plotter 和 Logic Analyzer。
- [ ] 输出独立 HTML/PDF、中英双语报告和可复现 `manifest.json`。
- [ ] 完成公开元件适配器接口、贡献模板和兼容性矩阵。

## 1.0 发布门槛

- [ ] 全部无 COM、现代/旧协议、打包和安全测试通过。
- [ ] Windows 32 位 Python + Multisim 14.3 完整回归通过。
- [ ] wheel/sdist 不包含 NI 派生模板、实验数据或开发环境。
- [ ] 安装、迁移、故障恢复、贡献和中英双语用户文档完成。
- [ ] PyPI、官方 MCP Registry、GitHub Release 和社区目录信息一次性更新到
      `v1.0.0`。

## English summary

The public release remains `v0.1.0-alpha.3`. Development proceeds through four
gated phases—MCP 2 foundations, resilient experiment jobs, measurable design
requirements, and high-value components/reports. No intermediate package or
registry release is planned; the next public release is `v1.0.0` after all
gates and real-Multisim regressions pass.
