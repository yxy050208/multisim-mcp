# Changelog / 更新日志

本项目遵循语义化版本的预发布形式。中文为主要说明，英文摘要紧随其后。

## [Unreleased]

### 中文

- 后续变更将在下一版本记录；发布分支中的 1.3.0 内容已冻结，等待 CI、标签和在线发布物核验。

### English

- Future changes will be recorded here. The 1.3.0 release branch is frozen pending CI, tag, and live artifact verification.

## [1.3.0] - 2026-10-25

### 中文

- 修复 DeepSeek Harness 0.2.1-rc.2 将插件隐藏为不兼容的问题：发布候选
  `multisim-mcp-dsh-plugin@1.3.0-rc.4` 固定使用 `@deepseek-ai/dsh-mcp-client@0.2.1-rc.2`，
  并声明 `engines.dsh`；兼容性门禁现在会检查该声明及新版 MCP Client 依赖。

- 新增 ARTEX 风格工程 Agent 工作流合同：`AgentTask`、`TaskChain`、`ArtifactRef`、
  `ApprovalGate` 和 `TaskEvent`。任务链进行 DAG 依赖校验，事件 ID 可确定性去重，产物
  只允许相对定位器或不透明 URI，审批对象不携带 bearer token；合同已进入
  `api_contract.agent_workflow` 能力发现，尚未改变现有 durable job 存储或自动执行边界。
- 新增只读 Workbench `POST /api/agent-workflow/plan`：根据自然语言需求生成方案、网表、
  布局、原生实验、优化和报告任务链；该入口只返回审阅图，`execution_started=false`，
  不调用模型、不写文件、不启动 Multisim。
- 新增传输无关 `AgentWorkflowExecutor`：按任务依赖执行注入的处理器，在审批门暂停，
  对可重试节点进行有界重试，并在处理器缺失或失败时失败关闭；默认仅接入需求审查和
  `plan_design_options`，不会自动调用 Multisim。
- 新增目录级参数-布局联调入口 `evaluate_optimization_layout`：从已完成的
  `optimization.json` 恢复每个可逆参数候选网表，逐个执行有界单板布局搜索，保存
  `layout-plan.json`、`layout-results.json` 和 `combined-ranking.json`，并按电气硬约束
  与几何硬门禁联合选出候选；可选材料化仍明确返回 `native_acceptance=pending`，不把
  构建器预检冒充 Multisim 原生验收。
- `evaluate_optimization_layout` 已接入 MCP 的 `optimization` profile；传入优化目录、
  基线元件坐标和可选探针即可由 Agent 直接启动该联调，工具总数更新为 full=123、
  optimization=71，旧 profile 的其他工具集合保持不变。
- 新增单板有界布局优化层 `optimize_layout_positions`：以真实 dry-run 几何门禁评估基线和
  局部移动候选，优先选择无错误、无回退导线、低交叉率、少弯折和短导线的方案；不会
  修改网表或 `.ms14`，仍需后续保存重开、拓扑和仿真验收。
- 收紧版本化元件清单校验：元件族、原生名、模型来源和引脚必须为非空字符串，版本列表
  必须是非空字符串，`verified` 必须是真布尔值；错误清单会在映射选择前失败关闭。
- 几何预检现在对非有限或非正的元件坐标/尺寸、非有限导线点和零长度导线段失败关闭，
  并将 `clearance` 校验为有限非负数，避免异常几何进入原生工程生成或被误判为可交付。
- 新增 `ContinuousResponseContract` 与 `compare_continuous_response_series`，对原生瞬态
  自适应采样轴上的 RC 输出做失败关闭的特征验收：初值、最终值、峰值、谷值、响应范围、
  分析端点和极值时间；不重采样整条波形，缺失/非有限/不完整数据不会通过。`adc4_dac4_transfer_split`
  现同时纳入 ADC/DAC 16 级转换合同和 `filt` 连续响应合同。
- 重新完成 ADC4/DAC4 transfer 的 Multisim 14.3 原生闭环：42 个归档文件、878 个采样值，
  `accepted/native-verified`；故障矩阵扩展为 10 类并全部拒绝。证据目录为
  `release/hybrid-multiboard-native-20261016-adc4-dac4-transfer-r3c`，不进入公开发布包。
- `adc4_dac4_edge_split` 进一步在四路数字边沿合同之外加入 `filt` 连续响应合同；Multisim
  14.3 原生归档 `release/hybrid-multiboard-native-20261016-adc4-dac4-edge-r2` 通过，离线
  复核检查 42 个文件、696 个采样值，连续响应和数字边沿故障矩阵 9/9 全部拒绝。
- 新增 `build_component_mapping_matrix` 与 `tools/build_component_mapping_matrix.py`，输出
  精确版本 manifest 的 SHA-256、元件族、原生名、引脚签名、模型来源和逐项验证状态。
  当前 14.3 清单为 15 项（14 项 `native-verified`），14.2 与 14.3.1 明确保持
  `manifest-unavailable`，不会跨版本回退。
- `dnot4_rc_split` 复用 `ContinuousResponseContract` 验证普通数字源到 RC 负载的完整参考
  一致性；Multisim 14.3 归档检查 42 个文件、1,090 个请求电压采样值，`filt` 最终值 `4.6656276 V`，
  故障矩阵 8/8 全部拒绝。
- 新增 `vcvs_rc_split` 与 `vccs_rc_split` 受控源双板案例。VCVS 跨板传递 `raw` 并验证
  接收端 RC；VCCS 采用 `ctrl` 控制信号跨板、接收板保留真实 G 源，避免无负载电流源
  输出电压的错误比较。两例均在 Multisim 14.3 原生通过，分别以 522/392 个请求电压
  采样值和 8/8 故障拒绝完成证据闭环。
- 新增受控源参数角落矩阵：VCVS 增益 `0.5/2`、VCCS 跨导 `0.5mS/2mS`。四个 Multisim
  14.3 原生角落均通过接口、完整参考和连续响应门禁，各自故障矩阵 8/8 拒绝；VCVS
  接收夹具高电平按声明增益生成，VCCS 继续采用 `ctrl` 跨板边界。
- 新增受控源负载变化角落：VCVS 串联电阻 `500Ω`、滤波电容 `2µF`，VCCS 串联电阻 `2kΩ`、
  并联负载 `50kΩ`。四个 Multisim 14.3 原生角落均通过，分别完成 8/8 故障拒绝。
- 新增受控源组合角落回归：`vcvs_rc_split_combo_low` 同时覆盖增益 `0.5`、串联电阻 `500Ω`、
  滤波电容 `2µF`；`vccs_rc_split_combo_high` 同时覆盖跨导 `2mS`、串联电阻 `2kΩ`、
  并联负载 `50kΩ`。两个 Multisim 14.3 原生归档均为 `accepted/native-verified`，各自
  故障矩阵 8/8 拒绝；结果限定于声明的离散参数点、模型、夹具和版本。
- `run_hybrid_multiboard_regression.py` 新增无 COM 的 `--list`，输出全部已登记案例及其
  合同摘要，便于后续按 Multisim 版本批量准备和核对独立验收任务。

- 增加 `adc1_dac1_split` 受限单比特 ADC→DAC 双板回归：两板通过 Multisim 14.3
  `HDR1X4` 交换 `digital`、`high` 和双地，接收端使用显式 BV 数字夹具与独立 5 V
  电源夹具，RC 输出完成保存重开、拓扑、布局、探针、瞬态、跨板和完整参考验收，状态为
  `accepted/native-verified`。案例输入固定为 `DC 5`，只证明稳定阈值基线，不外推跳变输入
  的边沿动态；对应归档复核检查 42 个文件、348 个采样值，7/7 故障注入全部拒绝。
- `explicit-digital-fixture` 现在允许受限的 `BV` 行为适配器，同时继续拒绝未知器件、缺少
  数字适配器或不完整端口合同；混合多板离线复核器和故障矩阵会根据归档中的 `case_id`
  选择正确的板、探针和完整参考合同，并兼容旧 DNOT4 归档。
- 增加 `adc4_dac4_split` 四位混合双板基线：使用两个已验证 `HDR1X4`，分别承载四位数据
  与电源/地；结构化模型字段扩展到有界 4 KiB 以容纳 ADC4 的嵌套行为表达式。稳定 `DC 5`
  输入下，8 个跨板连接器引脚、逐板保存重开、瞬态、完整参考和 7/7 故障拒绝均通过；这
  仍是声明的便携 BV 模型合同，不等同于真实芯片精度或动态性能认证。
- 新增 `DigitalEdgeComparisonContract` 与 `tools/compare_digital_edge_series.py`，用于对
  自适应瞬态轴上的数字边沿做失败关闭的事件比较。它只比较初末电平、边沿方向/数量和
  阈值穿越时刻，不插值整条波形；在 ADC1 跳变失败归档上识别 5 个边沿，最大时刻差
  `1.064e-10 s`，作为动态证据层保留，不能替代模拟波形或器件动态验收。
- 新增 `adc1_dac1_edge_split` 原生动态案例：数字接口和完整参考使用事件合同，模拟/供电
  网络继续使用完整采样门禁；Multisim 14.3 保存重开、拓扑、布局、瞬态和边沿比较全部
  `accepted/native-verified`。动态归档故障矩阵覆盖 8 类篡改，缺失事件、方向、数量或
  时刻证据均失败关闭。
- 增加 `adc4_dac4_edge_split` 四位跳变案例：四路编码边沿和完整参考使用事件合同，
  `filt` 另行使用连续响应合同；两个 `HDR1X4` 的 8 个连接器引脚、逐板保存重开、拓扑、
  布局和标量接口均通过，状态为 `accepted/native-verified`。原始 DAC `analog_out` 仍由
  转换案例单独验收；对应故障矩阵现覆盖 9 类篡改并全部拒绝。

- 新增受限混合数字/模拟双板原生验收案例：数字 `DNOT4` 板通过 14.3 `HDR1X4` 将
  `sig`、供电和双地连接到 RC 负载板；接收端边界使用显式 `explicit-digital-fixture`，
  只允许 V、DNOT4 和 R/C/L。Multisim 14.3 逐板保存重开、拓扑、探针、瞬态、跨板序列和
  完整参考均通过，状态为 `accepted/native-verified`。严格采样轴和缺少接收端夹具的失败
  证据保持失败关闭；新增 CLI 回归入口可在不启动 COM 时输出逻辑合同和夹具 digest；
  该案例不扩展为任意多板联调或 PCB 生产能力。
- 新增混合多板离线证据复核和故障矩阵：归档 manifest、夹具 digest、连接器 P1–P4、
  探针映射、重开拓扑、采样轴、完整参考和文件哈希均会失败关闭；本机 7/7 故障注入
  全部被拒绝，复核过程不启动 COM。

- 刷新版本准入矩阵：32 位 worker 实测检测到 Multisim 14.3，14.3 标记为
  `api-verified`；14.2 与 14.3.1 保持 `unverified`，不借用 14.3 的元件或电路结论。
  `runtime.json`、`native-api.json`、`matrix.json` 由 SHA-256 manifest 绑定；该证据只
  覆盖 Automation API 探测，完整元件和电路族仍须按版本独立原生回归。

- 修复混合信号回归中生成适配器输出探针的原生重开遗漏：当最长中段导线距离异网仅
  8 个绘图单位以内时，不再把探针放在线路中段，而是退回到已验证的元件端点；安全
  直线仍保留中段探针以改善图面。新增近旁异网与安全中段单测。Multisim 14.3 实机
  重新验证 `shift_s0_rc_load` 和 `adc4_rc_bridge`，保存重开、ReportNetlist、布局、
  引脚、瞬态和全部请求输出均通过，混合信号矩阵恢复为 13/13。该规则解决的是探针
  持久化/观测通道问题，不改变网络拓扑；探针返回值现在同时记录源网络、候选元件端点、
  实际落点和 `terminal`/`interior` 选择，便于后续适配器审计。结果也不外推到其他版本。

- 统一实验链路中的模型来源留证：`ModelReference` 与元件选择的 `model_source` 现在
  归一到同一 `model_provenance` 合同。SPICE 兼容性审计、实验目录 manifest、Markdown
  报告和资源摘要会共享来源、SHA-256、许可证状态及确定性 digest；缺失声明保持为
  `missing`/`unknown`，不会自动读取或下载模型，也不改变旧调用接口。

- 新增 `DesignPatchApplicationService` 作为补丁审批、持久化应用、回滚和崩溃恢复的
  传输无关应用层；CLI 已切换到该入口，保留原有一次性令牌、目标锁、事务回执和
  journal 语义。后续 MCP/Workbench 写入适配器可复用同一服务，不复制事务逻辑。

- Workbench loopback API 增加显式 `--enable-writes` 开关及受项目根目录约束的补丁审批、
  应用、回滚和恢复路由。默认只读行为保持不变；HTTP 不返回 bearer token，令牌只写入
  本地文件并由同一 `DesignPatchApplicationService` 消费。

- Workbench 增加验证后提交路由，复用现有 verification-plan、实验 manifest 和 workflow
  journal。只有真实实验的全部硬约束通过才写回设计，失败候选不会自动提交。

- MCP `full` profile 新增七个补丁事务工具，统一复用 `DesignPatchApplicationService`：
  基础审批、应用、回滚、恢复，以及验证后审批、实验提交和恢复。审批只把一次性令牌
  写入调用方指定的新文件，响应不返回 bearer token；执行只读取令牌文件并明确返回
  写入、仿真、提交和失败候选保留状态。受限 `core`、`experiment`、`optimization`
  profile 保持原工具集合，DeepSeek Harness full 契约更新为 122 个工具。

- 加强验证后 workflow 恢复安全性：manifest 绑定 owner PID，运行中的实验拒绝恢复；
  已有回执但残留底层 patch journal 时，恢复先完成 journal 的摘要、审批和清理校验，
  不再把未消费或未清理的事务直接标记为 committed。

- 验证后 workflow manifest 升级到 schema 2。旧 schema 1 清单仍可读取用于审计，但因缺少
  owner PID 元数据，恢复会失败关闭并要求人工处理底层 journal，避免兼容迁移降低安全性。

- 重新完成复杂数字原生回归：Multisim 14.3 单板矩阵 5/5 通过，命名引脚 141/141 通过；
  两个双板 HDR1X4 案例在 DC/TRAN/AC 共 6/6 次 `accepted/native-verified`。实测覆盖
  保存重开、ReportNetlist、布局、跨板接口、完整参考、远端 1 kΩ 负载和报告导出；证据及
  本地模板包不进入开源仓库，也不外推为任意数字拓扑或其他 Multisim 版本保证。

- `compare_design_variants` 现在支持同一基准 `CircuitDesign` 的 `DesignPatch` 候选：
  补丁先按 compare-and-swap 合同在内存中校验和应用，再沿用统一实验、硬约束和实测排名。
  候选补丁副本与设计一起写入比较目录并纳入 manifest；流程保持只读，不会自动修改或采纳
  源工程。完整设计候选的旧调用方式保持兼容。

- 新增受限真实 LM324AJ 双板合同层：固定 `sensor`/`conditioner` 分区、`HDR1X4` 的
  `P1=stage1`、`P2=vcc`、`P3=vss`、`P4=0` 和 LM324AJ A 单元端子顺序；接收板使用
  明确声明的原生上游电路夹具。新增保存重开后的全引脚、R/C SI 值、LM324AJ/LM158_4
  `CiModel` 指纹门禁与离线故障注入回归。现有 MCP 多板工具可通过 `vendor_contract`
  复用该合同；预览不启动 COM。范围仅为 Multisim 14.3、本机授权模型和固定拓扑，
  不包含任意供应商多板、跨板联合优化或高级排线。详见
  `docs/VENDOR_MULTIBOARD_ACCEPTANCE_20261011.md`。

- 完成真实 LM324AJ 双板的最终原生闭环：发送板实验显式加入接收端 LM324AJ 输入/负载
  夹具，避免 AC/TRAN 独立板边界条件失真；通用验收器新增可审计的比较门槛参数，默认
  仍为 `1e-9 V`，LM324AJ 合同明确使用 `1e-7 V`。Multisim 14.3/Python 3.13 64 位下
  OP、AC、TRAN 三种分析均 `accepted/native-verified`，TRAN 仅在调用方显式选择
  `series_alignment=linear` 时进行自适应轴重采样。真实模型、逐板/完整参考、接口序列、
  引脚、R/C 与 `CiModel` 指纹均已归档；本机证据不进入公开仓库。

- 在真实双板验收结果中补充两份不含实验夹具的物理板图导出。每份板图都经过 Multisim
  保存重开、HDR1X4 四针、LM324AJ 五端子、R/C SI 值和源/重开模型指纹检查，并写入 PNG、
  SPICE、组件清单与 SHA-256 清单；物理板图只代表板级设计视图，不代表再次完成孤立板仿真、
  PCB 制造或高级排线优化。

- 用第二组 `100 mV / 增益 10 / 1 kHz / ±15 V / 100 kΩ` 需求重建同一 LM324AJ 双板合同，
  OP/AC/TRAN 和两份物理板图再次全部通过；夹具值从批准设计派生，确认负载、供电、增益
  和滤波参数没有被固定样例吞掉。

- 增加真实双板证据归档复核器 `tools/verify_vendor_multiboard_evidence.py`，不启动 COM，
  重新检查合同、分区、夹具、引脚、R/C、模型指纹、完整采样矩阵、接口/参考比较、物理
  板图、报告和 SHA-256 清单。离线故障注入覆盖 13 类篡改，修改或重新计算错误哈希均保持失败。

- 增加真实 LM324AJ 单板准入记录和 14.3 精确版本回归：锁定 `1IN+`、`1IN-`、`VS+`、
  `VS-`、`1OUT` 的语义顺序，未校准工作点不达标时返回 `target-not-met`，测量反馈的
  `VTRIM` 原型可在同一验收范围内重新生成并通过 OP/AC/TRAN。模型正文和授权模板仍只
  在本机验证，不进入公开包；14.2、14.3.1 或其它版本不会借用 14.3 映射。详见
  `docs/VENDOR_COMPONENT_ACCEPTANCE_20261011.md`。

- 修正自然语言真实运放入口对 `±12 V` 供电的解析，并让低截止频率自动选择远离截止点的
  通带采样窗口。第二个 `50 mV / 增益 5 / 500 Hz / 47 kΩ` 需求在 Multisim 14.3 上
  经过 5 个未校准候选和 1 个测量反馈校准候选，最终 OP/AC/TRAN 原生采样通过；失败候选
  仍保留为 `target-not-met`，没有放宽指标。

- 完成固定理想双板信号流程的首个公开需求回归矩阵：六组增益、偏置、源阻抗、截止频率
  与非零输入相位变体，在 Multisim 14.3 上共 12/12 OP/AC 原生分析通过；补充三板身份、
  完整观测映射、采样状态/轴/有限数值和归档哈希门禁。7 类故意损坏的证据均被拒绝，
  非数值阻容读回以明确可序列化失败返回。该结果仍限定于理想 OPAMP5、显式单向前级夹具
  和基本双板联调，不代表真实运放、PCB 或高级布线优化。

- 已审核 `signal-active` 显式夹具接入固定双板传感器流程，复用现有规划/验收工具；
  前级 RC 电路片段保留 AC 幅相，来源及全部分区/元件/连接器进入一致性门禁。
  HDR1X4 支持两根独立地线；保存重开严格核对全部引脚、SI 阻容与理想模型。
  四组 Multisim 14.3 原生 OP/AC 验收通过，含 64 位主进程、独立板图与报告导出。
  修复理想运放输出探针重开遗漏，仿真前拒绝请求探针缺失；补充不启动 COM 的证据复核。
  图面仍需审阅；不宣称真实器件、PCB 或任意双向耦合联调。高级布局和联合优化不在本轮范围。

- 有源编译新增显式 DC/AC 激励与源阻抗合同，组合前后摘要分别保存；新增夹具审阅项，
  审批时重建全部组合连接，防止沿用旧凭证或修改源、负载与引脚。既有原生模拟入口支持
  精确绑定新预览/审批，补充阻容回读、理想模型表达式及版本检查。Multisim 14.3 上四组
  理想有源方案与默认输入两端点完成保存重开、OP/AC、PNG/CSV/HTML 导出，包含 64 位
  主进程实机运行；能力发现记录具体案例。基础无激励输出保持兼容；未宣称真实芯片、
  实物或任意拓扑验收，图面交叉与标签仍需审阅。详见有源模板本轮原生验收文档。

- `signal-active` 接入统一元件解析、审批及 `CircuitDesign`/SPICE 预览链：显式理想
  OPAMP5、独立滤波电容额定值审查、有限增益校正、源/负载合同和正负偏置，支持受限
  放大/衰减与一阶低通。真实芯片不会静默替换成理想模型；模型合同进入审批摘要。
  往返门禁核查引脚、数值、模型和参数；旧无源摘要保持兼容。独立节点计算及接口回归
  属于软件证据；带显式夹具的本轮原生验收见上述新增记录，不改变既有公开工具数。

- 新增共享工程能力发现：MCP `runtime_status`、元件目录支持可选 `target_version`，
  本机 `/api/capabilities` 和只读 CLI `capabilities` 共用契约。分别返回旧目录标签、
  受限工作流/profile、实际编译矩阵、精确版本元件映射与 SHA-256、公开文档原生案例。
  缺少目标版本清单时不借用邻近版本；历史记录、相关底层实验和本机依赖不冒充当前
  工程验收。已有契约字段与 115 个 MCP 工具保持兼容。

- 明确后续开源/商业研发边界：开源推进复杂电路、基本多板联调和原生正确性验证；商业版
  重点做安装和使用体验，后续专有多板优化、跨板整体优化及高级布局/排线优化独立维护。
  已有公开代码保留原许可和贡献者归属。
  发布审计与 wheel/sdist 检查拦截商业目录、商品归档和 NI 本地资产，CI 及公开发布工作流
  在上传前执行同一边界门禁；Git 候选路径使用 NUL 分隔，避免中文文件名转义导致漏检。
- 统一模型许可证声明检查，`pending`、`NOASSERTION`、`待核对` 等保留为证据缺口并阻断
  模型审批；来源字段拒绝类型错误、控制字符和静默截断。外部模型重哈希使用实际有界
  读取并拒绝跨平台盘符、目录穿越、Windows 流/设备别名及根目录外的符号链接，返回结果
  只保留引用元数据，不包含模型正文或根目录绝对路径。

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
