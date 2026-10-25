# 单板布局与原生验收记录（2026-10-18）

本记录把本机原生 Multisim 结果与布局优化结果分开保存。环境为 Windows 11、32 位
Python 3.12.10 COM worker、NI Multisim 14.3、`ewd/ewe` 编解码器和本机授权模板包。
模板包没有进入公开仓库。

## 原生模拟基线

使用 `python -m multisim_mcp.native_analog_acceptance --output NEW_DIRECTORY` 完成生成、
保存、重开和原生 OP/AC/TRAN：

- 10 V 分压器输出 `4.9999999975 V`，负载电流 `4.9999999975 mA`；
- RC 候选 `1 kΩ/1.5 kΩ/1.6 kΩ/1.8 kΩ` 全部通过 DC、AC 复数响应和瞬态响应检查；
- 选中的 `1.6 kΩ` 截止频率 `994.577484 Hz`，相对 1 kHz 误差 `0.5423%`；
- 161 个 AC 点、70 个瞬态点，最大瞬态误差约 `0.433 mV`。

这些结果证明的是声明模板和 Multisim 14.3 的原生基线，不是任意元件或任意版本的保证。

## 数字输出带电阻负载

测试网表为 `VDD + PULSE 输入 + A1/DNOT4 + RLOAD(1 kΩ)`。`create_schematic_from_netlist`
使用 `verify=true` 打开并重读工程后得到：

- `A1`、`RLOAD`、`VDD`、`VIN` 均存在；
- ReportNetlist 元件和网络拓扑通过；
- 布局预检通过，异网交叉率为 `0.167`；
- `run_circuit_experiment` 的 Multisim 瞬态返回 190 个点；
- `V(sig)` 在 `0–5 V` 之间翻转，数字观测状态为 `complete`；
- `I(b__a1)` 在高电平负载期间约为 `-5 mA`。

这说明当前精确的 `A1 + RLOAD` 夹具可以在本机保存、重开和仿真。模型来源和许可证仍需
单独登记，不能扩展为所有数字器件的通用结论。

## 布局候选交接结果

`apply_optimized_layout_candidate` 已完成以下保护链：

1. 接收 `validate_layout_positions(optimize=true)` 的选中坐标；
2. 复用现有 `.ms14` 写入器；
3. 解码回读所有应用坐标；
4. 检查回退导线和元件重叠；
5. 只有全部通过才返回 `state=applied`。

在带 `OutProbe` 的分压工程中，移动元件会使旧工程重布线器产生回退路径；即使坐标回读
一致，Multisim 重开后的输出通道也可能消失。当前实现返回 `state=rejected-geometry`，
不会交付该副本。地符号 `0/GND` 已默认固定，`probe_nets` 也已传入优化干跑。

随后启用 `validate_layout_positions(..., optimize=true, materialize=true,
output_ms14=...)`，对同一分压探针工程按选中坐标重新生成了全新 `.ms14`：

- 选中候选 `R2:left:1`；
- 重新生成布局异网交叉率为 `0.0`，无回退导线；
- Multisim 14.3 打开、保存副本、重开均成功；
- `V(OutProbe)` 和 `I(OutProbe)` 在重开后仍可枚举；
- 原生 DC 采样 `V(OutProbe)=4.9999999975 V`。

因此，当前安全交接路径已经完成：旧工程原地修改遇到回退时关闭，选中候选则按网表重新
生成并交给原生验收。该结果仍只覆盖本机 Multisim 14.3 和声明模板。

## 证据范围

本记录证明的是本机 Multisim 14.3、声明模板、有限模拟/数字夹具和当前回归矩阵。它不证明
跨版本、任意数字模型、PCB 制造规则、硬件寄生、全局最优布线或所有带探针旧工程都能被
原地修改后保持原生输出。
