# 当前开发落点

以代码、原生实验和本文为恢复依据，不以历史回复中的完成声明代替验收。

- 当前任务：第二阶段的 2N3904 共射原生闭环。
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
  `C:\Users\18331\Documents\multisim-evidence\digital-counter4-gated-20261003`。
- 已完成：在版本准入生效后重新运行完整五案例数字矩阵；`logic_chain_load`、`dff_load`、
  `counter4_load`、`shift4_load`、`counter4_decode_load` 全部通过，5/5 案例引脚证据
  完整，141 个命名引脚连接通过，0 个失败、0 个拓扑不匹配。证据保存在本机
  `C:\Users\18331\Documents\multisim-evidence\digital-matrix-20261003-gated`。
- 已完成混合信号阶段第一组原生基线：新增 `hybrid_regression` 与
  `tools/run_hybrid_regression.py`，`not_rc_load`、`logic_chain_rc_load`、
  `counter_q0_rc_load` 和 `shift_s0_rc_load` 四个案例均在 Multisim 14.3 实机通过，
  `diode_rc_shaper`（本地授权 `1N4001GP` 模型）也已通过。
  工具先生成带探针的 `.ms14`，再由 Multisim 重开保存副本并直接执行 COM 瞬态；四个工程
  的原生拓扑、器件完整性、命名引脚连接、数字 0/5 V 摆幅和模拟 RC 动态响应均通过；
  回归器把完整引脚证据纳入最终通过条件，并为二极管增加 A/K 到源网络的显式契约。
  正式矩阵证据保存在
  `C:\Users\18331\Documents\multisim-evidence\hybrid-native-matrix-20261003-v7`；
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
  `C:\Users\18331\Documents\multisim-evidence\hybrid-native-matrix-20261004-v10`。
- 已完成对称的 `adc_rc_bridge` 原生闭环。ADC 端的模拟输入和电源依赖同样通过 1 GΩ
  高值锚点物化；Multisim 14.3 重开、拓扑、引脚、布局和 COM 瞬态均通过，输出 `digital`
  为 0–5 V，`filt` 具有 0–0.3768 V 的 RC 响应。ADC/DAC 的单比特桥接现均有独立原生
  证据；后续仍需更高位宽 ADC/DAC 和真实器件模型验证。
- 已完成 `ADC4`/`DAC4` 四位便携桥的 Multisim 14.3 原生探针：ADC4 四路输出和各自
  RC 负载均通过 0–5 V 摆幅、重开拓扑、引脚和 COM 瞬态；DAC4 原生输出出现 16 级
  组合电平（0、0.333、0.667 … 5 V）。独立证据保存在本机
  `C:\Users\18331\Documents\multisim-evidence\hybrid-multibit-probe-20261004`，
  正式混合信号回归矩阵已扩展并通过 12/12。
- 已完成 `adc4_dac4_transfer` 原生闭环：在 Multisim 14.3 重开工程后执行 68 点三角
  输入扫描，阈值附近排除 2 点后仍覆盖 0–15 全部 16 个编码；ADC 编码、数字电平、
  DAC 加权重构和最终 RC 输出均通过。证据保存在本机
  `C:\Users\18331\Documents\multisim-evidence\hybrid-native-matrix-20261005-v13`，
  当前正式混合信号矩阵为 13/13。
- H 源（CCVS）已用闭合控制支路重新验证，排除了此前“理想源控制回路未闭合”造成的
  假阴性：命令引擎能得到 `raw` 的 0–5 V 和 `filt` 的 RC 响应，但 Multisim 14.3
  原生工程重开后的 COM 输出仍为全零。其连接表和引脚拓扑正确，但原生行为未通过，
  因此 H 源继续标记为 `native unsupported/unverified`，不计入 13/13 正式矩阵。有效
  失败证据保存在本机 `C:\Users\18331\Documents\multisim-evidence\hybrid-ccvs-rc-20261004-v4`。
- Multisim 后端能力现在显式记录官方 Automation API 的来源、`MultisimInterface.MultisimApp`
  ProgID、独立 32 位 COM worker、版本探测范围，以及官方接口没有元件放置和导线绘制方法。
  未来接入新的 NI API 时沿用同一 EDA 后端契约，不改变源网表、拓扑门禁和实验报告格式。
- 不再采用“最终生成后替换标签就宣布工程正确”的流程；必须让 Multisim 打开、保存、
  回读真实器件属性并导出实际图像，然后核验最终产物的 manifest。
- RLC 入口另外拒绝电压源载体遗留的 `10Vpk/5kHz` 示例标签，并重新解码每个候选的
  `analysis-002.ms14` 检查 Multisim 保存后的实际标签；图面源参数检查与原生 OP/AC
  验收分开留档，避免把“仿真通过”误报成“工程图可交付”。
