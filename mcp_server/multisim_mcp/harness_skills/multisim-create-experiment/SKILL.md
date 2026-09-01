---
name: multisim-create-experiment
description: 根据工程要求生成安全、可复现的 Multisim 电路实验并核对理论与仿真结果；use when creating a new circuit, schematic, simulation, or experiment report
---

# 创建 Multisim 电路实验

本 skill 使用 Multisim MCP 工具。下文写原始工具名；DeepSeek Harness 默认会将其
显示为 `mcp__multisim__<工具名>`，如果配置使用了不同 `serverName`，请采用实际前缀。

1. 收集电路功能、输入输出、供电、频率、容差、分析类型、输出目录和验收指标。
   对会改变设计结论的缺失条件先向用户确认。
2. 调用 `runtime_status`。完整工作流未就绪时，报告具体缺口，不要反复尝试 COM。
3. 在生成网表前调用 `plan_design_options`。展示 2--4 个取舍不同的技术方案，说明
   默认推荐、假设、风险和实现路径；规划结果是只读的，不会生成文件或启动仿真。
   等用户明确选择 `plan_id` 和 `option_id` 后，调用 `select_design_option` 锁定摘要。
4. 调用 `prepare_design_specification`，展示并补齐电气参数、模块、分析计划和验证门槛。
   只有 `ready_for_netlist_draft=true` 且用户审阅规格后，才能进入下一步；不要把参数完整
   表述为拓扑、额定值或仿真已经通过。
5. 用户明确确认当前规格摘要后调用 `prepare_netlist_draft`，先展示逻辑模块、网络、待选
   器件与派生约束。该结果不是 SPICE；不得把 `ready_for_component_resolution` 解释为
   已可成图或仿真。
6. 调用 `resolve_component_requirements` 展示候选器件族、额定值计算依据和模型来源状态。
   用户提交具体型号、额定值和模型摘要后，调用 `approve_component_resolution` 绑定人工
   审阅。只有审批凭证的 `ready_for_executable_netlist=true` 时，才可调用
   `compile_executable_netlist`。当前编译器只支持 `signal-passive` 的明确器件族组合；其他
   方案应报告支持缺口，不得自行拼接引脚。编译结果仍需向用户展示 SPICE、计算值、假设和
   门禁，并停在 `approve_executable_netlist`；没有新的明确审批前不得成图或仿真。外部模型
   只允许来自服务端受限模型根目录，并必须重新哈希。
7. 得到另行批准的完整网表和分析计划后，成图阶段将编译预览、网表审批凭证和其中的原样
   `spice_netlist` 传给 `create_schematic_from_netlist`；该工具会重新校验绑定关系，只写入
   获批原理图而不启动仿真。随后调用 `approve_simulation_plan`，把同一网表审批绑定到
   `ExperimentSpec` 的安全命令、测量信号和验收限制；再将三份凭证传给
   `run_verified_circuit_experiment`，由入口在成图和仿真前重新校验。有明确验收指标时优先调用
   `run_verified_circuit_experiment`；长任务可将同三份凭证传给
   `submit_circuit_experiment`，凭证会随作业保存并在隔离 worker 中再次校验；普通实验仍可调用
   `run_circuit_experiment`，长任务则查询提交作业的状态。
   如果用户从工作台下载了 `multisim-approved-experiment-handoff.json`，可以先在可信终端
   执行 `multisim-mcp execute-handoff --handoff <file> --root <project> --json` 做一致性校验；
   只有用户明确确认后才加 `--confirm`，命令会严格先成图、再仿真，且默认不覆盖已有产物。
   时间较长时可使用 `--submit --confirm`：先成图，再将仿真计划排入 durable worker，并把返回
   的 job 句柄交给工作台队列观察。
8. 成功后调用 `get_experiment_summary` 和 `list_experiment_artifacts`。只有需要更多
   文本证据时才分页调用 `read_experiment_artifact`，不要读取二进制内容。
   受控审批运行还应通过 `inspect-project` 或工作台回读目录 manifest，确认
   `approval_provenance` 与当前 simulation-plan/netlist/compiled/spec 摘要一致；
   旧实验或直接执行结果若显示未记录归属，不得冒充本次审批结果。
9. 核对理论值、仿真测量、容差和验证结论。任何缺少数据支持的指标标记为“未验证”。
10. 仅在用户要求导出时调用 `export_experiment_artifact`；导出目录必须已由服务端批准。

最终答复应列出电路假设、网表/分析、关键测量、PASS/FAIL/未验证结论和产物清单。
不要把“最接近要求”写成“通过”，也不要声称自动生成的设计已可直接生产。

## English summary

Create a reproducible experiment from explicit requirements, prefer verified or
durable high-level tools, inspect bounded artifact summaries, and report only
claims supported by simulation evidence.
