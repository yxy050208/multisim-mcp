# 混合信号回归矩阵 / Mixed-signal regression matrix

混合信号回归验证数字 XSPICE 器件与连续时间 R/C 网络在同一个 Multisim 工程中的
闭环行为。每个案例都要求：生成带探针的 `.ms14` 原理图、Multisim 重开并回读拓扑、
确认原生器件完整、直接对保存后的原生工程执行 COM 瞬态仿真，再从原生分析 CSV 电压列
检查数字节点和模拟节点。

当前矩阵包含八条基线：`not_rc_load` 验证单个 NOT 输出，`logic_chain_rc_load` 验证
NOT → AND → OR 三段组合逻辑输出，`counter_q0_rc_load` 验证四位计数器最低位输出；
`shift_s0_rc_load` 验证四位串入并出移位寄存器最低位输出。前四个案例都经过 1 kΩ/1 µF
网络并带 100 kΩ 负载，观察数字节点和 `V(filt)`；`diode_rc_shaper` 使用本地授权的
`1N4001GP` 原生二极管模型验证脉冲整形，`counter_q0_q1_rc_load` 同时验证计数器
`q0/q1` 两路独立 RC 负载，`vcvs_rc_bridge` 验证电压控制电压源（VCVS）接口，
`vccs_rc_bridge` 验证电压控制电流源（VCCS）接口。
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
原生器件完整性，以及从瞬态 CSV 读取的电压列。没有精确版本清单时，工具会在案例
执行前以 `manifest-unverified` 或 `version-mismatch` 失败关闭。

2026-10-03 的正式原生矩阵已通过 8/8。八个工程均由 Multisim 重开并直接执行 COM
瞬态，数字输出均覆盖 0/5 V，模拟输出均有连续 RC 动态响应；每个案例的命名引脚
连接证据均完整且无不匹配。完整证据保存在本机
`C:\Users\18331\Documents\multisim-evidence\hybrid-native-matrix-20261003-v8`，不提交到开源仓库。

回归器现在把引脚证据完整性作为通过条件；此前二极管案例暴露的“仿真成功但端子未验证”
不会再被汇总为通过。多路 RC 案例现在按每个数字/模拟输出对分别检查摆幅与响应；VCVS
载体的 D/G/S/SUB 端子契约已纳入回读校验；VCCS 案例已通过原生行为门禁。H 源实验在
同类拓扑下出现全零输出，暂不计入矩阵，后续单独定位其载体语义；再增加更复杂
的二极管整形；每一类都必须先单独取得
Multisim 原生证据，不能由这条基线外推。
