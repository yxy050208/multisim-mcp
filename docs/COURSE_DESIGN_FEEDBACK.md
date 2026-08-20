# 课程设计真实回归与产品反馈

本文记录一次五路波形发生器课程设计如何反向改进 Multisim MCP。它不是对某个课程
答案的替代，而是可复现的产品验收基准。

## 回归场景

同一套 +10 V 单电源系统需要产生并测量五路 600 Ω 负载输出：

- 20-50 kHz、1 Vpp 方波；
- 经 74LS74 分频得到的 5-10 kHz、1 Vpp 方波；
- 5-10 kHz、3 Vpp 三角波；
- 20-30 kHz、3 Vpp 正弦波；
- 250 kHz、8 Vpp 正弦波。

要求频率和幅度误差不超过 5%，正弦波还需要可量化的失真证据。真实 Multisim 回归
在一个合并实验中对 12 项频率、峰峰值和 THD 判据给出 12/12 PASS。

## 本次暴露的问题

### 1. 验收指标缺少时域频率和 THD

1.0.0 可以验收峰峰值、增益、带宽、上升时间等指标，但周期波形的频率和 THD 需要
调用方读取 CSV 后自行计算。这会导致报告和 `verification.json` 使用不同的证据链。

改进：`frequency` 和 `thd` 现在成为正式 MeasurementRequest 指标，能直接用于
`measure_experiment`、`verify_experiment_requirements` 和
`run_verified_circuit_experiment`。

`frequency` 支持：

- `start_x` / `end_x` 测量窗口；
- `threshold`、`edge` 和 `hysteresis`；
- `min_cycles` 最少周期数；
- 非均匀时步下的线性阈值交点插值。

`thd` 支持：

- 自动从阈值交点估计基波，或显式指定 `fundamental_frequency`；
- `harmonics` 选择 2-50 阶分析；
- 基于时间积分的谐波投影，兼容 SPICE 自适应时步；
- 百分比 THD、各次谐波峰值和分析窗口的结构化证据。

### 2. 模型通过不等于实物通过

课程回归同时使用了便携数字适配器、行为等效积分器、调谐滤波等效网络和有限带宽
运放模型。它能证明频率关系和目标幅度可行，但不能替代厂商宏模型、元件容差和面包板
寄生参数验证。

产品要求：报告必须清楚写出模型层级和剩余验证，不得把“等效模型 PASS”表述成
“实物已验收”。模型来源、许可证和散列将继续纳入 2.0 的 ArtifactSet 设计。

### 3. 规格之间可能存在结构性冲突

两级 74LS74 固定四分频把 20-50 kHz 映射为 5-12.5 kHz，无法在整个输入范围内同时
保证分频输出不超过 10 kHz。MCP 应在生成电路前做约束传播，区分：

- 20-40 kHz 的五路公共联动范围；
- 40-50 kHz 的方波独立扩展；
- 需要新增可切换分频器时的严格全范围方案。

这类冲突检查进入诊断/优化路线图，不应由报告阶段静默掩盖。

### 4. 自动原理图布局可运行但不一定可提交

合并网表可以生成可编辑 `.ms14` 并完成仿真，但复杂电路的自动布局仍可能拥挤、交叉或
裁切。当前产物适合复现仿真，不应默认视为最终教学原理图。

后续布局改进应支持模块分区、稳定坐标、信号流方向、跨页连接符、测试点对齐和导出前
的可读性检查。短期课程报告使用独立系统框图并保留 `.ms14` 作为可编辑证据。

## 回归门禁

以后修改测量或报告模块时，至少应保持：

1. 纯正弦 THD 接近 0%，已知 10% 二次谐波的 THD 接近 10%。
2. 方波和带直流偏置正弦的时域频率均能准确测量。
3. 非单调时间轴、零幅度、周期不足和无效参数返回 `unverified` 或校验错误。
4. 五路合并真实 Multisim 回归的 12 项验收全部 PASS。
5. 报告同时呈现目标、测量值、容差、判定和模型证据边界。
6. 内联 LM324 宏模型的两个实例均展开为可编辑内部器件，反向网表完整且同源实验
   的频率、幅度和 THD 三项验收全部 PASS。

## English summary

The five-output course-design regression exposed missing time-domain frequency
and THD verification, ambiguous model fidelity, linked-range conflicts, and
poor readability for large automatically laid-out schematics. Frequency and
THD are now first-class deterministic metrics. Model provenance, constraint
propagation, and layout readability remain explicit product roadmap items.
Compatible inline vendor subcircuits are now recursively expanded with explicit
complete/partial/carrier-only editable-model coverage.
