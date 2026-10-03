# 混合信号回归矩阵 / Mixed-signal regression matrix

混合信号回归验证数字 XSPICE 器件与连续时间 R/C 网络在同一个 Multisim 工程中的
闭环行为。每个案例都要求：生成原理图、Multisim 重开并回读拓扑、确认原生器件完整、
执行瞬态仿真，再从实际 CSV 电压列检查数字节点和模拟节点。

当前第一条基线是 `not_rc_load`：`NOT` 输出经过 1 kΩ/1 µF 网络并带 100 kΩ 负载，
观察 `V(dout)` 和 `V(filt)`。它用于确认数字到模拟边界，不代表任意 ADC/DAC 或功率
接口已经得到验证。

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

2026-10-03 的实机探测已通过：原生重开成功，`R1`、`C1`、`RLOAD` 和 `A1` 均保留，
瞬态得到 275 个采样点，`V(dout)` 在 0/5 V 间切换，`V(filt)` 呈连续 RC 响应。完整
证据保存在本机 `C:\Users\18331\Documents\multisim-evidence\hybrid-not-rc-20261003`，
不提交到开源仓库。

下一步才是增加计数器/移位寄存器驱动 RC、二极管整形和受控源接口；每一类都必须先
单独取得 Multisim 原生证据，不能由这条基线外推。
