# 混合信号回归矩阵 / Mixed-signal regression matrix

混合信号回归验证数字 XSPICE 器件与连续时间 R/C 网络在同一个 Multisim 工程中的
闭环行为。每个案例都要求：生成带探针的 `.ms14` 原理图、Multisim 重开并回读拓扑、
确认原生器件完整、直接对保存后的原生工程执行 COM 瞬态仿真，再从原生分析 CSV 电压列
检查数字节点和模拟节点。

当前矩阵包含十三条基线：`not_rc_load` 验证单个 NOT 输出，`logic_chain_rc_load` 验证
NOT → AND → OR 三段组合逻辑输出，`counter_q0_rc_load` 验证四位计数器最低位输出；
`shift_s0_rc_load` 验证四位串入并出移位寄存器最低位输出。前四个案例都经过 1 kΩ/1 µF
网络并带 100 kΩ 负载，观察数字节点和 `V(filt)`；`diode_rc_shaper` 使用本地授权的
`1N4001GP` 原生二极管模型验证脉冲整形，`counter_q0_q1_rc_load` 同时验证计数器
`q0/q1` 两路独立 RC 负载，`vcvs_rc_bridge` 验证电压控制电压源（VCVS）接口，
`vccs_rc_bridge` 验证电压控制电流源（VCCS）接口，`dac_rc_bridge` 验证单比特 DAC
行为桥接到 RC 负载，`adc_rc_bridge` 验证单比特 ADC 行为桥接到 RC 负载，`adc4_rc_bridge`
验证四位 ADC 的四路输出和独立 RC 负载，`dac4_rc_bridge` 验证四位 DAC 的二进制加权
输出和 RC 负载，`adc4_dac4_transfer` 在三角输入扫描下验证 ADC 的全部 16 个编码、
DAC 重构和最终 RC 输出。
它们用于
确认组合逻辑、时序逻辑到连续时间 RC 负载的数字到模拟边界，不代表任意 ADC/DAC、
不同二极管模型、受控源或功率接口已经得到验证。

本机实测（Multisim 14.3、本地授权模板包）命令：

```powershell
$env:MULTISIM_MCP_TEMPLATE_DIR = "C:\Users\18331\Documents\multisim-evidence\local-pack-20261003"
python tools/run_hybrid_regression.py `
  --target-version "Multisim 14.3" `
  --output C:\Temp\multisim-hybrid-regression
```

`matrix.json` 会记录精确版本、`components-14.3.json` 的 SHA-256、拓扑和布局判定、
原生器件完整性，以及从瞬态 CSV 读取的电压列；工具同时写出 `manifest.json`，绑定
矩阵摘要的 SHA-256。没有精确版本清单时，工具会在案例执行前以 `manifest-unverified`
或 `version-mismatch` 失败关闭。

DAC/ADC 行为桥会为表达式读取的数字、模拟输入和电源轨加入 1 GΩ 高值锚点。Multisim
的原生行为源不会仅凭表达式中的 `V(net)` materialise 一个只有单个实体引脚的节点；
没有锚点时，命令引擎可以正常计算，但原生工程重开后会出现输出通道缺失。锚点只建立
节点身份，使用的电阻值对目标电路的负载影响可忽略，并且会进入拓扑与器件证据。

2026-10-05 的正式原生矩阵已通过 13/13。十三个工程均由
Multisim 重开并直接执行 COM
瞬态，数字输出均覆盖 0/5 V，模拟输出均有连续 RC 动态响应；每个案例的命名引脚
连接证据均完整且无不匹配。完整证据保存在本机
`C:\Users\18331\Documents\multisim-evidence\hybrid-native-matrix-20261005-v13`，不提交到开源仓库。

四位桥接的独立原生探针证据保存在本机
`C:\Users\18331\Documents\multisim-evidence\hybrid-multibit-probe-20261004`；ADC4 四路
数字输出均覆盖 0–5 V，DAC4 输出经过 0、0.333、0.667 … 5 V 的 16 级组合。两个案例
已由正式回归器纳入上方的 13/13 矩阵目录。

`adc4_dac4_transfer` 使用 68 个 Multisim 原生瞬态采样点，排除阈值附近 2 个不确定点后，
仍覆盖 0–15 全部 16 个编码；ADC 位电平、编码一致性和 DAC 重构最大误差均通过，
最大 DAC 误差为 `8.9e-16 V`（数值舍入量级）。该结果验证便携行为桥的转换逻辑，
不等同于任意厂商 ADC/DAC 的精度、采样保持或 INL/DNL 规格。

回归器现在把引脚证据完整性作为通过条件；此前二极管案例暴露的“仿真成功但端子未验证”
不会再被汇总为通过。多路 RC 案例现在按每个数字/模拟输出对分别检查摆幅与响应；VCVS
载体的 D/G/S/SUB 端子契约已纳入回读校验；VCCS 案例已通过原生行为门禁；DAC 案例已
通过高值依赖锚点修复后的原生重开和瞬态门禁；ADC 案例也完成同样的原生闭环；ADC4
和 DAC4 已完成四位原生探针并纳入正式矩阵证据。H 源实验在同类拓扑下出现
全零输出，暂不计入矩阵，后续单独定位其载体语义；下一步扩展更高位宽 ADC/DAC 和真实
器件模型验证；每一类都必须先单独取得
Multisim 原生证据，不能由这条基线外推。
