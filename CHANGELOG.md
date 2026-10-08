# Changelog / 更新日志

本项目遵循语义化版本的预发布形式。中文为主要说明，英文摘要紧随其后。

## [Unreleased]

### 中文

- 新增双板复杂数字回归案例 `split_logic_load`：以已验收的 `HDR1X4` 真实连接器跨接
  `clk`、`data`、共享电源和地，在远端板保留 1 kΩ 输出负载，并分别覆盖 DC、TRAN、AC
  原生验收。新增结构化案例模块和 `tools/run_multiboard_digital_regression.py` 运行器；
  TRAN/AC 仅在调用方显式选择 `series_alignment=linear` 时对独立板的自适应采样轴做有
  边界检查的线性重采样，默认仍严格拒绝轴不一致。Multisim 14.3 实机三种分析均为
  `accepted/native-verified`；证据留在仓库外，详见 `docs/MULTIBOARD_DIGITAL_REGRESSION.md`。
- 扩展双板数字矩阵 `split_mixed_logic`，覆盖 OR/XOR/NOR/XNOR 四类数字模型；同时让
  原生探针选点主动避开其他网络的线段交点，避免 Multisim 保存重开时丢失交叉点上的源网
  探针。第二个案例在 14.3 上 DC/TRAN/AC 三种分析均为 `accepted/native-verified`。
- 新增 Multisim 14.3 原生 `HDR1X4` 四针连接器族：从官方 Getting Started 样例 J1
  提取本地模板，支持 `XJ1 ... HDR1X4` 网表语法、连续 P1–P4 引脚和精确 pin signature
  映射；完成打开/保存/重开、全引脚 ReportNetlist 及 DC/AC/TRAN 闭环验收。模板仍由
  用户本机授权样例生成，仓库不分发 NI 元件资产；其他连接器和 Multisim 版本继续单独
  验收。
- 将已验收的 `HDR1X4` 接入多板设计物化：按显式板端实例生成真实四 pin 连接器组件和
  `XJ<n> ... HDR1X4` 预览；版本映射待定或旧式推断连接器会保持关闭原生执行门。新增
  Multisim 14.3 双板四引脚实机闭环，覆盖生成、保存重开、ReportNetlist、DC/TRAN/AC
  原生分析、接口序列比较和完整电路参考比较，三种分析均为 `accepted/native-verified`；
  重开后的 ReportNetlist 还会逐针校验 `P1`–`P4` 到源网的映射，错序直接阻断验收。
- 版本清单缺失时改为显式 `unavailable`/`mapping-pending` 预览结果并关闭原生执行，避免
  在未验收的 Multisim 版本中合成或冒充连接器映射。
- 新增显式多板接口测试夹具合同：支持电压源、终端电阻、地参考和观测点，校验
  位号/网络/多驱动冲突，并拒绝没有物理导线锚点的观测点；缺失跨板覆盖时不自动猜测
  边界条件。新增跨板接口读数比较门禁，缺失端点保持 `unverified`，不会当作 0。
- 改进原生探针布局：对 R/V 和数字网络优先放置在足够长的导线中段，避免探针图标压住
  元件本体；L/C 网络仍使用端点策略以保持 Multisim 14.x 重开稳定性。已在 Multisim
  14.3 电源板＋分压板样例完成逐板生成、保存重开、ReportNetlist、拓扑/布局、原生
  COM OP、探针输出和 `V(bus)=5 V` 跨板一致性验收，并与保存重开的完整电路参考一致。
- 将 Multisim 后端的 API 来源和能力边界写入版本化能力元数据，明确记录 NI
  Automation API/COM、`MultisimInterface.MultisimApp`、独立 32 位 worker、版本探测、
  模板/XML 原理图生成，以及官方接口当前不提供元件放置和导线绘制方法。
- 补充有效控制支路下 H 源（CCVS）仍无法通过原生 COM 输出的失败证据，继续失败关闭，
  不计入 13/13 混合信号矩阵。
- 新增 `@ADC4`/`@DAC4` 四位便携行为桥，加入独立 RC 负载、引脚和原生重开探针；
  Multisim 14.3 实测 ADC4 四路 0–5 V，DAC4 输出覆盖 16 级组合电平；两个案例已
  纳入正式 12/12 混合信号矩阵。
- 新增 ADC4→DAC4 三角输入扫描回归：Multisim 14.3 原生瞬态覆盖全部 16 个编码，
  ADC 位电平、编码和 DAC 重构均通过；正式混合信号矩阵扩展为 13/13。
- 加强多板拓扑规划：支持板级元件数和连接器引脚容量约束，返回每板接口清单、
  约束违反项和 `feasible` 状态；不可行分区不会被当作通过方案。
- 为可行多板候选生成确定性的逐板逻辑工件：包含板级组件、板内/跨板网络、连接器
  映射和接口清单；工件明确保持 `logical-only`/`unverified`，等待后续逐板原生
  `.ms14` 生成、重开、拓扑和仿真验收。
- 新增显式多板物理连接器合同：连接器型号、各板实例、连续 pin map、信号类型、端点方向
  和可选电压/电流/阻抗约束均进入结构门禁；跨板网络缺 pin、重复 pin、板归属错误、地线
  类型错误和端点方向冲突会使候选标记为 `infeasible`。旧的自动连接器继续用于逻辑预览，
  但明确标记为 `inferred`/`unverified`，不冒充原生物理连接证据；显式合同在原生符号/封装
  映射完成前保持 `native_connector_status=mapping-pending`。
- 兼容层新增 `resolve_connector_mapping`，按目标 Multisim 版本、连接器标识和完整 pin
  signature 精确匹配 manifest；缺少独立条目时返回 `unavailable`，不会静默退回通用载体。
- 将可行分区接入 EDA 核心：可从 `CircuitDesign` 生成逐板结构化设计和带跨板接口
  注释的 SPICE 预览，保留源设计的内联模型定义；这些预览仍保持 `logical-only`，
  不替代 Multisim 原生重开和仿真验收。
- 增加逐板接口结构门禁：检查跨板连接器对称性、网络存在性、对端板引用和空板；
  `interface_validation` 失败时阻止后续逐板原生生成，且不改变 `native_status=unverified`。
- 新增只读 MCP 工具 `plan_multiboard_engineering_request`，让 Agent 可以直接提交版本化
  多板请求并读取候选分区与逐板逻辑工件；该工具不启动 COM、不写入工程文件。
- 区分多板候选的约束可行性与结构可交付性：计划返回 `structural_status`、
  `structurally_ready` 和推荐候选索引，避免把带空板的低连接器成本方案误选为多板设计。
- 新增只读选择工具 `select_multiboard_engineering_candidate`，以计划摘要和选择摘要绑定
  一个结构完整的候选，作为后续逐板原生生成的输入凭证。
- 新增版本门控的 `run_native_multiboard_acceptance` 首版原生验收运行器：逐板生成、打开、
  保存、重开、ReportNetlist 回读、拓扑/布局门禁、原生 DC OP、跨板观测比较和完整电路
  参考比较均写入机器可读证据；Multisim 版本比较采用规范化元组，兼容 COM 返回的
  `Multisim 14.3` 前缀形式。14.3 电源板＋分压板实测结果为 `accepted/native-verified`，
  全部验收布尔项通过。
- 将 `run_native_multiboard_acceptance` 暴露为 MCP 工具：从同一份结构化工程请求重新规划、
  锁定结构完整候选，再进入预览或显式原生执行；同步 DeepSeek Harness 兼容清单，完整工具
  数为 115，experiment profile 为 92。
- 原生验收新增保存后重开 `ReportNetlist` 的独立拓扑门禁 `all_reopened_topology_pass`，
  避免只凭生成阶段检查就把重开后被 Multisim 丢失的器件或网络判为通过。
- 原生验收证据包新增每板 `native-op.json`、`native-op.csv`、根目录
  `acceptance.json` 和 `acceptance-report.md`，便于人工审阅、数据分析和后续实验报告组装。

## [1.3.0rc3] - 2026-09-25

### 中文

- 布线器空间分桶：A* 代价函数从遍历全部已布线段改为排序数组 + bisect 取
  相邻线段，语义严格等价（117 条布线路径逐点一致）。大图纸实测构建
  694.7s → 183.1s（流水灯）、127s → 41.9s（呼吸灯）。
- `create_schematic_from_netlist` 新增 `verify=False` 快速路径：纯 XML 构建、
  不启动 Multisim，迭代布局时秒级到分钟级出图；定稿时用默认 `verify=True`。
- 新增 `check_component_overlap` 工具：解码 + 引脚包络几何检查（秒级、无 COM），
  报告重叠元件对与导线穿越统计。
- `set_component_positions` 新增 `mode="relative"`（批量微移）、
  `anchor="pin_center"`（与构建期 component_positions 同锚点）、`snap`（网格
  吸附），并在结果中报告 `overlaps_after`。文档明确 Multisim 连线模型：
  端点是固定引用，重布线只改路径。

### English

- Spatially bucket the A* cost scan (sorted arrays + bisect): semantically
  identical routing (117 paths byte-equal) at 3-4x the speed on large layouts.
- `verify=False` fast path on `create_schematic_from_netlist` builds pure XML
  without launching Multisim; verification stays opt-in.
- New `check_component_overlap` tool (decode + hull geometry, seconds, no COM).
- `set_component_positions` gains relative mode, pin-center anchor, grid snap
  and an `overlaps_after` report; docs spell out Multisim's fixed-endpoint
  wiring model.

## [1.3.0rc2] - 2026-09-25

### 中文

- 把 v6/v7 电路重建期间积累在安装环境里的修复合并回源码：SWB 按 model 名识别、
  模板 `<Item>` 包装校验、pywin32 gen_py 缓存自愈 `repair_com_cache()`、
  CiModel 绑定与导出顺序、CD4017/LM324AJ 原生载体、多节元件 U1→U1A 拓扑别名。
- 修复 SWB 模板把 `.model` 卡粘在器件卡同一行的问题：Multisim GUI 的
  「检查 SPICE 网表」会把 `SW(` 当函数解析而丢弃开关元件。模板包已修，
  并新增 `_normalize_inline_model_cards()` 在模板加载时自动把粘行的
  `.model`/`.subckt` 卡放到独立行。
- 新增元件位置能力：`list_component_positions` / `set_component_positions`
  两个工具（解码 .ms14 → 移动符号与导线端点 → 障碍感知重布线 → 编码回写，
  电气拓扑不变），以及 `create_schematic_from_netlist` 新参数
  `component_positions`（布线前固定元件坐标）。
- doctor 新增模板包生成器版本检查：旧版本生成的包报 `stale`/fail，不再静默降级。
- 纳入开发版整流器工作流（`plan/run/optimize_natural_rectifier`、桥式整流布局、
  SIN 源原生 AC_VOLTAGE 载体）并全部通过测试。
- `run_spice_netlist` 结果新增 `convergence_hint`：检测到瞬态收敛失败时直接给出
  已验证的 `.options rshunt=3e11 method=gear` 修法（LM158/LM324 宏电路）。

### English

- Merge all schematic/COM fixes from the local install environment back into
  the source tree (SWB model-name detection, template validation, gen_py cache
  self-repair, CiModel binding/order, CD4017/LM324AJ carriers, U1A alias).
- Fix the SWB template gluing its `.model` card onto the device line, which
  made Multisim's GUI "Check SPICE Netlist" drop the switch components
  (`Expected ')' in function sw`). The pack is fixed and a new
  `_normalize_inline_model_cards()` pass enforces line separation at load time.
- New component-placement capability: `list_component_positions` /
  `set_component_positions` tools (decode → move symbols and wire endpoints →
  obstacle-aware reroute → encode; electrical topology unchanged) plus a
  `component_positions` argument on `create_schematic_from_netlist`.
- doctor now fails on template packs generated by older releases (`stale`).
- Ship the development rectifier workflow (natural-language plan/run/optimize,
  bridge layout, native sine source carrier) with the full suite green.
- `run_spice_netlist` gains a `convergence_hint` field with the verified
  rshunt+gear fix for op-amp macro transients.

## [Unreleased]

- 新增原生温度能力探测：`tools/probe_native_temperature.py` 对工程副本执行
  `.TEMP` 命令、检查 XSPICE 日志、保存并重新打开副本后回读 OP；完成状态不再被误判为
  温度验证。新增 `native_temperature.py` 的 `unsupported`/`unverified` 结构化结果，
  并在原生能力探测中公开 `temperature_capability`。
- 在当前主线重新生成本机授权模板包并完整执行五类数字回归；组合逻辑、DFF、四位计数器、
  四位移位寄存器和计数器解码负载全部通过原生重开、拓扑、引脚、布局和瞬态输出检查，
  带负载计数器路径不再复现历史 PR17 的负载丢失现象。
- 扩展混合信号原生回归矩阵：新增四位计数器最低位和四位串入并出移位寄存器最低位
  驱动 RC 负载案例；Multisim 14.3 矩阵现为 4/4 通过，四类工程均完成原生重开、
  拓扑/引脚回读、COM 瞬态和数字/模拟输出验收，证据保存在本机
  `hybrid-native-matrix-20261003-v4`。
- 加入 `diode_rc_shaper` 案例，使用本地授权 `1N4001GP` 模型验证真实二极管元件与
  RC 整形边界；该案例随后取得完整的 Multisim 原生证据。
- 修复混合回归门禁：完整引脚证据现在是通过条件；新增原生二极管 `D` 的 A/K 端子契约，
  并完成 5/5 混合信号矩阵实机复验，证据保存在本机 `hybrid-native-matrix-20261003-v5`。
- 混合回归支持多个数字/模拟输出对，新增计数器 `q0/q1` 双路独立 RC 负载案例；
  Multisim 14.3 矩阵扩展为 6/6 通过，证据保存在本机
  `hybrid-native-matrix-20261003-v6`。
- 加入 `vcvs_rc_bridge` 案例，将实验性 VCVS 原生载体纳入混合信号重开、引脚和瞬态门禁；
  新增 E/G 载体 D/G/S/SUB 端子契约，Multisim 14.3 矩阵扩展为 7/7 通过，证据保存在
  本机 `hybrid-native-matrix-20261003-v7`。
- 将活动受控源案例调整为 `vccs_rc_bridge`，验证 G 源受控电流与 RC 瞬态；Multisim 14.3
  矩阵扩展为 8/8 通过。H 源实验出现拓扑通过但输出全零，暂不计入矩阵并保留本机
  失败证据。
- 加入待验收的 `dac_rc_bridge`，验证 `@DAC1` 行为桥接在原生工程重开后的数字到模拟
  响应；未取得 Multisim 原生证据前不计入通过矩阵。
- 修复 `@ADC1`/`@DAC1` 行为表达式的原生节点物化：为表达式读取的输入和电源轨加入
  1 GΩ 高值锚点，避免 Multisim 重开后丢失只有单个实体引脚的依赖网。Multisim 14.3
  原生混合矩阵现为 9/9；DAC 案例完成拓扑、引脚、重开和 COM 瞬态输出验收。
- 新增 `adc_rc_bridge` 原生回归案例；ADC 与 DAC 均完成 Multisim 14.3 的重开、拓扑、
  引脚和 COM 瞬态输出验收，混合信号矩阵扩展为 10/10。
- 新增版本范围兼容性矩阵工具 `tools/run_compatibility_matrix.py`：按已安装 Multisim
  版本保存 Automation API 探测、能力档案和 SHA-256 清单；相邻或未知版本保持
  `unverified`/`unsupported`，不会从 14.3 结果推断兼容。
- 布局验证新增 `crossings_per_wire` 与可配置上限，超过上限会产生
  `excessive-wire-crossings` 错误；数字逻辑新增按低扇出信号链排序的布局 profile，
  负载元件会跟随其驱动器放置。`create_schematic_from_netlist` 新增可选的
  `require_layout_pass=true` 交付门禁：失败时保留 XML 与布局报告供修复，不编码
  `.ms14`，并已覆盖到受控 handoff 及完整 Multisim 实验流水线。
- 新增 `optimize_natural_rectifier`：固定负载的九候选原生参数搜索、八个容差组合、启动验收、选定保存文件无改参复验及对比报告。真实 MCP 测试完成27次运行，12V/100mA/≤0.3V案例选定3900μF，较4700μF基线减少17.02%。
- 处理 Multisim SetRLCValue 的旧数值缓存：验证有效参数字符串与原生RLCValue，记录缓存差异，保留严格证据门槛。开发版 full/experiment/optimization profile 为107/84/70。

- 完成低压 1N4001GP 桥式整流的原生 MCP 工作流。修复早期规划器固定负载电阻、未接入纹波验收、把正弦 OP 当作整流稳态电压及无来源 1N4007 型号的问题。
- 同步真实正弦载体的参数表及端口极性，保存后核对模型、全部引脚、R/C/源参数；加入桥式布局和隐藏测量面板。
- 增加按时间积分的瞬态均值、RMS 和纹波，验证输入波形、负载电流及稳态窗口；导出原生图纸、CSV、SVG 与可读 HTML 报告。
- 整流生成部分新增两个工具；已发布的 1.3.0rc1 保持原内容。

## [1.3.0rc1] - 2026-09-13

### 中文

- 发布自然语言及组合模拟电路的原生生成、仿真、测量验收和导出预览入口。
- 将版本化组件映射纳入安装包，修复 COM-free 测试的外部环境依赖及 CI 工具数量检查。
- 保留预览边界：新工程需图面复核，实际验证集中在 Multisim 14.3。

- 修复 COM worker 在中文（CJK 区域设置）Windows 上的路径损坏问题：客户端按 UTF-8
  写 JSON 协议，但 worker 进程此前按系统 ANSI 代码页解码自己的 stdin，导致任何
  非 ASCII 路径（如中文目录名）在 worker 侧变成乱码，编解码调用以 ENOENT 失败且
  错误信息看似路径正常。现在启动 worker 时强制 `PYTHONUTF8=1`，`system/ping`
  诊断新增 `stdio_encoding` 字段，并附回归测试。

- 1.3 开发：增强标准纠错基准套件的可审计性。每个用例和整个套件现在记录 UTC
  时间戳、耗时、通过率和统一验收判据；即使所有真实实验失败，也会保留
  `validation.json` 与可校验的目录清单，避免摘要与清单之间出现自引用哈希失效。
- 1.3 开发：增加稳定 Agent API 契约。`runtime_status` 现在提供可缓存的版本化
  `api_contract`，描述 Tool Profile、功能、错误码和 durable job 状态；CLI JSON
  错误保留 `type/message` 兼容字段，并增加统一 `code/retryable`。
- 1.3 开发：Workbench loopback API 增加有界只读 SSE 作业事件流
  `/api/jobs/{job_id}/events`，支持一次性快照、心跳、终态自动关闭和硬超时；不改变
  MCP 工具、Resource 或持久化作业 schema。
- 1.3 开发：增加只读 `review_design_requirements` 需求契约审查，在基线实验前区分硬约束、
  软目标、偏好和假设，并拦截同一信号上的明显冲突。

### English

- Fix COM worker path corruption on CJK-locale Windows: the client speaks
  UTF-8 over the JSON protocol, but the worker previously decoded its own
  stdin with the system ANSI code page, mangling every non-ASCII path before
  codec execution (failing with ENOENT while the error text looked normal).
  The worker is now spawned with `PYTHONUTF8=1`; `system/ping` diagnostics
  gained a `stdio_encoding` field, covered by a regression test.

- 1.3 development: hardened the standard correction benchmark suite. Per-case and
  suite-level UTC timestamps, durations, pass rate, and acceptance criteria are
  recorded. Even an all-failed real run retains `validation.json` and a verifiable
  directory manifest, avoiding self-referential summary hashes.
- 1.3 development: added a stable Agent API contract. `runtime_status` exposes a
  cacheable versioned `api_contract` for Tool Profiles, features, error codes, and
  durable-job states. CLI JSON errors retain `type/message` for compatibility and
  add normalized `code/retryable` fields.
- 1.3 development: added a bounded read-only SSE job event stream at
  `/api/jobs/{job_id}/events` to the loopback Workbench API, with one-shot snapshots,
  heartbeats, terminal-state close, and hard time limits; MCP tools, Resources, and
  persisted job schemas remain unchanged.
- 1.3 development: added the read-only `review_design_requirements` contract review,
  separating hard constraints, soft objectives, preferences, and assumptions and
  rejecting obvious contradictory bounds before a baseline experiment.

## [1.2.0] - 2026-09-01

### 中文

- 新增从需求、候选方案、设计规格、网表草稿、元件解析到可执行网表的分阶段设计链路；
  方案和执行均通过显式审批门，默认不会自动采用或写入候选结果。
- 新增电路诊断、补丁预演与事务、设计优化、全局优化、自主纠错和多方案比较；支持
  硬约束、确定性搜索、Pareto 前沿、恢复点及真实实验门禁。
- 扩展传输无关 EDA Core：统一 Multisim、ngspice、行为参考与差分验证后端，增加
  数字观测、SPICE 兼容性/来源证明和开放 EDA 选择策略。
- 扩展持久实验/优化作业、目录清单、完整性校验与中断恢复，并将 MCP 公共面更新为
  78 个工具、20 个资源模板和 5 个双语提示词。
- 增强 DeepSeek、OpenAI、Ollama 与 OpenAI-compatible 模型配置和受限工具循环；
  独立 DeepSeek Harness npm 插件继续保持 `1.1.0`，与本版本兼容。
- 本次 GitHub 候选版仅整理 Python MCP 核心、CLI、文档、测试及可选本地桥接 API，
  **不包含 React Workbench 前端**，避免把尚未收尾的独立软件界面混入核心发布。

### English

- Added an approval-gated design pipeline from requirements and alternatives to
  specifications, netlist drafts, component resolution, and executable netlists.
- Added diagnosis, patch preview/transactions, constrained optimization, global
  optimization, autonomous correction, and deterministic design comparison.
- Expanded the transport-neutral EDA core across Multisim, ngspice, behavioral
  references, differential checks, digital observation, and SPICE provenance.
- Expanded durable jobs, integrity manifests, recovery, and the public MCP surface
  to 78 tools, 20 resource templates, and five bilingual prompts.
- Kept the independently published DeepSeek Harness npm integration at `1.1.0`.
- This GitHub candidate contains the Python MCP core, CLI, tests, documentation,
  and optional loopback bridge APIs; it deliberately excludes the React Workbench.

## [1.1.0] - 2026-08-21

### 中文

- 新增统一 `directory.manifest.json`：项目、完整实验和参数扫描/优化目录共享严格
  schema、生命周期状态、修订号、生成器版本及产物大小/SHA-256；默认完整性校验拒绝
  路径越界、符号链接、未知字段和篡改，实验与扫描均在原子发布事务内生成。
- 新增传输无关模型运行时：支持 DeepSeek/OpenAI/Ollama 的有界非流式 Chat
  Completions、严格消息/工具/用量对象、每请求密钥轮换、及时取消和双重授权失败回退；
  `model` CLI 仅从显式 stdin/UTF-8 文件读取提示词且不公开工具。
- 新增白名单 `BoundedToolLoop`：每个工具必须同时提供定义、独立参数验证器和本地
  handler；全部参数先预检，再受轮次、调用数和结果大小限制执行，拒绝未知工具、重复
  调用 ID、未配对历史和未完成最终轮次。
- 新增独立 `model-diagnose` 入口及四个固定只读 EDA 工具：支持严格 `CircuitDesign`
  JSON 或仅解析不执行的安全 SPICE 网表，提供有界设计摘要、分页元件、网络连接与结构
  检查；普通 `model` 仍无工具，原始网表、annotations、路径和后端操作均不公开。
- 新增模型 Provider 自助配置：可从已知环境变量自动发现 DeepSeek、OpenAI、
  Ollama 和 OpenAI-compatible 服务，安全预览、原子合并写入、脱敏查看并显式探测
  模型列表；配置只保存环境变量引用，拒绝明文密钥和远程 HTTP。
- 新增第一版传输无关 EDA Core：严格版本化 `CircuitDesign`、`DesignPatch` 和
  `ArtifactSet`，提供可逆有界补丁、模型来源和 SHA-256 产物清单。
- 新增 `EdaBackend` 能力发现协议、后端注册/调度服务和可注入的 Multisim 适配器；
  核心不依赖 MCP/COM，并以无 COM 假后端在 Python 3.10 与 32 位 Python 3.12 验证。
- 新增失败关闭的 `CircuitDesign` 与受限 SPICE 转换边界；
  `create_schematic_from_netlist` 已作为首个工具通过应用服务执行，公开签名和返回结果
  保持兼容，并通过真实 Multisim 14.3 与双 LM324 宏模型回归。
- `run_spice_netlist` 已接入同一应用服务，保留可选输出目录、超时、返回点数、覆盖和
  危险命令双重授权契约；产物清单会去重临时/发布副本，并通过真实 10 V 分压器工作点
  回归得到 5 V 输出。
- 新增传输无关 `ExperimentRequest` 与 `ExperimentApplicationService`，同步、验证和
  持久 worker 实验共用可注入事务入口；真实瞬态门禁生成 453 点数据、15 个完整文件
  和 15 个安全 Resource 句柄，同时保持 MCP 结果与 job 存储格式兼容。
- 将约 400 行 staging、绘图、报告、完整性门禁、原子发布和回滚逻辑提取为
  `MultisimExperimentPipeline`；新增发布中途故障注入测试，证明旧产物恢复且无临时
  状态残留，并再次通过 453 点真实 Multisim 完整事务。
- 新增版本化 JSON-RPC COM worker，将全部 Multisim Automation 与 `.ms14` 编解码
  操作固定在独立 32 位进程；MCP 前端现可使用 64 位 Python，支持状态保持、心跳/
  取消转发、RPC 超时、崩溃重启和并发串行化。真实隔离门禁生成 437 点数据和完整
  15 文件事务，CLI 新增 `--worker-python` 配置。
- 根据五路波形课程设计真实回归，新增时域 `frequency` 与 `thd` 验收指标；支持测量
  窗口、边沿、阈值、迟滞、最少周期数、基波频率和谐波阶数，并将结果直接写入
  `verification.json` 与正式实验报告。
- 新增课程设计反馈记录，明确等效模型/厂商模型证据边界、联动频率冲突和自动原理图
  布局限制，避免把等效模型 PASS 误报为实物验收完成。
- 新增内联厂商 `.subckt` 宏模型递归展开：保留嵌套依赖、局部节点、模型引用和
  `PARAMS:` 覆盖，生成 Multisim 稳定参考编号，并以 `editable_model_coverage`
  区分完整、部分和仅载体证据；两级 LM324 真实事务回归通过。
- 新增 2.0 综合路线图，确定先平台化、再纠错优化、开放仿真后端、可视化工作台和
  KiCad 工程输出的开发顺序。
- 新增 DeepSeek 与官方 DeepSeek Harness 兼容说明，包括凭据边界、工具规模、
  Resources/Prompts 当前限制和版本验证矩阵。
- 配置生成器新增 `deepseek-harness` 客户端，输出官方 MCP Client 使用的 Cordis
  插件片段，并校验上游 `serverName` 约束。
- 新增四种服务端 Tool Profile；默认 `full` 保持完整工具兼容，其他档案可减少
  DeepSeek Harness 等客户端的工具 schema 上下文占用。
- 新增列出、分页读取、受控导出和汇总实验产物的四个 Tool 等价入口，供暂不消费
  MCP Resources 的客户端使用；导出限定在显式批准的根目录内。
- 新增五个版本化 DeepSeek Harness Skill，覆盖创建、纠错、比较、报告和指标验证；
  `harness-skills` 命令可安全安装到项目 `.dsh/skills`，默认拒绝覆盖。
- 新增机器可读 Harness 兼容清单、确定性本地门禁和每周非阻塞上游版本漂移监控。
- 新增可独立安装的 `multisim-mcp-dsh-plugin` bundle 源码，以及隔离、无 API Key、
  固定官方 dsh 版本的配置组合与真实启动烟雾测试。

### English

- Added one strict `directory.manifest.json` contract for project, experiment,
  and sweep/optimization folders, with lifecycle state, revisions, producer
  version, artifact sizes/SHA-256 hashes, fail-closed integrity checks, and
  generation inside the existing atomic experiment and sweep transactions.
- Added a transport-neutral bounded Chat Completions runtime with normalized
  messages/tools/usage, per-request credential rotation, prompt cancellation,
  double-opt-in failover, a tool-free file/stdin CLI, and an allowlisted tool
  loop that requires independent argument validation for every handler.
- Added a separate `model-diagnose` command with four fixed read-only EDA tools
  over strict CircuitDesign JSON or safely parsed, never-executed SPICE input;
  raw netlist text, annotations, paths, simulation, and mutation stay outside
  the model tool surface.
- Added secret-free model-provider discovery, preview, atomic merge, sanitized
  display, and explicit models-endpoint probes for DeepSeek, OpenAI, Ollama, and
  custom OpenAI-compatible services.
- Added the first transport-neutral EDA core with strict versioned
  `CircuitDesign`, reversible bounded `DesignPatch`, and hashed `ArtifactSet`
  objects.
- Added the `EdaBackend` capability protocol, backend dispatch service, and an
  injectable Multisim adapter with no-COM tests on Python 3.10 and win32 3.12.
- Added a fail-closed `CircuitDesign`/limited-SPICE conversion boundary and
  routed `create_schematic_from_netlist` through the application service while
  preserving its public contract, including a real dual-LM324 Multisim test.
- Routed `run_spice_netlist` through the same application service while
  preserving optional publication, timeout, point-limit, overwrite, and unsafe
  command gates; a real 10 V divider operating-point regression produced 5 V.
- Added transport-neutral `ExperimentRequest` and `ExperimentApplicationService`
  boundaries shared by synchronous, verified, and durable-worker experiments;
  a real transient gate produced 453 points and the complete 15-file transaction.
- Extracted staging, plotting, reporting, completeness checks, atomic publishing,
  and rollback into injectable `MultisimExperimentPipeline`, including a
  mid-publication fault test that proves complete restoration and cleanup.
- Moved every Multisim Automation and `.ms14` codec operation behind a versioned
  JSON-RPC subprocess that retains state in 32-bit Python while allowing a
  64-bit MCP frontend, with cancellation/heartbeat forwarding, RPC timeouts,
  crash restart, serialized concurrency, and a real 437-point artifact gate.
- Added time-domain `frequency` and `thd` verification metrics after a real
  five-output waveform-generator regression, including explicit measurement
  windows, edge/threshold/hysteresis controls, minimum cycles, fundamental
  frequency, and harmonic count.
- Documented the evidence boundary between equivalent and vendor models plus
  linked-range and automatic-schematic-layout limitations found by the course
  design workflow.
- Added recursive editable expansion for compatible inline vendor `.subckt`
  models, including nested dependencies, scoped nodes, instance parameters,
  stable Multisim references, and explicit editable-model coverage status.
- Added the post-1.0 platform, optimization, multi-EDA, and visual-workbench roadmap.
- Documented the DeepSeek and official DeepSeek Harness compatibility baseline.
- Added a `deepseek-harness` client target that renders a validated Cordis MCP
  plugin fragment without forwarding model credentials to the MCP process.
- Added four server-side tool profiles while preserving the complete `full`
  profile as the default.
- Added four Tool equivalents for listing, paginating, exporting, and summarizing
  experiment artifacts when a client does not consume MCP Resources.
- Added five versioned DeepSeek Harness skills plus a safe, no-clobber project
  installer for `.dsh/skills`.
- Added a machine-readable Harness compatibility manifest, deterministic local
  gate, and weekly non-blocking upstream drift monitor.
- Added an installable `multisim-mcp-dsh-plugin` source bundle and an isolated,
  credential-free smoke test against the pinned official dsh CLI.

## [1.0.0] - 2026-08-10

### 中文

- 迁移到 MCP Python SDK 2.x；同一 stdio 服务兼容 `2026-07-28` 和旧协议客户端。
- 将所有工具调用串行到专用 COM 线程，适配 SDK 2 的同步 handler 线程模型。
- 增加 11 个 `multisim://experiments/...` 实验资源模板、2 个扫描资源模板和重启后
  重新注册工具。
- 增加创建实验、调试、比较、报告和指标验证五个中英双语 Prompt。
- 为完整实验及资源注册结果增加明确的 output schema 和运行时结构校验。
- 增加 32 位 Windows 可安装的加密依赖边界及现代/旧协议、资源安全测试。
- 增加持久实验任务状态机，以及提交、查询、列出、取消和安全重试任务的 MCP 工具与状态 Resource。
- 将异步实验隔离到独立 worker 进程，支持排队、检查点、进度、取消、总超时、
  心跳超时、崩溃检测和 MCP 重启后的安全重排队。
- 为输出目录增加同名任务占用检查与跨进程文件租约；完整产物仍以事务方式发布。
- 增加版本化 `ExperimentSpec`、13 类确定性测量、逐项
  PASS/FAIL/未验证结论，以及理论值/仿真值/误差的结构化比较。
- 增加参数、容差、温度和可复现 Monte Carlo 扫描，包含 100 次硬上限、事务式
  汇总产物、MCP Resources 和持久 worker 支持。

- 增加 13 个不依赖 NI 数据库资产的可移植元件适配器，覆盖高价值模拟、功率、时序数字与单比特混合信号模型。
- 增加数据万用表、Bode Plotter 与 Logic Analyzer MCP 工具；缺少相位证据时明确返回不可用。
- SPICE3 ASCII raw 解析器新增复数 AC 数据、幅值、实部、虚部与相位支持。
- 完整实验自动输出中英双语独立 HTML/PDF 与带 SHA-256 的 `manifest.json`，并新增 5 个 Resource 模板。
- 公开严格声明式 JSON 元件适配器接口、贡献示例和兼容性矩阵；禁止执行代码和外部文件指令。
- 本地模板生成器改用当前 Multisim 自动创建的空白电路作为工程骨架，写入 schema 2
  manifest；`doctor` 拒绝可能静默丢失元件的旧 schema 1 包。
- 完成 116 项无 COM 测试、32/64 位 Python 安装回归、现代/旧 MCP 协议握手、
  代码型 wheel/sdist 审计以及真实 Multisim 14.3 元件与代表性仿真回归。

### English

- Migrated to MCP Python SDK 2.x with one stdio server serving both the
  `2026-07-28` and legacy protocol eras.
- Serialized every tool call onto a dedicated COM-initialized worker thread.
- Added sixteen experiment resource templates, two sweep resource templates,
  re-registration, five bilingual prompts, and validated structured results
  for the high-level workflow.
- Added a 32-bit Windows-compatible cryptography constraint and dual-era,
  resource-security, and structured-output tests.
- Added a durable experiment-job state machine with queueing, progress,
  cancellation, total/heartbeat timeouts, restart recovery, and a status
  resource.
- Isolated asynchronous experiments in restartable subprocesses and added
  cross-process output leases plus structured crash/hang diagnostics.
- Added versioned design requirements, deterministic measurements, strict
  pass/fail/unverified verdicts, and theory-versus-simulation errors.
- Added parameter, tolerance, temperature, and seeded Monte Carlo sweeps with
  a 100-run cap, transactional summaries, resources, and durable jobs.
- Added thirteen portable component adapters for high-value analog, power,
  sequential-digital, and one-bit mixed-signal models without NI database assets.
- Added data-backed multimeter, Bode Plotter, and Logic Analyzer tools.
- Added complex SPICE3 raw parsing with magnitude, real, imaginary, and phase data.
- Added standalone Chinese/English HTML and PDF reports, a SHA-256
  reproducibility manifest, five resources, and a strict declarative adapter API.
- Rebuilt user-local pack scaffolding from a blank circuit created by the
  installed Multisim version, added a schema-2 manifest, and made `doctor`
  reject legacy schema-1 packs that can silently omit components.
- Completed 116 COM-free tests, 32/64-bit Python installation checks, modern
  and legacy MCP handshakes, code-only artifact audits, and real Multisim 14.3
  component and representative-simulation regressions.

## [0.1.0-alpha.3] - 2026-08-09

### 中文

- 新增保持无参数 stdio 启动兼容的 `multisim-mcp doctor`、`serve` 和
  `config` 命令。
- `doctor` 默认无副作用检查 Python 位数、pywin32、Multisim COM 注册、模板包和
  `.ms14` 编解码器，并提供稳定 JSON、可选严格退出码和显式 `--connect` 激活验证。
- 配置生成器可输出 Claude Desktop JSON、Codex TOML 和通用 stdio JSON，默认
  仅预览且拒绝静默覆盖文件。
- 将 pywin32 首次导入的 COM 缓存输出隔离到 stderr，避免污染 MCP/JSON stdout。
- 当 pywin32 的 `gen_py` 生成包装缓存损坏时，自动回退到动态 COM Dispatch，
  无需删除用户缓存即可连接 Multisim。

### English

- Added backward-compatible `doctor`, `serve`, and `config` CLI commands.
- Added default-side-effect-free checks for Python architecture, pywin32, COM
  registration, the local template pack, and `.ms14` codecs, with stable JSON,
  an optional strict exit code, and an explicit `--connect` activation probe.
- Added Claude Desktop JSON, Codex TOML, and generic stdio configuration
  fragments with preview-first overwrite protection.
- Redirected pywin32 import chatter away from MCP and JSON stdout.
- Added a dynamic COM Dispatch fallback for stale or corrupt pywin32 `gen_py`
  wrapper caches without deleting user data.

## [0.1.0-alpha.2] - 2026-08-09

### 中文

- 增加 Linux/Docker `introspection-only` 模式，可完成 MCP 初始化和工具发现。
- 将 `pywin32` 限定为 Windows 依赖；非 Windows 自动化调用返回明确兼容性提示。
- 增加最小权限 Glama 验证镜像、严格 Docker 构建上下文和 Ubuntu 容器握手 CI。
- 实际 Multisim 电路生成、仿真和导出仍仅支持本地 Windows + 32 位 Python。

### English

- Added a Linux/Docker `introspection-only` mode for MCP initialization and
  tool discovery.
- Made `pywin32` Windows-specific and added explicit diagnostics for unsupported
  automation runtimes.
- Added a least-privilege Glama validation image, a strict Docker build context,
  and an Ubuntu container handshake check.
- Real Multisim generation, simulation, and export remain Windows-only.

## [0.1.0-alpha] - 2026-08-09

### 中文

- 建立从受限 SPICE 网表到可编辑 Multisim 原理图、真实仿真、CSV/SVG 和
  Markdown 报告的完整工作流。
- 支持主要模拟 SPICE 原语、2–16 端通用子电路、组合逻辑和 JK 触发器。
- 接入原生 XFG 函数发生器和 XSC 示波器状态。
- 增加命令白名单、外部文件指令拦截、覆盖保护和本机 stdio 安全边界。
- 增加本地模板包生成器，避免在公开仓库分发许可不明确的 NI 派生 XML。
- 发布 `multisim-mcp==0.1.0a1` 到 PyPI，并登记到官方 MCP Registry。
- 48 项无 COM 测试、19 项安装包资源测试和 8 组真实 Multisim 元件族回归通过。

### English

- Added the complete constrained-netlist → editable schematic → real simulation
  → CSV/SVG/Markdown workflow.
- Added major analog SPICE primitives, generic 2-16-pin subcircuits, logic gates,
  JK timing, and native XFG/XSC instrument state.
- Added safe command validation, overwrite protection, local-pack generation,
  and release-time asset separation.
- Published `multisim-mcp==0.1.0a1` to PyPI and the official MCP Registry.
- Verified 48 COM-free tests, 19 installed-package resource tests, and eight
  real Multisim component-family groups.
