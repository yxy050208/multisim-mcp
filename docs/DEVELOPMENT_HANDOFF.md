# 当前开发落点

> 2026-10-10 最新范围：开源重点研发复杂电路、基本多板联调和可复现的原生验证；
> 商业版重点提供便于安装和使用的体验，后续专有多板优化及高级布局/排线优化独立维护。
> 历史公开实现保留原有许可。规则见 [`OPEN_SOURCE_SCOPE.md`](OPEN_SOURCE_SCOPE.md)
> 和根目录 `AGENTS.md`。已完成公开资产发布门禁及模型来源/许可证/有界重哈希加固。

以代码、原生实验和本文为恢复依据，不以历史回复中的完成声明代替验收。

2026-10-11 最新：已完成 `signal-active` 显式夹具/审批的单板原生闭环，及固定双板
传感器示范。双板四组最终 OP/AC 实机验收通过，包含 64 位主进程；原生引脚、阻容与
理想模型回读、逐点参考核对及独立板图/报告齐备。已修复零源阻抗样例的运放输出探针
重开遗漏。详见 [`双板验收`](SIGNAL_MULTIBOARD_NATIVE_ACCEPTANCE_20261011.md) 与
[`单板验收`](ACTIVE_SIGNAL_NATIVE_ACCEPTANCE_20261011.md)。下一步扩展需求变体/
故障注入，然后接真实运放与受限模拟数字接口；不外推为任意复杂工程或实物验证。

2026-10-11 后续回归已完成：新增六组需求变体（含 `+90°`、`−45°`、`+30°`、`−90°`
输入相位、零源阻抗、正负偏置和小增益），在 Multisim 14.3/Python 3.13 64 位下
OP/AC 共 12/12 `native-verified`。比较器加严要求三类工程身份和完整观测映射，
归档故障 7/7 拒绝；当前全量测试 1048 项、46 项跳过、357 个子测试通过，wheel/sdist
检查通过。下一步是
真实运放型号的单板闭环和 14.3 版本化映射复测，随后再扩展受限模拟/数字接口；不把
理想 OPAMP5 或单向夹具结果宣称为真实器件/任意多板联调。

2026-10-11 真实运放单板闭环已完成：LM324AJ A 单元在 Multisim 14.3 的正负电源端子
顺序已修正并由回归测试锁定；保存重开后的全引脚、模型身份和正文指纹均通过。未校准
候选的原生工作点为约 `1.034418 V`，按统一 `0.99..1.01 V` 要求诚实返回
`target-not-met`；测量反馈加入理想 `VTRIM` 原型后，OP/AC/TRAN 采样要求通过。
这只证明有界校准闭环，不能当作已完成的硬件参考源或量产校准电路。LM324 证据仅适用
14.3 和本机授权 `LM158_4` 模型，版本准入记录见
[`VENDOR_COMPONENT_ACCEPTANCE_20261011.md`](VENDOR_COMPONENT_ACCEPTANCE_20261011.md)。

同日又完成第二个真实模型需求变体：`50 mV`、增益 `5`、`500 Hz`、`±12 V`、`47 kΩ`
负载。该过程先暴露自然语言解析器把 `±12 V` 回退成默认 `±15 V`、并把低截止频率的
100 Hz 纳入通带的问题；修复后，解析结果保留 `−12/+12 V`，通带改为 `5..50 Hz`。
Multisim 14.3 原生 5 个未校准候选均按目标失败关闭，测量反馈校准候选通过 OP/AC/TRAN，
工作点约 `0.2499983 V`、通带增益 `4.95083..4.99984`。失败和通过目录均只在本机
忽略目录保存，详见上述准入记录。

2026-10-11 至 2026-10-13 完成真实 LM324 基本双板阶段：`vendor_multiboard.py` 只
接受上述 `LM324AJ` A 单元双级前端和 Multisim 14.3。`sensor` 板保留输入/第一级，
`conditioner` 板保留第二级/负载，`HDR1X4` 固定为 `stage1/VCC/VSS/0` 四针。接收板
实验使用显式 `explicit-native-vendor-fixture` 上游片段；发送板实验另加入完整的接收
端输入/负载 `explicit-native-vendor-load-fixture`，复现 R2/C2、第二级 LM324AJ 和反馈
负载的边界条件，避免把独立板运行误判成端口电气不一致。合同会在执行前关闭模型、供电、
引脚、分区和版本改动；保存重开后复用现有原生验收器检查完整引脚、阻容 SI 值、
LM324AJ/LM158_4 `CiModel`/正文指纹。新增 60 项聚焦测试（含 27 个子测试）通过，
并在本机 Multisim 14.3/Python 3.13 64 位完成最终 OP/AC/TRAN 三种原生双板验收：三种
分析均为 `accepted/native-verified`。AC 最大接口差约 `1.20e-15 V`、最大参考差约
`4.50e-11 V`；TRAN 使用显式线性重采样，最大接口差约 `1.23e-10 V`、最大参考差约
`1.69e-10 V`。跨工程真实模型比较门槛记录为
`1e-7 V`，仅适用于该 LM324AJ/14.3 合同。每个分析还导出了两份不含实验夹具的物理
板图，并通过保存重开、完整引脚、R/C 与源/重开模型指纹门禁；它们没有再次执行孤立板
仿真。证据保存在本机忽略目录
`release/vendor-multiboard-lm324-20261013-{op,ac,tran}-native-v3/`，不进入公开仓库。
该结果不外推到其它版本、其它厂商或任意拓扑。随后已完成第二个参数变体
`100 mV / 增益 10 / 1 kHz / ±15 V / 100 kΩ` 的 OP/AC/TRAN、物理板图和哈希门禁，
三种分析同样为 `accepted/native-verified`；证据在本机
`release/vendor-multiboard-lm324-20261013-variant2-{op,ac,tran}-v1/`。下一步是补充
更系统的故障注入回归，再接受限模拟/数字接口，保持高级跨板联合优化和排线优化在商业版
边界内。最终复核证据已统一归档到
`release/vendor-two-board-evidence-20261013-final/`：两组需求的六个目录均通过无 COM
一致性复核；复核结果明确为归档一致性，不替代新的当前机器仿真。

- 当前任务：开源单板闭环与基本多板联调回归、模型来源和公开发布门禁；后文保留历史验收记录。
- 已完成：正确输入激励和按目标增益算值、结构预检、原生 OP/AC/TRAN、模型/引脚核查、
  波形专用源模板、共射布局、实际 MCP 执行、报告和 manifest 完整性验证；开发版现支持
  估算值附近最多 5 个 E24 发射极电阻候选逐个原生仿真，并按实测 1kHz 增益选择。
- 入口：`run_natural_common_emitter`，预览默认不启动 Multisim；执行要求新输出目录和本地
  VDC/VPULSE 元件模板。`tools/run_common_emitter_plugin_acceptance.py` 可复现实机验收。
- 实测：`sample_validation/ce_mcp_acceptance_final`，细节见 COMMON_EMITTER_NATIVE_ACCEPTANCE.md。
- 已完成：用 Multisim 14.3 对 5 个 E24 发射极电阻候选逐个实机回归，3 个候选通过当前
  声明的原生验收，`RE=560 Ω` 以 1kHz 增益误差最小被选中；证据保存在本地
  `sample_validation/ce_candidate_search_002`，不提交到开源仓库。
- 已完成：候选报告会从每个原生 AC 扫频提取峰值、−3dB 阈值和上下截止边界；边界不在
  请求扫频范围内时返回 `unverified`，不会伪造带宽。新增回归覆盖两种情况。
- 已完成：原生 AC_VOLTAGE 载体接受并写回 `DC … AC … SIN(...)`，共射入口在正弦需求
  下执行原生稳态窗口并计算 H2–H5 THD；`ce_sine_thd_001` 实测选中 `RE=560 Ω`，
  THD `0.002435%`，1% 门槛通过。
- 已完成：正弦模式对选中候选执行递增输入幅值扫描；粗扫描实测 `200mV` 输入峰值仍为
  1% THD 内，`500mV` 首次超过门槛（THD `1.6907%`），输出峰峰值约 `3.8023V`；
  随后通过 4 次原生二分将证据边界收窄到 `368.75mV–387.5mV`。结果是有限采样边界，
  不宣称连续精确的最大摆幅。
- 已完成：在扫描边界附近增加 4 次原生二分细化；每个中点保留独立工程、CSV、报告和
  manifest。Multisim 14.3 实测将 1% THD 边界从 `200mV–500mV` 收窄到
  `368.75mV–387.5mV`，最大已验证输出峰峰值 `6.9690V`。
- 已完成：幅值点现在同时检查 THD、三个声明的 OP 工作点以及集电极到电源轨的动态裕量；
  `ce_sine_margin_20261003` 实测所有已验证点均有超过 `2.30V` 的最小裕量，边界由
  THD 超限而非电源轨饱和决定。下一项是扩展波形整形和容差扫描。
- 已完成：自然语言中的“元件容差 N%”会触发 13 点确定性电阻角落扫描。`±5%` 实测
  13/13 角落通过，最坏增益误差 `9.10%`；每个角落保留独立工程、CSV、报告和 manifest。
  当前下一项是温度/晶体管参数角落与波形整形，仍不宣称随机蒙特卡洛或实物容差覆盖。
- 已完成：正弦频率和输入峰值不再写死为 1kHz/1mV；请求值会同步到原生 SIN 源、AC
  频率邻域、TRAN 范围和 THD 计算。2kHz/10mV 实测通过，THD `0.01985%`。下一项
  转向温度角落；晶体管模型参数角落已完成白名单接入和 2N3904 实机回归。
- 已完成：2N3904 `Is/Vaf/Bf` 模型参数白名单覆盖。自然语言“晶体管参数容差 5%”会生成
  9 个独立原生工程，逐个进行模型正文回读、OP/AC/TRAN、增益、THD 和集电极轨裕量
  验收。`ce_model_corners_sine_20261003` 实测 9/9 通过，最坏增益误差 `4.6619%`，
  最高 THD `0.02001%`，最小轨裕量 `5.6118V`。
- 已完成：温度能力探测。对共射工程副本在 Multisim 14.3 的 `DoCommandLine` 执行
  `.temp 0/25/85`，日志均返回 `no such command available in XSPICE`；三点 OP 读数和
  保存重开后的读数不变。`tools/probe_native_temperature.py` 固化了复制、执行、回读和
  证据留存流程，结果只能是 `unsupported`/`unverified`，不会伪造温度角落通过。
  温度角落、多版本实机认证、完整 CIR 迁移尚未完成。
- 已完成：在当前主线 `c1396fc` 上从本机 Multisim 14.3 授权样例重新生成模板包，完整
  执行五类数字回归（组合链、DFF、四位计数器、四位移位寄存器、计数器解码负载）。
  五个案例均通过布局、重开拓扑、原生器件完整性、瞬态输出和引脚证据，0 个拓扑不匹配；
  `counter4_load` 带四路电阻负载也通过。因此 PR17 历史负载丢失现象在当前主线已不复现，
  后续应转向多版本复测和更高复杂度的混合信号/多板闭环，而不是重复修复同一问题。
- 已完成：新增 `tools/run_compatibility_matrix.py` 与版本范围证据结构。2026-10-03
  实机探测检测到 Multisim 14.3，矩阵将 14.3 标记为 `api-verified`，将未安装的 14.2
  标记为 `unverified`；工具会保留 `native-api.json`、`matrix.json` 和 SHA-256 清单，
  不会把相邻版本的能力复制成通过。完整元件族和电路流程仍需在各版本分别实机回归。
- 已完成：数字回归器现在在执行案例前读取实际 Multisim 版本，并要求精确匹配的已验证
  `components-<version>.json`。指定 14.2 而实际运行 14.3 时，实机验证在 0 个案例执行
  前返回 `version-mismatch`；指定 14.3 的 `counter4_load` 通过布局、重开拓扑、原生
  器件完整性、瞬态和四路输出观察，证据保存在本机
  `C:\path\to\multisim-evidence\digital-counter4-gated-20261003`。
- 已完成：在版本准入生效后重新运行完整五案例数字矩阵；`logic_chain_load`、`dff_load`、
  `counter4_load`、`shift4_load`、`counter4_decode_load` 全部通过，5/5 案例引脚证据
  完整，141 个命名引脚连接通过，0 个失败、0 个拓扑不匹配。证据保存在本机
  `C:\path\to\multisim-evidence\digital-matrix-20261003-gated`。
- 已完成混合信号阶段第一组原生基线：新增 `hybrid_regression` 与
  `tools/run_hybrid_regression.py`，`not_rc_load`、`logic_chain_rc_load`、
  `counter_q0_rc_load` 和 `shift_s0_rc_load` 四个案例均在 Multisim 14.3 实机通过，
  `diode_rc_shaper`（本地授权 `1N4001GP` 模型）也已通过。
  工具先生成带探针的 `.ms14`，再由 Multisim 重开保存副本并直接执行 COM 瞬态；四个工程
  的原生拓扑、器件完整性、命名引脚连接、数字 0/5 V 摆幅和模拟 RC 动态响应均通过；
  回归器把完整引脚证据纳入最终通过条件，并为二极管增加 A/K 到源网络的显式契约。
  正式矩阵证据保存在
  `C:\path\to\multisim-evidence\hybrid-native-matrix-20261003-v7`；
  这只是数字到模拟边界基线，尚不代表 ADC/DAC、二极管整形、受控源或任意混合信号拓扑
  已覆盖。
- `vcvs_rc_bridge` 已通过原生重开、D/G/S/SUB 到源网络的端子回读、瞬态和 RC 响应门禁；
  VCVS 端子契约已纳入通用拓扑校验；`vccs_rc_bridge` 也已通过 G 源受控电流和原生
  瞬态门禁。此前混合矩阵为 8/8。H 源实验曾出现拓扑通过但输出全零，证据保存在本机
  `hybrid-ccvs-rc-20261003-v1/v2/v3`，暂不计入通过矩阵，后续需单独定位其载体语义。
- `dac_rc_bridge` 曾作为待验收案例，用于验证 `@DAC1` 行为桥接在原生工程重开后的
  数字到模拟响应；下面记录其最终闭环结果。
- 已完成 `@DAC1` 原生闭环。根因是行为源的 `V(digital)`/`V(high)` 表达式引用不会让
  Multisim 为只有单个实体引脚的依赖网建立可保存的节点；命令引擎可运行但原生重开后
  输出通道缺失。ADC/DAC 适配器现在为表达式依赖网加入 1 GΩ 高值锚点。`dac_rc_bridge`
  在 Multisim 14.3 完成重开、拓扑、引脚、布局和 COM 瞬态验收，输出 `raw` 为 0–5 V，
  `filt` 具有 0–0.3768 V 的 RC 响应；单比特 ADC/DAC 阶段的正式矩阵为 10/10。历史证据保存在本机
  `C:\path\to\multisim-evidence\hybrid-native-matrix-20261004-v10`。
- 已完成对称的 `adc_rc_bridge` 原生闭环。ADC 端的模拟输入和电源依赖同样通过 1 GΩ
  高值锚点物化；Multisim 14.3 重开、拓扑、引脚、布局和 COM 瞬态均通过，输出 `digital`
  为 0–5 V，`filt` 具有 0–0.3768 V 的 RC 响应。ADC/DAC 的单比特桥接现均有独立原生
  证据；后续仍需更高位宽 ADC/DAC 和真实器件模型验证。
- 已完成 `ADC4`/`DAC4` 四位便携桥的 Multisim 14.3 原生探针：ADC4 四路输出和各自
  RC 负载均通过 0–5 V 摆幅、重开拓扑、引脚和 COM 瞬态；DAC4 原生输出出现 16 级
  组合电平（0、0.333、0.667 … 5 V）。独立证据保存在本机
  `C:\path\to\multisim-evidence\hybrid-multibit-probe-20261004`，
  正式混合信号回归矩阵已扩展并通过 12/12。
- 已完成 `adc4_dac4_transfer` 原生闭环：在 Multisim 14.3 重开工程后执行 68 点三角
  输入扫描，阈值附近排除 2 点后仍覆盖 0–15 全部 16 个编码；ADC 编码、数字电平、
  DAC 加权重构和最终 RC 输出均通过。证据保存在本机
  `C:\path\to\multisim-evidence\hybrid-native-matrix-20261005-v13`，
  当前正式混合信号矩阵为 13/13。
- H 源（CCVS）已用闭合控制支路重新验证，排除了此前“理想源控制回路未闭合”造成的
  假阴性：命令引擎能得到 `raw` 的 0–5 V 和 `filt` 的 RC 响应，但 Multisim 14.3
  原生工程重开后的 COM 输出仍为全零。其连接表和引脚拓扑正确，但原生行为未通过，
  因此 H 源继续标记为 `native unsupported/unverified`，不计入 13/13 正式矩阵。有效
  失败证据保存在本机 `C:\path\to\multisim-evidence\hybrid-ccvs-rc-20261004-v4`。
- Multisim 后端能力现在显式记录官方 Automation API 的来源、`MultisimInterface.MultisimApp`
  ProgID、独立 32 位 COM worker、版本探测范围，以及官方接口没有元件放置和导线绘制方法。
  未来接入新的 NI API 时沿用同一 EDA 后端契约，不改变源网表、拓扑门禁和实验报告格式。
- 多板规划已增加板级 `max_components` / `max_connector_pins` 约束、每板接口清单和
  `feasible`/`infeasible` 结果；固定的组件板归属会在候选枚举中保留。可行候选现在
  附带确定性的逐板 `logical_artifacts`（组件、板内/跨板网络、连接器和接口），但其
  `status=logical-only`、`verification_status=unverified`，尚未自动发布多个独立 `.ms14`
  工程；EDA 核心现可从 `CircuitDesign` 生成逐板结构化设计和 SPICE 预览。下一步应将
  这些预览先通过 `interface_validation` 结构门禁，再逐板绑定到原生 `.ms14` 生成、
  回读和仿真门禁；结构 `valid` 仍不代表原生电气通过。
- 不再采用“最终生成后替换标签就宣布工程正确”的流程；必须让 Multisim 打开、保存、
  回读真实器件属性并导出实际图像，然后核验最终产物的 manifest。
- RLC 入口另外拒绝电压源载体遗留的 `10Vpk/5kHz` 示例标签，并重新解码每个候选的
  `analysis-002.ms14` 检查 Multisim 保存后的实际标签；图面源参数检查与原生 OP/AC
  验收分开留档，避免把“仿真通过”误报成“工程图可交付”。

## 2026-10-06 进展校正

- 多板原生验收已完成逐板 `.ms14` 生成、Multisim 保存/重开、ReportNetlist、布局、原生
  DC/TRAN/AC 和完整参考闭环。TRAN 不再只比较末值，AC 不再只比较末频率幅值；新增
  `native_analysis_series` 证据会保留完整原生采样轴，逐点比较实部/虚部，采样轴不一致
  时不插值并保持未验证。
- Multisim 14.3 实测 `multiboard-fixture-divider-20261006-series-dc`、`-tran`、`-ac`
  均为 `accepted/native-verified`；TRAN 58 点、AC 7 个频率点，跨板接口和完整参考最大
  差异为 0。相关工程、CSV、JSON 和 PNG 均在源码仓库外，不得提交。
- 因此本文件早先关于“多板尚未自动发布独立 `.ms14` 工程”的描述已过时；当前剩余边界
  是连接器/板级物理约束、更多 Multisim 版本的独立实机矩阵，以及多板全局优化目标，不能
  把 14.3 的这组三元件分压样例外推为任意复杂多板工程。

## 2026-10-06 连接器合同推进

- 已完成：多板规划器支持显式 `connectors` 合同。每个连接器需要 `part`、参与板、每块板
  的物理 `instances` 和从 1 开始连续的 `pins`；每个 pin 保存网络、信号类型、方向，
  可选保存板端方向以及电压/电流/阻抗约束。
- 已完成：结构门禁现在检查跨板网络是否完整覆盖、同一网络是否重复占用 pin、连接器是否
  引用了错误的板、地网信号类型、端点方向冲突、物理实例一一归属和板级 pin 容量。
  合法合同会展开为带 `part`/`instance` 的板端接口，非法合同保留候选但标记为
  `infeasible`，不会进入可交付分区。
- 兼容策略：没有显式 `connectors` 的旧请求仍能生成逻辑预览，但返回
  `connector_contract_status=inferred`、`verification_status=unverified`；这只是网表级
  连接器推断，不是实际封装或 Multisim 原生连接器证据；候选的
  `native_connector_ready` 会保持 `false`。显式合同只有在版本感知的原生执行阶段
  解析到已验收的映射后才会打开；当前 14.3 清单已经加入 `HDR1X4`，其他 part 仍为
  `mapping-pending`。已有 14.3 多板验收证据未被重写。
- 2026-10-07 已完成 `HDR1X4`：来源为 Getting Started 样例 J1，完成本地包提取、
  14.3 打开/保存/重开、四 pin `ReportNetlist` 以及带探针的 DC/AC/TRAN；证据保存在
  仓库外的 `multisim-evidence/connector-scan/hdr1x4-native-acceptance.json`。
- 回归结果：连接器相关聚焦测试已扩展为 `39 passed`；本轮全量回归为 `924 passed,
  45 skipped, 154 subtests passed`。
- 下一项：继续对 14.2/其他版本建立独立 connector manifest 和实机矩阵，并扩展更多
  已明确验收的连接器规格。不能把 `HDR1X4` 的结论外推到其他连接器型号。
- 已完成接口准备：兼容层新增 `resolve_connector_mapping`，只接受目标版本 manifest 中
  同时匹配 part 标识和完整 pin signature 的条目；没有独立条目时返回 `unavailable`，
  不会把通用 `XSUB2` 载体冒充真实连接器。14.3 的 `HDR1X4` 条目已在闭环验收后标记
  `verified=true`。

## 2026-10-07 多板 HDR1X4 原生物化闭环

- 已完成：`materialize_circuit_design_partition` 接受目标 Multisim 版本并调用精确的
  `resolve_connector_mapping`。只有 manifest 中 `verified=true` 且 pin signature 完整
  匹配的连接器，才会按显式 `instances` 在每块板加入真实的 `CircuitComponent`；未映射
  型号保持 `mapping-pending`，不会退回到 `XSUB2` 或自动猜测。
- 已完成：SPICE 适配器输出 `XJ<n> net1 net2 net3 net4 HDR1X4`，原生构建器随后从本机
  授权模板包写入四个 `CiPort`。原生验收执行器在映射未验证时主动拒绝执行，避免生成
  没有物理连接器的“看似多板工程”。旧的无 `connectors` 请求仍可生成逻辑预览，但
  `unverified-inferred` 不会打开原生执行门。
- 已完成：重开后的 `ReportNetlist` 不再只检查连接器/网络名称是否出现；对每个
  `HDR1X4` 实例逐 pin 校验 `P1`–`P4` 到源网的对应关系。缺针、错序或错网会把板级
  `reopened_topology` 置为 `fail`/`unverified`，从而阻断 `native-verified` 结果。
- 实机证据：Multisim 14.3 双板四引脚样例已完成两块板分别生成、打开、保存、重开、
  `ReportNetlist` 元件/网络回读，以及 DC、TRAN、AC 三种原生分析；跨板接口序列和
  完整电路参考比较均通过，三次结果都是 `accepted/native-verified`。证据保存在源码
  仓库外的 `C:\path\to\multisim-evidence\multiboard-hdr1x4-20261007-*`，
  不提交 NI 模板、样例工程或解码 XML。
- 回归测试新增连接器物化、映射待定和旧推断连接器门禁；后续仍需在 14.2 及其他目标
  版本建立独立 manifest 和实机验收，不能把 14.3 的 `HDR1X4` 结论外推到其他连接器。
- 版本失败关闭：目标版本没有本地 manifest 时，预览返回 `unavailable` 映射并保持
  `mapping-pending`，不会回退到通用载体；只有补充该版本独立清单和实机证据后才能打开
  原生执行。

## 2026-10-09 双板复杂数字回归

- 已完成：新增 `split_logic_load` 双板数字案例。`logic` 板产生 `clk`/`data`，两板通过
  已验收的 `HDR1X4` 连接器共享 `clk`、`data`、`high` 和 `0`；`io` 板通过显式电压夹具
  复现跨板输入，经过 NOT/AND 级联并保留 `RLOAD=1 kΩ` 远端负载。这样每块板都能独立
  打开、保存、重开和仿真，同时仍检查跨板接口与完整设计参考。
- 新增 `multiboard_digital_regression.py`、`run_multiboard_digital_regression.py` 和
  `MULTIBOARD_DIGITAL_REGRESSION.md`。运行器支持 COM-free 预览和 DC/TRAN/AC 原生
  验收，预览始终标记 `logical-only/unverified`。
- 实机证据：Windows 11、Multisim 14.3、32 位 Python 3.12、本地授权模板包上，DC、
  TRAN、AC 均为 `accepted/native-verified`；两板生成/重开、ReportNetlist、逐针连接、
  布局、跨板接口、完整参考和远端负载均通过。证据保存在仓库外的
  `C:\path\to\multisim-evidence\multiboard-digital-20261009-*`。
- 当前边界：该结论只覆盖 `HDR1X4`、本案例元件族和 Multisim 14.3；其他连接器、目标版本
  和更复杂的多板拓扑仍需独立 manifest 与实机证据，不能由本案例自动外推。
- 回归结果：全量 `931 passed, 45 skipped, 154 subtests passed`；新增定向测试 11 项。

## 2026-10-09 双板混合门级回归与探针交叉修复

- 已完成：矩阵新增 `split_mixed_logic`，在相同的 HDR1X4、边界夹具和远端 1 kΩ 负载合同下
  覆盖 OR/XOR/NOR/XNOR 四类门模型。它与 `split_logic_load` 共用物理边界，便于把门模型
  差异和连接器/夹具差异分开验证。
- 实机证据：Windows 11、Multisim 14.3、本地授权模板包上，`split_mixed_logic` 的 DC、
  TRAN、AC 均通过生成、保存重开、ReportNetlist、逐针连接、布局、接口序列和完整参考
  比较，结果为 `accepted/native-verified`。证据保存在
  `C:\path\to\multisim-evidence\multiboard-digital-20261009-mixed-v2`。
- 期间发现：混合门布局中 `clk` 探针候选点恰好落在 `data` 网络的线段交点；Multisim 重开
  后会丢弃该探针，导致完整参考比较出现 `clk` 未验证。探针选点现在会枚举候选线段并排除
  其他网络占用的点，随后重新完成三种原生分析。
- 回归结果：全量 `935 passed, 45 skipped, 154 subtests passed`；新增探针交叉门禁和混合门
  双板案例测试均通过。

## 2026-10-10 开源边界与模型来源加固

- 新研发范围遵守根目录 `AGENTS.md` 和 `OPEN_SOURCE_SCOPE.md`：开源推进复杂电路生成、
  映射、原生正确性、仿真、报告与基本多板联调；商业版重点做安装和使用体验，后续专有
  多板联调优化、跨板优化与高级布局/排线优化独立维护。既有公开实现保留原许可、API 和贡献者归属。
- 公开 Git 候选与实际 wheel/sdist 共用路径门禁，拒绝商业目录、商品归档和许可未确认的
  本地资产；强制暂存也不能绕过。Git 使用 NUL 分隔原始路径，中文文件名不会因转义漏检。
  CI、PyPI、npm 与 MCP Registry 工作流均接入发布前审计；该门禁不能识别改名后的私有代码，
  文件来源仍须审查。未读取或导入其他商业工作区及商品包。
- 模型来源字段统一校验，拒绝控制字符、错误类型及超长元数据，保留合法旧审批摘要的
  规范化方式。许可证占位声明在审批、SPICE 来源审计与 Agent 诊断中保持证据缺口。
  许可证声明不授予模型再分发权；明确的本地使用声明仍可用于本机验证。
- 外部模型仅在指定根目录内有界读取并重哈希；拒绝目录穿越、跨平台 Windows 盘符、
  流/设备别名与根外符号链接。报告大小对应实际哈希字节，结果不含模型正文或根目录绝对路径。
- 最终验证：32 位 Python 3.12 全量运行 `995` 项测试，`950` 项通过、`45` 项跳过；
  `64` 项定向测试在 32 位 Python 3.12 和 64 位 Python 3.13 下均通过。
  64 位 Python 构建的 wheel/sdist 包成员门禁通过，npm dry-run 仅包含四个预期公开文件；
  Harness 契约检查、插件本地发布检查及四份工作流 YAML 解析均通过。
  发布审计为 `0` 错误，唯一警告为未提交修改；`git diff --check` 通过。
- 本轮没有新增 Multisim 原生实机验收证据，不将上述软件回归解释为任意复杂电路已通过。
  改动仍在本地，未提交、未推送、未发布；本地构建沿用当前版本元数据，仅供验证，
  不得覆盖已经发布的 `1.3.0rc3`。路线图中的完整用户模型库管理仍未宣称完成。
- 下一项开源工作：完善单板导入/生成/原生仿真/报告的一致性回归，优先核验模型缺失、
  元件值回读和明确的失败原因；随后扩展基本多板逐板生成、接口回读与仿真汇总回归。
  保留源码仓库外的工程及原生实验凭据，用公开基准支持技术定位。
- 用户确认基本多板联调仍在开源继续开发；本次范围修正仅更新研发文档，没有改变代码、
  API 或授权，也未新增实验验收结果。商业工作区、账号和商品继续由另一任务管理。

## 2026-10-11 统一工程能力发现

- 新增共享 `engineering_capabilities.py`：在原有 `api_contract` 下分别列出八类旧目录标签、
  十二个受限工作流、实际审批编译矩阵、精确版本清单及公开文档案例。目录标签不再用于
  推断整类成熟度；工作流实现、profile 可发现工具与目标版本案例分别说明。
- MCP `runtime_status(target_version=...)`、只读 CLI `capabilities` 和本机
  `/api/capabilities?target_version=...` 共用契约；`schematic_component_catalog` 使用相同
  版本映射并给每个载体增加 `versioned_mapping_status`。已有无参数调用、契约版本与
  115 个工具保持兼容，没有增加独立服务或执行入口。
- 当前仅有 14.3 manifest；未选择版本不默认选 14.3，其他版本映射为空且证据未验证。
  历史记录带文档、分析和案例边界，标明未读取原始实验凭据、未在本机重新验收。
  相关底层实验不冒充完整自然语言入口；正向依赖检查也不冒充 COM 激活或工程通过。
- 安装包实测发现 Windows ANSI/UTF-8 管道会破坏中文家族名称；新的 CLI 查询采用
  ASCII Unicode 转义。在隔离安装的 64 位 wheel 上复核中文名称与源码契约一致，
  14.3 清单可读、14.2 不借用邻近版本，CI 增加安装后查询检查。
- 最终验证：32 位 Python 3.12 全量运行 `1010` 项，`965` 项通过、`45` 项跳过；
  64 位 Python 3.13 的 `54` 项接口/能力定向测试全部通过。wheel/sdist 构建及公开资产
  门禁通过，发布审计 `0` 错误、`1` 项未提交警告；CI YAML 与 `git diff --check` 通过。
  日志、隔离安装检查与包 SHA-256 保存在本机 `release/engineering-capabilities-20261011/`。
- 本轮没有新增 Multisim 电路实机验收或外部模型实验，基本多板能力仍在开源范围。
  本地验证包保留现有 `1.3.0rc3` 元数据，不得覆盖已发布版本；代码仍未提交、推送或发布。
- 下一步：统一无源/运放方案到同一个 `CircuitDesign` 编译路径，先验证模块端口、供电、
  单位、模型和网络合同，再执行原生回读与实验；之后完成固定双板传感器示范。
  当前统一审批编译矩阵仍只有 `signal-passive`，本轮没有宣称该编译扩展已完成。
  使用说明见 [`ENGINEERING_CAPABILITIES.md`](ENGINEERING_CAPABILITIES.md)。


## 2026-10-11 有源调理统一编译（当时记录）

- `signal-active` 现在要求显式 `ideal-opamp5`，实现输入 RC、有限增益缓冲、正负偏置、
  放大/衰减和理想输出负载。原有真实器件候选及审批接口保留；真实运放来源不明、
  任意外部模型替代或理想族声称物理型号/额定值会被阻断。
- 滤波电容新增 `cr-06` 独立额定值审批，避免只批准运放却未审查其电容。模型合同包含
  固定增益、命名引脚、MIT 来源、SHA-256 和理想限制，并绑定到元件审批、支持矩阵和 IR。
  模块端口和供电逐项绑定逻辑草案；所有组件使用显式 SI 数值。
- SPICE 结构往返升级为核对身份、引脚顺序、值、模型及参数；返回的嵌套支持矩阵采用
  深拷贝，避免调用者污染全局合同。已有无源预览摘要及其审批流程保持兼容。
- 自查复现并修复零偏置的小数舍入：`0.1…0.3 V → 0.3…0.9 V` 不再因浮点残差
  产生超大偏置电阻。十进制端点乘积先求偏置分子，真实小偏置仍按组件边界拒绝或接受。
- 最终全量 32 位 Python 3.12.10：`1032` 项运行，`987` 项通过、`45` 项跳过；
  64 位 Python 3.13.14 的 `91` 项定向测试全部通过。新增 21 项有源回归及 1 项 HTTP
  回归，MCP/HTTP/能力发现共用支持矩阵；后端没有启动新的 Multisim 实验。
- 独立软件电气记录覆盖 10 组参数、50 个 OP 点和 50 个 AC 点，源码 32/64 位及隔离
  安装的 64 位 wheel 均通过。最大 OP 绝对误差约 `2.99e-11 V`，AC 幅度相对误差约
  `7.31e-12`，相位误差约 `9.58e-13 rad`。这些是理想模型的数值一致性，不是硬件精度。
  本地证据：`release/active-signal-compiler-20261011/`（测试日志、独立求解输入/输出、
  编译/SPICE/审批摘要、隔离安装结果、wheel/sdist、公开资产审计和 SHA-256 manifest）。
- wheel/sdist 及公开资产门禁通过；发布审计 0 错误、1 项未提交警告；代码和证据只在
  本地，没有提交、推送或发布。验证包仍带 `1.3.0rc3` 元数据，不得覆盖已公开 rc3。
- 新模板 `native_validation_status=unverified-for-this-template`。它采用 Multisim MCP
  原生 OPAMP5 载体方言，不提供第三方 SPICE 可直接运行的模型正文；无饱和、带宽、
  电流限制或真实 ADC 采样模型，不将历史专用运放验收移作本轮证据。
- 下一步：先实现显式外部源/源阻抗/负载及分析夹具的独立批准和组合摘要，防止在已批
  网表追加激励后仍使用旧摘要；随后对新模板执行精确版本门控的原生成图、保存重开、
  网络/引脚/值/模型回读、OP/AC、报告和产物完整性。通过后再做固定双板传感器示范。
  专有跨板优化及高级排线仍保持在商业开发范围之外。

## 2026-10-11：激励合同与有源原生闭环

- `compile_executable_netlist` 新增可选 `experiment_fixture`（仅有源、14.3、DC/AC）：
  显式源电压、源阻抗与已包含负载；新增源前保留完整基础预览。组合摘要绑定夹具、
  原网表、别名、完整 IR 与 SPICE。审阅需要 `confirm_fixture` 和匹配摘要；重建校验
  阻断源/引脚/数值/别名/基础谱系篡改。旧无夹具无源黄金摘要保持不变。
- 既有原生模拟入口接受成对的预览/网表审批，并严格绑定 proposal 的原 SPICE。
  精确版本、全部引脚、相对容差阻容回读与源/保存工程的理想 VCVS 表达式均校验。
  禁止以覆盖源工程、参数或模型的方式绕过原合同。分析与检查由原生 proposal 的明确
  execute 意图授权，记录不含独立 simulation-plan 审批，不能混称两条执行路径。
- 经验：纯 DC 源在结构化解析中规范化为 value，DC+AC 为 model 表达式；组合器现按
  两种类型生成，避免源语义正确但摘要往返失败。小电容回读不能使用固定 `1e-12 F`
  绝对容差。原生探针名/载体模型、命名引脚与实际保存工程共同决定有效仿真。
- 六份实机工程通过（四组模板＋默认输入上下端点），其中零源阻抗使用 64 位 Python
  主进程及 32 位 worker。12 个 OP 通道值、372 个 AC 复数通道样本；最大绝对差
  OP `4.801e-9 V`、AC `3.613e-9 V`。原始记录和全部文件 SHA-256 保留在
  `release/active-fixture-native-20261011/`；第一次缺少模板环境的失败保留，未仿真。
  本地 NI 提取包只用于本机授权实验，不进入公开包。
- 最终 32 位全量：1045 运行、1000 通过、45 跳过；64 位定向：93 运行、91 通过、
  2 跳过；隔离安装 64 位 wheel 复核完整产物及凭证成功。wheel/sdist 门禁通过；
  发布审计 0 错误、1 未提交警告；未提交/推送/发布，rc3 元数据包仅验证。
- 能力发现新增明确的夹具工作流/案例；基础未加激励预览不冒充原生运行证据。
  图面通过现有几何门禁，但存在 40 个异网交叉等可读性问题，不能以引脚正确替代
  图面合格声明。详见 `docs/ACTIVE_SIGNAL_NATIVE_ACCEPTANCE_20261011.md`。
- 下一步：固定双板传感器采集示范，明确板归属、HDR1X4 端口、已审批激励与共享地，
  逐板生成/回读/仿真，核对跨板接口与完整电路参考，再给出汇总报告。
  本分支不引入高级排线、跨板联合优化、商业安装资产或私有模型。
