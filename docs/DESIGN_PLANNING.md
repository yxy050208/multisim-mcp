# 技术方案优先工作流 / Planning-first workflow

`plan_design_options`、`select_design_option`、`prepare_design_specification`、
`prepare_netlist_draft`、`resolve_component_requirements`、
`approve_component_resolution`、`compile_executable_netlist` 和
`approve_executable_netlist`
组成从需求走向电路图之前的只读规划链。它先把需求
归类为一个工程域，生成 2--4 个取舍不同的实现路径，并按显式目标给出默认推荐；
在用户确认方案和实现路径之前，它不会生成 SPICE 网表、`.ms14` 原理图、仿真任务或
任何文件。

## 为什么先规划

直接让模型生成网表会把“架构选择”和“元件连接”混在一次昂贵调用中，也容易让用户
错过成本、功耗、鲁棒性和实现复杂度之间的取舍。规划结果是小而稳定的 JSON，可以先
在 Harness 或工作台中展示方案卡片；确认后再把同一个 `plan_id` 交给下一阶段的
网表/原理图生成器。

## MCP 调用

工具名：`plan_design_options`

```json
{
  "requirements": "机器人底盘电机闭环控制，要求低延迟、抗负载变化，并可在 MCU 上实时运行",
  "constraints": {
    "supply": "24 V",
    "controller": "STM32",
    "max_latency_ms": 2
  },
  "objectives": {
    "robustness": 0.35,
    "performance": 0.30,
    "implementation_speed": 0.20,
    "cost": 0.15
  },
  "context": {
    "competition": "robotics",
    "team_size": 2
  },
  "max_options": 3
}
```

`constraints` 和 `context` 只会被规范化并保留在计划中；当前规划器不会执行硬约束筛选，
也不会假装已经检查了额定值、器件库存或实机约束。下一阶段生成器必须再次校验这些条件。
`objectives` 支持 `performance`、`robustness`、
`cost`、`power`、`complexity`、`implementation_speed`、`latency` 和 `safety`，
权重会归一化。

返回结果包含：

- `plan_id`：稳定的 `plan-<sha256前32位>`，相同输入和规划器版本会得到相同 ID；
- `options`：每个方案的架构步骤、实现路径、优势、取舍、风险和定性指标；
- `recommended_option_id`：在保留声明约束、但尚未执行器件/电气校验的前提下，按加权
  启发式分数选出的默认方案；
- `state`：初始为 `proposed`；选择后应变为 `selected`；
- `request_digest` 与 `plan_digest`：用于把后续生成请求绑定到原始规划；
- `next_step`：固定为 `select_option_before_schematic`；
- `artifacts_generated`：固定为空数组；
- `execution_boundary`：`schematic_generated`、`simulation_started` 和
  `files_written` 均固定为 `false`。

规划分数是可解释的启发式排序，不是仿真、HIL 或实机结果。所有方案的
`evidence_status` 在此阶段都是 `planning-only`；只有后续真实实验产生证据后才能
报告 PASS/FAIL 或测量指标。

## 方案确认与交接

确认某个候选后，Agent 可以调用 `select_design_option`：

```json
{
  "plan": "上一步返回的完整规划 envelope",
  "option_id": "control-robust-pid"
}
```

该工具会重新校验 `plan_digest`，拒绝被篡改或重复选择不同候选的计划，并返回
`state=selected`、`source_plan_digest`、`selected_plan_digest` 和
`selection_digest`。返回的 `next_step` 为 `prepare_netlist_after_confirmation`，但
仍保证所有执行边界为 `false`。工作台的“确认并锁定方案”按钮使用同一接口；当前只
锁定上下文，不会自动启动成图。

## 电气规格准备

锁定方案后调用 `prepare_design_specification`，把架构转成成图前可审阅的规格：

```json
{
  "plan": "select_design_option 返回的完整 envelope",
  "parameter_values": {
    "dc_bus_voltage_v": 24,
    "motor_rated_voltage_v": 24,
    "continuous_current_a": 12,
    "stall_current_a": 35,
    "pwm_frequency_hz": 20000,
    "control_loop_frequency_hz": 1000,
    "feedback_sensor": "encoder",
    "max_latency_ms": 2,
    "ambient_temperature_c": 60
  }
}
```

工具会再次验证选择摘要，并返回模块、带类型/单位/上下限的参数要求、缺失参数、
分析计划、验证门槛，以及稳定的 `specification_id` / `specification_digest`。
它只从明确需求和同名硬约束中识别少量高置信参数，不猜测关键额定值。
参数完整只表示具备准备网表草案的输入条件，不表示拓扑、器件或仿真已经通过。
`circuit_design_created`、`netlist_generated`、`schematic_generated`、
`simulation_started` 和 `files_written` 仍固定为 `false`。

## 逻辑网表草案

参数完整后，必须由使用者明确确认当前 `specification_id` 和
`specification_digest`，Agent 才能调用 `prepare_netlist_draft`：

```json
{
  "plan": "select_design_option 返回的完整 envelope",
  "specification": "prepare_design_specification 返回的完整规格",
  "approval": {
    "approved": true,
    "specification_id": "spec-...",
    "specification_digest": "...",
    "review_note": "已在本机工作台确认"
  }
}
```

结果包含稳定的 `draft_id` / `draft_digest`、模块级网络、连接关系、待解析器件族、
派生约束和审查门槛。15 个内置技术方案都具备有界的逻辑拓扑模板。这个结果明确标记为
`logical-block-netlist`：预览文本不是 SPICE，器件型号、额定值和模型来源仍未解析，
`ready_for_schematic=false`、`ready_for_simulation=false`。该工具不创建
`CircuitDesign`，也不写文件或启动 EDA 后端。

## 推荐交互顺序

1. Agent 调用 `runtime_status`，确认服务端和后端状态。
2. Agent 调用 `plan_design_options`，展示 2--4 个方案和默认推荐。
3. 用户确认 `plan_id`、`option_id`、实现路径及缺失假设；Agent 调用
   `select_design_option` 锁定交接摘要；必要时修改目标权重后重新规划。
4. Agent 调用 `prepare_design_specification`，补齐并校验电气参数，展示模块、分析计划和
   验证门槛；只有 `ready_for_netlist_draft=true` 才能继续。
5. 用户确认规格摘要后调用 `prepare_netlist_draft`，审阅逻辑模块、网络、待选器件和
   派生约束。
6. 调用 `resolve_component_requirements`，查看每个逻辑角色的候选器件族、原生载体、
   便携适配器、额定值计算依据和模型来源状态。该步骤仍只读，不会静默选定具体型号。
7. 用户显式提交候选族、型号、额定值和模型来源后，调用
   `approve_component_resolution` 绑定器件解析、额定值和来源审阅；该结果只是进入网表
   编译的前置凭证，仍不产生 SPICE。
8. 对支持矩阵中的方案调用 `compile_executable_netlist`。它会完整重建审批凭证、重新哈希
   受限模型目录中的外部模型，并生成内存中的引脚级 `CircuitDesign` 与安全 SPICE 预览；
   用户再次确认后才可生成原理图。
9. 用户确认器件、引脚拓扑、计算值和 SPICE 后调用 `approve_executable_netlist`。该凭证只
   开放后续成图准备，不批准写文件、激励源、分析命令或仿真。
10. 用户确认网表和分析类型后，才调用后续原理图/实验接口。成图时将完整的编译预览和
    `approve_executable_netlist` 返回的审批凭证一起传给 `create_schematic_from_netlist`；
    工具会重新验证凭证，并且只使用凭证绑定的 SPICE 文本。

当前版本已经完成第 1--8 步，并实现第 8 步的首个独立编译模板
`signal-passive / passive-affine-low-pass-v1`。其余 14 个逻辑方案尚无引脚级编译器，会明确
拒绝而不是把模块图冒充成电路。第 9 步的批准会绑定编译摘要、CircuitDesign 摘要和 SPICE
SHA-256；批准后状态为 `approved-for-schematic-and-simulation-planning`，仍不会成图、写
文件或仿真。

## 器件候选与额定值解析

`resolve_component_requirements` 接受已批准的逻辑草案：

```json
{
  "draft": "prepare_netlist_draft 返回的完整草案",
  "selections": {
    "cr-01": {
      "family": "rail-to-rail-op-amp",
      "part_number": "用户待确认的具体型号",
      "voltage_rating_v": 12,
      "model_source": {
        "name": "本地模型文件",
        "sha256": "<64 位十六进制摘要>",
        "license": "待核对"
      }
    }
  }
}
```

不提供 `selections` 时，工具只返回稳定的推荐候选。当前内置目录覆盖 15 个规划选项
中的全部逻辑器件族；电阻、电容、电感等会标为 `native-primitive`，已存在便携宏适配器
的族会标为 `portable-adapter`，MCU、运放专用型号、电机对象和功率 IC 等则标为
`verified-model-required`。系统会根据规格中的供电、电流、频率和环境温度计算带安全裕量
的最低额定值，但状态固定为 `calculated-not-verified`，不会伪造厂商数据。

完成选择后，Agent 将返回的 `selection_snapshot` 原样交给审批工具，并把用户的确认绑定到
同一个摘要：

```json
{
  "draft": "原逻辑网表草案",
  "resolution": "resolve_component_requirements 返回的完整结果",
  "approval": {
    "approved": true,
    "resolution_id": "resolution-…",
    "resolution_digest": "<64 位摘要>",
    "confirm_topology": true,
    "confirm_ratings": true,
    "confirm_model_provenance": true,
    "review_note": "用户已审阅候选、额定值和模型来源"
  }
}
```

审批工具会重新解析摘要并拒绝未选择、额定值不足、模型来源缺失或许可证仍为待核对的条目；
通过后只返回 `component-resolution-approval` 凭证，供后续编译器使用。

外部模型显示为 `provided-not-verified` 时，表示人工已经提交并审阅了名称、URI、SHA-256
和许可证，足以通过“来源声明”门；它不等于模型字节已被读取或行为已验证。后续编译器仍
必须读取实际文件、重新计算 SHA-256，并在加载失败或摘要不匹配时拒绝生成可执行网表。

只有具体候选、额定值、模型摘要和许可证都经过人工确认，`approve_component_resolution`
才会返回 `ready_for_executable_netlist=true` 的审批凭证；该凭证仍保持
`ready_for_schematic=false`、`ready_for_simulation=false`，后续编译器必须重新校验摘要并
重新计算外部模型文件 SHA-256，才能进入 `CircuitDesign` / SPICE 阶段。

## 引脚级可执行网表预览

工具名：`compile_executable_netlist`

```json
{
  "draft": "prepare_netlist_draft 返回的完整草案",
  "component_approval": "approve_component_resolution 返回的完整凭证"
}
```

当前支持矩阵只有 `signal-passive`，并要求三项获批器件族精确为
`series-resistor`、`capacitor`、`resistor-divider`。编译器根据输入/输出范围、源/负载阻抗、
供电和截止频率求解一个带偏置的一阶无源低通网络；若要求电压增益、超出电源轨，或源/负载
条件无法得到全正阻值，会建议选择有源方案并拒绝编译。

返回值包含 6 个明确引脚器件、`CircuitDesign`、安全 SPICE、计算值、假设、SHA-256 和
结构化往返解析门禁。它不添加输入激励或分析命令，因此
`ready_for_netlist_approval=true`，但 `ready_for_schematic=false`、
`ready_for_simulation=false`。下一步 `approve_executable_netlist` 要求所有审阅确认字段为
`true`，并只返回绑定该预览的审批凭证。

## 引脚级网表人工确认

工具名：`approve_executable_netlist`

```json
{
  "executable_netlist": "compile_executable_netlist 返回的完整预览",
  "approval": {
    "approved": true,
    "compiled_id": "preview-…",
    "compiled_digest": "<64 位摘要>",
    "confirm_components": true,
    "confirm_topology": true,
    "confirm_calculated_values": true,
    "confirm_spice": true,
    "review_note": "用户已审阅引脚、计算值和 SPICE"
  }
}
```

工具会重新验证预览的 `CircuitDesign`、SPICE 往返、支持矩阵、编译摘要和 SHA-256，并把
审批摘要绑定到同一 `compiled_digest`。返回的
`multisim-mcp-executable-netlist-approval` 将 `ready_for_schematic` 置为 `true`，但
`ready_for_simulation`、`schematic_generated`、`simulation_started` 和 `files_written` 仍为
`false`；后续阶段必须再次验证该凭证，不能把它当作执行授权。

## 已批准网表成图交接

将 `compile_executable_netlist` 的完整返回值作为 `executable_netlist`，将
`approve_executable_netlist` 的完整返回值作为 `netlist_approval`，并把预览中的
`spice_netlist` 原样传给 `create_schematic_from_netlist` 的 `netlist` 参数。成图工具会在
调用 Multisim 前重新校验两份凭证、编译摘要和 SPICE 摘要；缺少任一凭证，或 `netlist` 与
已批准文本不一致，都会立即拒绝。成功响应会回显不含完整网表的审批 ID/摘要，明确
`simulation_started=false`。该交接只开放写入 `.ms14` 原理图，不会自动添加激励、分析命令
或启动仿真；实验仍需另一份明确的分析计划和执行审批。

## 仿真计划人工确认

工具名：`approve_simulation_plan`

该工具接收同一份完整的 `executable_netlist`、`netlist_approval`，以及包含原样网表、分析
命令、测量要求和理论值的 `ExperimentSpec`。审批字段必须明确确认网表、命令、测量和验收
限制。工具会重新验证网表审批、SPICE 绑定和 `op`/`dc`/`ac`/`tran` 安全命令，不写文件、不
生成原理图、不启动仿真。返回的 `multisim-mcp-simulation-plan-approval` 将
`ready_for_simulation=true`，但 `simulation_started=false`。

执行时把这三份输入传给 `run_verified_circuit_experiment` 的可选参数
`executable_netlist`、`netlist_approval` 和 `simulation_plan_approval`，同时传入同一份
`ExperimentSpec`。实验入口会在创建原理图和启动 Multisim 前再次重建并校验审批摘要；任一
凭证缺失、命令或测量被修改、或网表不一致都会失败关闭。旧的直接
`run_verified_circuit_experiment` 调用仍保留兼容性，但不会带有这份额外的仿真计划审批溯源。

MCP 进程只会从 `MULTISIM_MCP_MODEL_ROOT` 指定的目录读取获批外部模型；本机工作台固定使用
工程根目录下的 `models/`。URI 必须是该目录内的相对路径，实际文件会限制为 4 MiB 并重新
计算 SHA-256。当前无源模板不需要外部模型。

## English summary

`plan_design_options`, `select_design_option`, `prepare_design_specification`,
`prepare_netlist_draft`, `resolve_component_requirements`, and
`approve_component_resolution` are read-only pre-generation MCP tools. The
first returns a bounded set of trade-off-aware implementation options and a deterministic default
recommendation; the second binds the user's selected option; the third exposes missing electrical
parameters, modules, analyses, and validation gates; the fourth requires explicit approval and
produces a non-executable logical block netlist with unresolved component requirements. These
pre-generation tools return an explicit execution boundary and never emit a CircuitDesign, SPICE
netlist, schematic, file, or simulation result. The component resolver adds bounded component-family candidates,
rating calculations, and model-provenance gates; it still does not select a silent part number
or produce an executable netlist. The approval tool binds the reviewed component/rating/model
provenance snapshot and only authorizes a later compiler; it does not itself generate SPICE.
`compile_executable_netlist` is the first bounded pin-level compiler. It currently supports only
`signal-passive`, revalidates the complete approval artifact, re-hashes approved local model bytes,
and returns an in-memory CircuitDesign/SPICE preview. It does not write files, render a schematic,
add simulation stimuli, or run an EDA backend, and it fails closed for every unsupported option.
`approve_executable_netlist` is a separate human gate: it binds the preview digest, design digest,
and SPICE SHA-256, authorizes only later schematic planning, and leaves file writes and simulation
disabled. To generate the schematic, pass the complete compiler response as
`executable_netlist`, the complete approval response as `netlist_approval`, and the preview's
exact `spice_netlist` as `netlist` to `create_schematic_from_netlist`. The tool revalidates both
artifacts before invoking Multisim and rejects a missing credential or changed SPICE text. A
successful response records the approval identifiers and `simulation_started=false`; it only
opens the `.ms14` write and never starts a simulation.

`approve_simulation_plan` then binds the same approved preview to a validated `ExperimentSpec`.
It requires explicit confirmation of the netlist, safe analysis commands, measurements, and
acceptance limits, but still performs no file write or simulation. Pass the preview, netlist
approval, and simulation-plan approval to `run_verified_circuit_experiment` together with the same
specification. The execution entry point revalidates all three before creating the schematic or
starting Multisim; missing credentials or any changed command, measurement, or SPICE text fail
closed.
