# 混合数字/模拟双板原生验收

本记录增加一个受限的开源多板案例：数字反相器板通过已验证的 `HDR1X4` 连接器，把
数字信号、供电和双地连接到 RC 负载板。它验证的是基本跨板联调合同，不是任意数字/模拟
多板设计、联合优化或 PCB 布线质量。

## 结构与边界

- `logic` 板：5 V 源、脉冲输入和 `DNOT4` 反相器，输出 `sig`；
- `analog` 板：1 GΩ 供电锚点、1 kΩ/1 µF/100 kΩ RC 网络；
- `JLINK/HDR1X4`：P1=`sig`、P2=`high`、P3/P4=`0`；连接器映射只使用
  Multisim 14.3 精确清单；
- 接收板实验明确加入数字源和供电夹具。夹具是实验边界条件，不计入物理板 BOM；
- 瞬态分析明确使用 `series_alignment=linear`，因为 Multisim 各板的自适应采样轴可能不同。
  严格轴模式仍会失败关闭，不会静默插值。

新增 `explicit-digital-fixture` 夹具范围，仅允许 `V`、R/C/L 以及受限的 `DNOT4`/`BV`
数字适配器，要求显式端口和来源 digest；它不能被当作通用数字仿真器或自动推断边界。

## 原生结果

2026-10-16，Windows 11、Python 32 位 worker、Multisim 14.3、本机授权模板包：

| 项目 | 结果 |
| --- | --- |
| 逐板生成/保存/重开 | 通过 |
| HDR1X4、拓扑、布局、探针 | 通过 |
| 两板瞬态分析 | 通过 |
| `sig` 跨板序列比较 | 通过，最大差异 0 V |
| `high` 跨板序列比较 | 通过，最大差异 0 V |
| 两块板 `J1` 的 P1–P4 引脚回读 | 8/8 命名引脚通过，0 不匹配 |
| RC `filt` 与完整参考比较 | 通过，最大差异 0 V |
| 最终状态 | `accepted/native-verified` |

完整本机证据位于 `release/hybrid-multiboard-native-20261016-v6`，不进入开源发布包。
接收端数字夹具的真实来源摘要为
`37c69506d7ef715a552356352c31168f2e593ed133a5198b8f39471ff77872af`，并写入最终工程
的 fixture artifacts。
此前的失败目录也保留在本机：v1 缺少接收端激励夹具，v2 的数字夹具仍使用独立
电压源且无法复现 XSPICE 传播延迟；两次均正确返回 `native-failed`，没有放宽验收门槛。

可重复入口为：

```powershell
$env:PYTHONPATH = 'mcp_server'
$env:MULTISIM_MCP_TEMPLATE_DIR = 'YOUR_LICENSED_LOCAL_PACK'
python tools/run_hybrid_multiboard_regression.py `
  --output NEW_DIRECTORY --execute
```

省略 `--execute` 时只输出逻辑合同和夹具 digest，不启动 COM；目标版本不匹配会在
执行前失败关闭。

需要批量准备跨版本验收清单时，可先列出全部已登记案例；该命令同样不启动 COM：

```powershell
$env:PYTHONPATH = 'mcp_server'
python tools/run_hybrid_multiboard_regression.py --list
```

离线故障矩阵位于 `release/hybrid-multiboard-fault-matrix-20261016.json`，7/7 篡改均被
拒绝：夹具 digest、连接器引脚、探针映射、重开拓扑、采样轴、完整参考和归档文件哈希。
复核器不启动 COM，也不把归档一致性通过解释为当前机器重新仿真。

在连续响应合同抽象后，原始 `dnot4_rc_split` 案例也完成独立复验：普通 `DNOT4` 数字源
板通过 `HDR1X4` 驱动 RC 负载板，`filt` 与完整参考的初值、终值、峰值、谷值、响应范围
和自适应时间轴特征全部匹配。Multisim 14.3 归档为
`release/hybrid-multiboard-native-20261016-dnot4-rc-r1`，离线复核检查 42 个文件、
1,090 个请求电压采样值；`filt` 最终值为 `4.6656276 V`、响应范围为 `0.2848674 V`。
对应故障矩阵为
`release/hybrid-multiboard-fault-matrix-20261016-dnot4-rc-r1.json`，8/8 篡改拒绝。
这证明合同可复用于普通数字源→RC 负载边界，不仅适用于 ADC/DAC；仍不代表硬件容差或
制造级动态性能。

## VCVS/VCCS 受控源双板边界

连续响应合同随后扩展到两个受控源案例：

- `vcvs_rc_split`：源板保留 VCVS，跨 `HDR1X4` 传递 `raw`，接收板使用显式等效脉冲夹具
  驱动 RC 网络；原生归档 `release/hybrid-multiboard-native-20261016-vcvs-rc-r1`，
  42 个文件、522 个请求电压采样值，`filt` 最终值 `0.3619026 V`，响应范围 `0.3768221 V`，
  故障矩阵 8/8 拒绝。
- `vccs_rc_split`：VCCS 输出电压随负载变化，因此跨板边界选择受控信号 `ctrl`，接收板
  保留真实 VCCS 和 RC 网络，并使用显式控制脉冲夹具；不把无负载的电流源输出电压冒充
  为独立接口信号。原生归档 `release/hybrid-multiboard-native-20261016-vccs-rc-r5`，
  42 个文件、392 个请求电压采样值，`filt` 最终值 `0.3996102 V`，响应范围 `0.3997701 V`，
  故障矩阵 8/8 拒绝。

两个案例均通过生成、保存重开、拓扑、布局、连接器、跨板接口、完整参考和连续响应门禁。
受控源合同只覆盖声明的 Multisim 14.3 载体和边界夹具；不代表任意受控源宏模型、负载
变化下的通用输出阻抗或硬件级性能。

## 受控源参数角落

在上述拓扑上增加了四个有界角落：VCVS 增益 `0.5`、`2`，VCCS 跨导 `0.5 mS`、`2 mS`。
每个角落都重新生成并重开两板工程，按相同接口、完整参考和 RC 连续响应合同验收，四个
角落均为 `accepted/native-verified`；每个归档的故障矩阵均为 8/8 拒绝。VCVS 等效接收
夹具的脉冲高电平随声明增益同步生成，VCCS 继续使用 `ctrl` 控制信号跨板边界。

这些角落证明的是参数化工作流和已声明模型的拆分一致性，不是对任意增益、跨导范围或
器件容差分布的连续保证。

随后增加负载变化角落：VCVS 的串联电阻 `500 Ω`、滤波电容 `2 µF`，以及 VCCS 的串联
电阻 `2 kΩ`、并联负载 `50 kΩ`。四个 Multisim 14.3 原生角落均通过同一组接口、完整参考
和连续响应门禁，各自故障矩阵 8/8 拒绝。它们验证的是有限离散负载变化下的工程闭环，
不等同于连续容差扫描或任意负载稳定性证明。

随后将参数角落与负载角落组合为两个更严格的回归：

- `vcvs_rc_split_combo_low` 同时使用增益 `0.5`、串联电阻 `500 Ω` 和滤波电容 `2 µF`；
  原生归档为 `release/hybrid-multiboard-native-20261016-vcvs-combo-low-r1`；
- `vccs_rc_split_combo_high` 同时使用跨导 `2 mS`、串联电阻 `2 kΩ` 和并联负载 `50 kΩ`；
  原生归档为 `release/hybrid-multiboard-native-20261016-vccs-combo-high-r1`。

两个组合角落均在 Multisim 14.3 完成生成、保存重开、连接器引脚、拓扑、布局、跨板接口、
完整参考和连续响应验收，最终状态均为 `accepted/native-verified`；对应故障矩阵分别为
`release/hybrid-multiboard-fault-matrix-20261016-vcvs-combo-low.json` 和
`release/hybrid-multiboard-fault-matrix-20261016-vccs-combo-high.json`，均为 8/8 篡改拒绝。
组合结果只覆盖这两个离散参数点以及已声明的模型、夹具和负载，不能外推到连续参数空间、
任意负载或制造级性能。

## ADC/DAC 双板基线

在同一版本和模板包上又完成了一个独立的 `adc1_dac1_split` 案例：`adc` 板使用显式
`BV` 阈值模型把 5 V 模拟输入转换为 `digital`，`dac` 板使用显式 `BV` 数模模型驱动
1 kΩ/1 µF/100 kΩ RC 负载。接收板的 `digital` 由带真实 SHA-256 来源摘要的 BV 数字夹具
复现，`high` 由独立的 5 V 电源夹具提供；这些边界条件不会被混入物理板 BOM。

输入固定为 `DC 5`，目的是先取得稳定的单比特阈值基线。带跳变输入的逐点比较会受到
Multisim 各独立工程自适应时间轴的影响，后续单独建立事件/边沿合同，不通过放宽电压
容差掩盖采样轴差异。

已增加 `DigitalEdgeComparisonContract` 和
`tools/compare_digital_edge_series.py`。在此前 `r3` 跳变失败归档上，ADC 与 DAC 的整条
波形逐点比较最大差异为约 `1.137 V`，而事件层在不重采样波形的情况下识别出 5 个同向
边沿，最大阈值穿越时刻差为 `1.064e-10 s`。该层只证明初始/终止逻辑电平、边沿方向、
数量和阈值时刻，不能替代模拟波形、传播延迟或器件动态性能验收。

复核命令示例：

```powershell
$env:PYTHONPATH = 'mcp_server'
python tools/compare_digital_edge_series.py `
  release/hybrid-multiboard-native-20261016-adc1-dac1-r3/adc/native-tran-series.json `
  release/hybrid-multiboard-native-20261016-adc1-dac1-r3/dac/native-tran-series.json `
  --expected-output 'V(OutProbe)' --actual-output 'V(OutProbe)' `
  --edge-time-tolerance 1e-9
```

2026-10-16 的原生结果：两板生成、保存重开、HDR1X4 P1–P4（8/8）、拓扑、布局、探针、
瞬态、`digital`/`high` 跨板序列、RC 输出和完整参考全部通过，最终状态为
`accepted/native-verified`。证据目录为
`release/hybrid-multiboard-native-20261016-adc1-dac1-r5`，离线复核检查 42 个文件、
348 个电压采样值；对应故障矩阵为
`release/hybrid-multiboard-fault-matrix-20261016-adc1-dac1.json`，7/7 篡改拒绝。

随后完成 `adc4_dac4_split` 四位扩展：四位数据使用 `JDATA/HDR1X4`，供电与三路地使用
`JPOWER/HDR1X4`，两个连接器都按 14.3 清单逐针回读。ADC4/DAC4 的长行为表达式保留在
结构化 `CircuitComponent` 中（字段仍限制为 4 KiB），接收端以四路 BV 输出组成显式数字
夹具。稳定 `DC 5` 输入下，8 个跨板数据/电源引脚在两块板上均通过，逐板保存重开、
瞬态、完整参考、布局和故障矩阵全部通过；原生证据目录为
`release/hybrid-multiboard-native-20261016-adc4-dac4-r1`，离线复核 42 个文件、754 个
采样值，7/7 篡改拒绝。该结果验证的是已声明行为模型和两个四针连接器的多板合同，不是
真实四位 ADC/DAC 芯片精度或动态性能认证。

## ADC/DAC 跳变原生验收

新增 `adc1_dac1_edge_split`，将模拟输入和接收端数字夹具改为
`PULSE(0 5 0 1n 1n 20u 40u)`。模拟/供电网络仍要求完整采样逐点通过；声明为数字的
`digital` 接口和完整参考使用 `DigitalEdgeComparisonContract`，要求初末逻辑电平、5 个
边沿的方向/数量和阈值穿越时刻全部匹配。严格整波形结果仍保留在
`series_interface_comparison` 中，不能被事件结果覆盖。

原生状态为 `accepted/native-verified`，证据目录为
`release/hybrid-multiboard-native-20261016-adc1-dac1-edge-r4`；离线复核检查 42 个文件、
879 个采样值。动态故障矩阵为
`release/hybrid-multiboard-fault-matrix-20261016-adc1-dac1-edge.json`，8/8 篡改拒绝，
新增第 8 类数字边沿证据篡改。

## ADC4/DAC4 跳变扩展

新增 `adc4_dac4_edge_split`，输入采用 340 µs 周期的三角式 PULSE，四路 `d0..d3` 分别
使用数字边沿事件合同；接收端 `filt` 另外使用连续响应合同。原始 DAC `analog_out` 仍不
计入本案例的事件门禁，避免把转换重构和数字边界混为一个判据。

Multisim 14.3 原生状态为 `accepted/native-verified`：两个 `HDR1X4`、8 个连接器引脚、
逐板保存重开、拓扑、布局、标量接口、四路边沿接口、RC 响应和完整参考均通过。证据目录为
`release/hybrid-multiboard-native-20261016-adc4-dac4-edge-r2`，离线复核检查 42 个文件、
696 个采样值；`filt` 最终值和峰值均为 `0.1465689 V`。故障矩阵为
`release/hybrid-multiboard-fault-matrix-20261016-adc4-dac4-edge-r2.json`，9/9 篡改拒绝。

## ADC4/DAC4 转换与 RC 连续响应闭环

`adc4_dac4_transfer_split` 现在同时声明两类互补合同：

- `ConverterTransferContract` 检查 16 个编码是否全部出现、ADC 阈值编码是否一致，
  以及 DAC 二进制重构误差；
- `ContinuousResponseContract` 单独检查 `filt` 的初值、最终值、峰值、谷值、响应范围，
  以及分析端点和极值时间的自适应时间轴误差。它不对整条波形重采样，缺失、非有限、
  不完整或非严格递增的原生序列失败关闭。

2026-10-16 在 Multisim 14.3、本机授权模板包上重新执行：

| 项目 | 结果 |
| --- | --- |
| ADC/DAC 16 级转换合同 | 通过，16/16 编码，66 个非边界采样点，最大 DAC 误差 `8.8818e-16 V` |
| `filt` RC 连续响应合同 | 通过，68 个原生采样点，最终值 `0.7138213 V`，峰值 `0.7336927 V` |
| 自适应时间轴误差 | 通过，声明上限 `1e-5 s`，本次最大误差 `0 s` |
| 完整原生门禁 | 全部通过，`accepted/native-verified` |

最终归档为 `release/hybrid-multiboard-native-20261016-adc4-dac4-transfer-r3c`；离线
复核检查 42 个文件、878 个电压采样值。对应故障矩阵
`release/hybrid-multiboard-fault-matrix-20261016-adc4-dac4-transfer-r3c.json` 覆盖
夹具摘要、连接器引脚、探针、拓扑、采样轴、完整参考、数字边沿、ADC/DAC 转换、RC
连续响应和归档哈希共 10 类，10/10 篡改被拒绝。

该连续响应证据只证明声明的 BV 行为模型、1 kΩ/1 µF/100 kΩ 网络和 Multisim 14.3
工程在双板拆分后的特征一致性；不代表真实 ADC/DAC 芯片的采样保持、INL/DNL、器件
容差、温漂、负载瞬态或制造级性能。严格整波形比较仍保留为诊断数据，不能被该合同
替代或掩盖。

## 后续范围

下一步是将连续响应合同推广到更多声明的 RC/受控源输出，补充真实负载组合和独立版本实测，
并开始版本化元件映射矩阵。
每个新增模型和 Multisim 版本都必须重新完成模板、引脚、保存重开、采样轴、报告和完整
参考验收；这些结果只覆盖已声明的行为模型、连接器和 Multisim 版本。
