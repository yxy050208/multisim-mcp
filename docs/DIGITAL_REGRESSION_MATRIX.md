# 复杂数字电路回归矩阵

回归矩阵覆盖从组合逻辑到多级时序链的完整路径。每个案例都应执行：

1. 解析网表和适配器；
2. 生成可编辑 `.ms14`；
3. 用 Multisim 重开并导出 `ReportNetlist`；
4. 检查器件、连接网络、模型端口和布局；
5. 运行瞬态实验；
6. 检查输出波形并保存实验报告。

当前矩阵包含五类案例：

| 案例 | 覆盖边界 | 输出 |
| --- | --- | --- |
| `logic_chain_load` | NOT → AND → OR，带电阻负载 | `dout`, `n2` |
| `dff_load` | `@DFF` 适配器、时钟、复位和负载 | `q`, `qb` |
| `counter4_load` | 四位异步计数器和四路负载 | `q0..q3` |
| `shift4_load` | 四位串入并出移位寄存器和四路负载 | `s0..s3` |
| `counter4_decode_load` | 计数器输出扇出到四个反相器和负载 | `d0..d3` |

本机实测运行需要 32 位 Python、Multisim 和本地模板包：

```powershell
$env:PYTHONPATH = "mcp_server"
$env:MULTISIM_MCP_TEMPLATE_DIR = "C:\Users\18331\AppData\Local\multisim-mcp\component-pack-rc3"
python tools/run_digital_regression.py --output C:\Temp\multisim-digital-regression
```

只运行单个案例：

```powershell
python tools/run_digital_regression.py --case counter4_load --output C:\Temp\counter4-regression
```

为了量化数字专用信号链布局的收益，可以运行受控的通用网格消融。它复用同一网表、
元件包、Multisim 重开、拓扑检查和瞬态仿真，只关闭数字布局 profile；`matrix.json`
会记录 `layout_profile_mode=generic`，因此结果可以和默认 `digital` 模式逐项比较：

```powershell
python tools/run_digital_regression.py --case counter4_load `
  --layout-profile generic --output C:\Temp\counter4-generic-ablation
```

这项消融只用于测量布局交叉率和几何质量，不改变通过条件，也不把通用网格当作生产
布局策略。两次运行都应保留本地证据，不能用消融结果替代 Multisim 原生验收。

在本机 Multisim 14.3、`component-pack-rc3` 上对 `counter4_load` 的一次实测对照为：
默认数字 profile 的交叉率为 `1.176`（40 个不同网络交叉 / 34 条线），通用网格为
`1.324`（45 / 34），数字 profile 降低约 `11.2%`；两次均通过原生重开、拓扑、引脚
证据和瞬态输出检查。该数字是单个案例的实测，不外推为所有复杂电路的固定收益。

运行器会为每个案例保存原理图、仿真数据和报告，并在根目录写入 `matrix.json`。矩阵通过条件包括：

- 生成流程成功；
- 布局状态为 `pass`，交叉率不超过 2.0；
- 重开拓扑状态为 `pass`；
- 所有生成器件出现在 Multisim 原生连接表中；
- 瞬态仿真成功；
- 声明的输出网络有可观察波形。

`matrix.json` 还会单独写出 `pin_evidence`。其中 `fully_verified_cases` 只统计
ReportNetlist 能完整证明引脚到网络映射的案例；数字 XSPICE 模型的隐藏电源脚和
Multisim 省略的悬空输出脚会进入 `partially_verified_cases`。这类结果保持为
`unverified`，不会被转换成 `pass`；出现实际引脚不匹配时会记录到
`mismatch_cases`，并由实验流水线失败。

对于数字器件，`named_pin_counts` 进一步统计可见信号端子的逐引脚结果；当
ReportNetlist 省略 `VDD/VSS` 等隐藏端子时，回归器会读取同一次 Multisim
重开后保存的解码 XML，核对端口的 `CiNode` 网络引用。
例如 `I1/O1`、`J/K/CLK/Q` 等端口可以有 `pass`，而 `VDD/VSS` 或 Multisim
省略的悬空端子会保持 `unverified`。这使回归结果能证明实际信号链已经接对，
同时保留原生报告对隐藏端子的限制。

本地模板和实验产物只用于验证，不应提交到开源仓库。
